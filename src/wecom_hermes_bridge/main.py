from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import ConfigurationError, Settings
from .crypto import WeComCrypto, WeComCryptoError
from .hermes_api import HermesAPIClient
from .knowledge import MarkdownKnowledgeBase
from .message_processor import CustomerMessageProcessor
from .storage import SQLiteStore
from .sync_worker import MessageSyncWorker
from .wecom_api import WeComAPIClient
from .xml_utils import XMLPayloadError, parse_flat_xml


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    store = SQLiteStore(settings.database_path)
    store.initialize()
    app.state.settings = settings
    app.state.store = store
    app.state.sync_stop_event = asyncio.Event()
    app.state.sync_task = None
    app.state.processor_task = None
    app.state.wecom_client = None
    app.state.hermes_client = None
    app.state.knowledge_base = None

    if settings.knowledge_base_path is not None:
        if not settings.knowledge_base_path.is_file():
            raise ConfigurationError(
                f"Knowledge base file not found: {settings.knowledge_base_path}"
            )
        app.state.knowledge_base = MarkdownKnowledgeBase.from_path(
            settings.knowledge_base_path,
            trigger_terms=settings.knowledge_trigger_terms,
            top_k=settings.knowledge_top_k,
            max_chars=settings.knowledge_max_chars,
        )

    if settings.wecom_api_configured:
        wecom_client = WeComAPIClient(
            corp_id=settings.wecom_corp_id or "",
            app_secret=settings.wecom_app_secret or "",
            api_base=settings.wecom_api_base,
        )
        worker = MessageSyncWorker(store=store, wecom=wecom_client)
        app.state.wecom_client = wecom_client
        app.state.sync_task = asyncio.create_task(worker.run(app.state.sync_stop_event))
        if settings.hermes_configured:
            hermes_client = HermesAPIClient(
                api_base=settings.hermes_api_base,
                api_key=settings.hermes_api_key or "",
                timeout_seconds=settings.hermes_timeout_seconds,
            )
            processor = CustomerMessageProcessor(
                corp_id=settings.wecom_corp_id or "",
                store=store,
                wecom=wecom_client,
                hermes=hermes_client,
                dry_run=settings.dry_run,
                knowledge=app.state.knowledge_base,
            )
            app.state.hermes_client = hermes_client
            app.state.processor_task = asyncio.create_task(
                processor.run(app.state.sync_stop_event)
            )

    try:
        yield
    finally:
        app.state.sync_stop_event.set()
        if app.state.sync_task is not None:
            await app.state.sync_task
        if app.state.processor_task is not None:
            await app.state.processor_task
        if app.state.wecom_client is not None:
            await app.state.wecom_client.close()
        if app.state.hermes_client is not None:
            await app.state.hermes_client.close()


app = FastAPI(
    title="WeCom Hermes Bridge",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(ConfigurationError)
async def configuration_error_handler(
    _request: Request,
    error: ConfigurationError,
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    store: SQLiteStore = request.app.state.store
    return {
        "status": "ok",
        "environment": settings.environment,
        "dry_run": settings.dry_run,
        "wecom_callback_configured": settings.wecom_callback_configured,
        "wecom_api_configured": settings.wecom_api_configured,
        "hermes_configured": settings.hermes_configured,
        "knowledge_configured": request.app.state.knowledge_base is not None,
        "knowledge_chunks": (
            len(request.app.state.knowledge_base.chunks)
            if request.app.state.knowledge_base is not None
            else 0
        ),
        "sync_worker_running": request.app.state.sync_task is not None,
        "message_processor_running": request.app.state.processor_task is not None,
        "pending_callback_triggers": store.pending_trigger_count(),
        "inbound_messages": store.inbound_message_count(),
        "pending_customer_messages": store.pending_customer_message_count(),
    }


def _callback_crypto(settings: Settings) -> WeComCrypto:
    settings.require_wecom_callback()
    return WeComCrypto(
        token=settings.wecom_callback_token or "",
        encoding_aes_key=settings.wecom_callback_aes_key or "",
        receiver_id=settings.wecom_corp_id or "",
    )


@app.get("/callbacks/wecom/kf", response_class=PlainTextResponse)
async def verify_wecom_callback(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> str:
    settings: Settings = request.app.state.settings
    crypto = _callback_crypto(settings)
    try:
        crypto.verify_signature(msg_signature, timestamp, nonce, echostr)
        return crypto.decrypt(echostr)
    except WeComCryptoError as exc:
        raise HTTPException(status_code=403, detail="Invalid callback") from exc


@app.post("/callbacks/wecom/kf", response_class=PlainTextResponse)
async def receive_wecom_callback(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
) -> Response:
    settings: Settings = request.app.state.settings
    store: SQLiteStore = request.app.state.store
    crypto = _callback_crypto(settings)

    try:
        envelope = parse_flat_xml(await request.body())
        encrypted = envelope["Encrypt"]
        crypto.verify_signature(msg_signature, timestamp, nonce, encrypted)
        callback = parse_flat_xml(crypto.decrypt(encrypted))
    except (KeyError, WeComCryptoError, XMLPayloadError) as exc:
        raise HTTPException(status_code=403, detail="Invalid callback") from exc

    if callback.get("Event") != "kf_msg_or_event":
        return PlainTextResponse("success")

    open_kfid = callback.get("OpenKfId", "")
    pull_token = callback.get("Token", "")
    if not open_kfid or not pull_token:
        raise HTTPException(status_code=400, detail="Incomplete customer-service event")
    if settings.wecom_allowed_kf_ids and open_kfid not in settings.wecom_allowed_kf_ids:
        raise HTTPException(status_code=403, detail="Customer-service account is not allowed")

    store.enqueue_callback_trigger(open_kfid, pull_token)
    return PlainTextResponse("success")
