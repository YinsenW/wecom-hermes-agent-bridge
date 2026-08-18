from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from typing import Protocol

from .hermes_api import HermesSession
from .prompts import CUSTOMER_SERVICE_SYSTEM_PROMPT
from .storage import InboundMessage, SQLiteStore
from .wecom_api import ServiceState


HANDOFF_REPLY = "好的，已为您转接人工客服，请稍候。"
HANDOFF_KEYWORDS = ("人工客服", "转人工", "真人客服", "人工服务", "找人工")


class HermesConversationClient(Protocol):
    async def get_or_create_session(
        self,
        *,
        external_session_key: str,
        title: str,
        system_prompt: str,
    ) -> HermesSession: ...

    async def chat(
        self,
        *,
        session: HermesSession,
        external_session_key: str,
        user_message: str,
    ) -> str: ...


class WeComConversationClient(Protocol):
    async def get_service_state(
        self,
        *,
        open_kfid: str,
        external_userid: str,
    ) -> ServiceState: ...

    async def transition_service_state(
        self,
        *,
        open_kfid: str,
        external_userid: str,
        target_state: int,
        servicer_userid: str | None = None,
    ) -> str | None: ...

    async def send_text(
        self,
        *,
        open_kfid: str,
        external_userid: str,
        content: str,
        msgid: str,
    ) -> str: ...

    async def send_event_text(
        self,
        *,
        code: str,
        content: str,
        msgid: str,
    ) -> str: ...


class KnowledgeContextProvider(Protocol):
    def build_context(self, question: str) -> str: ...


class CustomerMessageProcessor:
    def __init__(
        self,
        *,
        corp_id: str,
        store: SQLiteStore,
        wecom: WeComConversationClient,
        hermes: HermesConversationClient,
        dry_run: bool,
        knowledge: KnowledgeContextProvider | None = None,
    ) -> None:
        self._corp_id = corp_id
        self._store = store
        self._wecom = wecom
        self._hermes = hermes
        self._dry_run = dry_run
        self._knowledge = knowledge
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            message = self._store.next_pending_customer_message()
            if message is None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                except TimeoutError:
                    continue
                continue
            try:
                await self.process_message(message)
            except Exception as exc:
                self._store.mark_inbound_error(message.msgid, type(exc).__name__)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=2)
                except TimeoutError:
                    continue

    async def process_message(self, message: InboundMessage) -> None:
        if not message.open_kfid or not message.external_userid:
            self._store.mark_inbound_processed(message.msgid, error="missing_identity")
            return

        lock_key = f"{message.open_kfid}:{message.external_userid}"
        async with self._locks[lock_key]:
            conversation = self._store.get_or_create_conversation(
                open_kfid=message.open_kfid,
                external_userid=message.external_userid,
            )
            if message.msgtype != "text":
                await self._handoff(message, reason="unsupported_message_type")
                return

            user_text = self._extract_text(message)
            if not user_text:
                self._store.mark_inbound_processed(message.msgid, error="empty_text")
                return
            if any(keyword in user_text for keyword in HANDOFF_KEYWORDS):
                await self._handoff(message, reason="customer_requested_human")
                return
            if self._store.outbound_status(outbound_msgid("reply", message.msgid)) in {
                "sent",
                "dry_run",
            }:
                self._store.mark_inbound_processed(message.msgid)
                return

            if not self._dry_run:
                service_state = await self._wecom.get_service_state(
                    open_kfid=message.open_kfid,
                    external_userid=message.external_userid,
                )
                if conversation.human_locked and service_state.state in {0, 1}:
                    conversation = self._store.restart_conversation(
                        open_kfid=message.open_kfid,
                        external_userid=message.external_userid,
                    )
                if service_state.state in {2, 3}:
                    self._store.set_conversation_human_locked(
                        open_kfid=message.open_kfid,
                        external_userid=message.external_userid,
                        locked=True,
                    )
                    self._store.mark_inbound_processed(message.msgid, error="human_service")
                    return
                if service_state.state == 4:
                    self._store.mark_inbound_processed(message.msgid, error="inactive_session")
                    return
                if service_state.state == 0:
                    await self._wecom.transition_service_state(
                        open_kfid=message.open_kfid,
                        external_userid=message.external_userid,
                        target_state=1,
                    )
            elif conversation.human_locked:
                self._store.mark_inbound_processed(message.msgid, error="human_locked")
                return

            external_session_key = (
                f"{self._corp_id}:{message.open_kfid}:"
                f"{message.external_userid}:{conversation.generation}"
            )
            system_prompt = CUSTOMER_SERVICE_SYSTEM_PROMPT
            if self._knowledge is not None:
                knowledge_context = self._knowledge.build_context(user_text)
                if knowledge_context:
                    system_prompt = f"{system_prompt}\n\n{knowledge_context}"
            try:
                session = await self._hermes.get_or_create_session(
                    external_session_key=external_session_key,
                    title=f"微信客服 {message.open_kfid[-8:]}",
                    system_prompt=system_prompt,
                )
                self._store.set_conversation_hermes_session(
                    open_kfid=message.open_kfid,
                    external_userid=message.external_userid,
                    hermes_session_id=session.session_id,
                )
                reply = await self._hermes.chat(
                    session=session,
                    external_session_key=external_session_key,
                    user_message=user_text,
                )
            except Exception:
                await self._handoff(message, reason="hermes_failed")
                return

            await self._send_normal_reply(message, reply)

    async def _send_normal_reply(self, message: InboundMessage, content: str) -> None:
        assert message.open_kfid and message.external_userid
        msgid = outbound_msgid("reply", message.msgid)
        content = truncate_utf8(content, 2048)
        if self._store.outbound_status(msgid) in {"sent", "dry_run"}:
            self._store.mark_inbound_processed(message.msgid)
            return
        if self._dry_run:
            status = "dry_run"
        else:
            self._store.upsert_outbound_message(
                msgid=msgid,
                inbound_msgid=message.msgid,
                open_kfid=message.open_kfid,
                external_userid=message.external_userid,
                content=content,
                status="pending",
            )
            await self._wecom.send_text(
                open_kfid=message.open_kfid,
                external_userid=message.external_userid,
                content=content,
                msgid=msgid,
            )
            status = "sent"
        self._store.upsert_outbound_message(
            msgid=msgid,
            inbound_msgid=message.msgid,
            open_kfid=message.open_kfid,
            external_userid=message.external_userid,
            content=content,
            status=status,
        )
        self._store.mark_inbound_processed(message.msgid)

    async def _handoff(self, message: InboundMessage, *, reason: str) -> None:
        if not message.open_kfid or not message.external_userid:
            self._store.mark_inbound_processed(message.msgid, error=reason)
            return
        msgid = outbound_msgid("handoff", message.msgid)
        status = self._store.outbound_status(msgid)
        if status not in {"sent", "dry_run"}:
            if self._dry_run:
                status = "dry_run"
            else:
                code = await self._wecom.transition_service_state(
                    open_kfid=message.open_kfid,
                    external_userid=message.external_userid,
                    target_state=2,
                )
                if code:
                    await self._wecom.send_event_text(
                        code=code,
                        content=HANDOFF_REPLY,
                        msgid=msgid,
                    )
                status = "sent"
            self._store.upsert_outbound_message(
                msgid=msgid,
                inbound_msgid=message.msgid,
                open_kfid=message.open_kfid,
                external_userid=message.external_userid,
                content=HANDOFF_REPLY,
                status=status,
                error=reason,
            )
        self._store.set_conversation_human_locked(
            open_kfid=message.open_kfid,
            external_userid=message.external_userid,
            locked=True,
        )
        self._store.mark_inbound_processed(message.msgid, error=reason)

    @staticmethod
    def _extract_text(message: InboundMessage) -> str:
        try:
            payload = json.loads(message.payload_json)
            value = payload["text"]["content"]
        except (KeyError, TypeError, ValueError):
            return ""
        return str(value).strip()


def outbound_msgid(kind: str, inbound_msgid: str) -> str:
    digest = hashlib.sha256(f"{kind}:{inbound_msgid}".encode("utf-8")).hexdigest()
    return f"h{digest[:31]}"


def truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    suffix = "…"
    budget = max(0, max_bytes - len(suffix.encode("utf-8")))
    truncated = encoded[:budget]
    while truncated:
        try:
            return truncated.decode("utf-8") + suffix
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return suffix if max_bytes >= len(suffix.encode("utf-8")) else ""
