from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class HermesAPIError(RuntimeError):
    """Raised when the Hermes API Server rejects or fails a request."""


@dataclass(frozen=True, slots=True)
class HermesSession:
    session_id: str
    system_prompt: str = ""


class HermesAPIClient:
    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get(f"{self._api_base}/health")
            response.raise_for_status()
            return response.json().get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False

    async def get_or_create_session(
        self,
        *,
        external_session_key: str,
        title: str,
        system_prompt: str,
    ) -> HermesSession:
        session_id = self.session_id_for(external_session_key)
        unique_title = f"{title[:107]} {session_id[-8:]}".strip()
        response = await self._client.post(
            f"{self._api_base}/api/sessions",
            headers=self._headers(external_session_key),
            json={
                "id": session_id,
                "title": unique_title,
                "system_prompt": system_prompt,
            },
        )
        if response.status_code not in {201, 409}:
            raise self._error("create session", response)
        return HermesSession(session_id=session_id, system_prompt=system_prompt)

    async def chat(
        self,
        *,
        session: HermesSession,
        external_session_key: str,
        user_message: str,
    ) -> str:
        encoded_session_id = quote(session.session_id, safe="")
        response = await self._client.post(
            f"{self._api_base}/api/sessions/{encoded_session_id}/chat",
            headers=self._headers(external_session_key),
            json={
                "input": user_message,
                # Hermes 0.18 Sessions API persists system_prompt metadata on
                # create, but its chat handler applies per-turn instructions.
                "instructions": session.system_prompt,
            },
        )
        if response.status_code != 200:
            raise self._error("chat", response)
        try:
            content = response.json()["message"]["content"]
        except (KeyError, TypeError, ValueError) as exc:
            raise HermesAPIError("Hermes chat returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise HermesAPIError("Hermes chat returned an empty response")
        content = content.strip()
        if self._looks_like_upstream_error(content):
            raise HermesAPIError("Hermes reported an upstream model-provider error")
        if self._looks_like_customer_service_policy_violation(content):
            raise HermesAPIError("Hermes returned a reply that violates customer-service policy")
        return content

    async def end_session(self, session: HermesSession, *, reason: str) -> None:
        encoded_session_id = quote(session.session_id, safe="")
        response = await self._client.patch(
            f"{self._api_base}/api/sessions/{encoded_session_id}",
            headers=self._headers(None),
            json={"end_reason": reason[:120]},
        )
        if response.status_code != 200:
            raise self._error("end session", response)

    @staticmethod
    def session_id_for(external_session_key: str) -> str:
        digest = hashlib.sha256(external_session_key.encode("utf-8")).hexdigest()
        return f"wecom_{digest[:32]}"

    def _headers(self, external_session_key: str | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if external_session_key:
            digest = hashlib.sha256(external_session_key.encode("utf-8")).hexdigest()
            headers["X-Hermes-Session-Key"] = f"wecom-kf:{digest}"
        return headers

    @staticmethod
    def _error(operation: str, response: httpx.Response) -> HermesAPIError:
        detail: Any
        try:
            payload = response.json()
            detail = payload.get("error") or payload.get("detail")
        except ValueError:
            detail = response.text[:300]
        return HermesAPIError(
            f"Hermes {operation} failed with HTTP {response.status_code}: {detail}"
        )

    @staticmethod
    def _looks_like_upstream_error(content: str) -> bool:
        normalized = content.casefold()
        provider_error_markers = (
            "authentication fails",
            "authenticationerror",
            "api key is invalid",
            "invalid api key",
            "insufficient credits",
            "rate limit exceeded",
        )
        has_marker = any(marker in normalized for marker in provider_error_markers)
        looks_like_http_error = normalized.startswith("http 4") or normalized.startswith(
            "http 5"
        )
        return has_marker or looks_like_http_error

    @staticmethod
    def _looks_like_customer_service_policy_violation(content: str) -> bool:
        normalized = content.casefold()
        unsafe_identity_or_capability_markers = (
            "我是 hermes",
            "我叫 hermes",
            "运行命令",
            "操作文件系统",
            "读取你电脑上的文件",
        )
        return any(
            marker in normalized for marker in unsafe_identity_or_capability_markers
        )
