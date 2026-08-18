from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


class WeComAPIError(RuntimeError):
    def __init__(self, operation: str, errcode: int, errmsg: str) -> None:
        super().__init__(f"{operation} failed ({errcode}): {errmsg}")
        self.operation = operation
        self.errcode = errcode
        self.errmsg = errmsg


@dataclass(frozen=True, slots=True)
class SyncPage:
    messages: list[dict[str, Any]]
    next_cursor: str
    has_more: bool


@dataclass(frozen=True, slots=True)
class ServiceState:
    state: int
    servicer_userid: str | None = None


class WeComAPIClient:
    def __init__(
        self,
        *,
        corp_id: str,
        app_secret: str,
        api_base: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._corp_id = corp_id
        self._app_secret = app_secret
        self._api_base = api_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=20)
        self._owns_client = client is None
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def sync_messages(
        self,
        *,
        open_kfid: str,
        cursor: str | None,
        pull_token: str | None,
    ) -> SyncPage:
        payload: dict[str, Any] = {
            "limit": 1000,
            "voice_format": 0,
            "open_kfid": open_kfid,
        }
        if cursor:
            payload["cursor"] = cursor
        if pull_token:
            payload["token"] = pull_token
        result = await self._post("/cgi-bin/kf/sync_msg", payload, "sync_msg")
        return SyncPage(
            messages=list(result.get("msg_list") or []),
            next_cursor=str(result.get("next_cursor") or cursor or ""),
            has_more=bool(result.get("has_more")),
        )

    async def get_service_state(
        self,
        *,
        open_kfid: str,
        external_userid: str,
    ) -> ServiceState:
        result = await self._post(
            "/cgi-bin/kf/service_state/get",
            {"open_kfid": open_kfid, "external_userid": external_userid},
            "get service state",
        )
        return ServiceState(
            state=int(result.get("service_state", 4)),
            servicer_userid=result.get("servicer_userid") or None,
        )

    async def transition_service_state(
        self,
        *,
        open_kfid: str,
        external_userid: str,
        target_state: int,
        servicer_userid: str | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {
            "open_kfid": open_kfid,
            "external_userid": external_userid,
            "service_state": target_state,
        }
        if servicer_userid:
            payload["servicer_userid"] = servicer_userid
        result = await self._post(
            "/cgi-bin/kf/service_state/trans",
            payload,
            "transition service state",
        )
        return result.get("msg_code") or None

    async def send_text(
        self,
        *,
        open_kfid: str,
        external_userid: str,
        content: str,
        msgid: str,
    ) -> str:
        result = await self._post(
            "/cgi-bin/kf/send_msg",
            {
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgid": msgid,
                "msgtype": "text",
                "text": {"content": content},
            },
            "send text",
        )
        return str(result.get("msgid") or msgid)

    async def send_event_text(
        self,
        *,
        code: str,
        content: str,
        msgid: str,
    ) -> str:
        result = await self._post(
            "/cgi-bin/kf/send_msg_on_event",
            {
                "code": code,
                "msgid": msgid,
                "msgtype": "text",
                "text": {"content": content},
            },
            "send event text",
        )
        return str(result.get("msgid") or msgid)

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        access_token = await self._get_access_token()
        response = await self._client.post(
            f"{self._api_base}{path}",
            params={"access_token": access_token},
            json=payload,
        )
        response.raise_for_status()
        result = response.json()
        errcode = int(result.get("errcode", -1))
        if errcode:
            if errcode in {40014, 42001}:
                self._access_token = None
                self._access_token_expires_at = 0
            raise WeComAPIError(operation, errcode, str(result.get("errmsg", "unknown")))
        return result

    async def _get_access_token(self) -> str:
        if self._access_token and time.monotonic() < self._access_token_expires_at:
            return self._access_token
        async with self._token_lock:
            if self._access_token and time.monotonic() < self._access_token_expires_at:
                return self._access_token
            response = await self._client.get(
                f"{self._api_base}/cgi-bin/gettoken",
                params={"corpid": self._corp_id, "corpsecret": self._app_secret},
            )
            response.raise_for_status()
            result = response.json()
            errcode = int(result.get("errcode", -1))
            if errcode:
                raise WeComAPIError(
                    "gettoken",
                    errcode,
                    str(result.get("errmsg", "unknown")),
                )
            access_token = str(result.get("access_token") or "")
            if not access_token:
                raise WeComAPIError("gettoken", -1, "missing access_token")
            expires_in = max(60, int(result.get("expires_in", 7200)))
            self._access_token = access_token
            self._access_token_expires_at = time.monotonic() + max(30, expires_in - 300)
            return access_token
