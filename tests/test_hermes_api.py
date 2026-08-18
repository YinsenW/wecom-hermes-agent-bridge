from __future__ import annotations

import httpx
import pytest

from wecom_hermes_bridge.hermes_api import HermesAPIClient, HermesAPIError, HermesSession


@pytest.mark.asyncio
async def test_create_and_chat_use_stable_isolated_session() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/sessions":
            return httpx.Response(
                201,
                json={"object": "hermes.session", "session": {"id": "ignored"}},
            )
        if request.url.path.endswith("/chat"):
            return httpx.Response(
                200,
                json={
                    "object": "hermes.session.chat.completion",
                    "message": {"role": "assistant", "content": "  您好，有什么可以帮您？  "},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HermesAPIClient(
            api_base="http://hermes:8642",
            api_key="test-api-key",
            timeout_seconds=5,
            client=http_client,
        )
        session = await client.get_or_create_session(
            external_session_key="corp:kf:user:1",
            title="customer session",
            system_prompt="safe prompt",
        )
        reply = await client.chat(
            session=session,
            external_session_key="corp:kf:user:1",
            user_message="你好",
        )

    assert session.session_id == HermesAPIClient.session_id_for("corp:kf:user:1")
    assert reply == "您好，有什么可以帮您？"
    assert requests[0].headers["authorization"] == "Bearer test-api-key"
    assert requests[0].headers["x-hermes-session-key"].startswith("wecom-kf:")
    assert "corp:kf:user:1" not in requests[0].headers["x-hermes-session-key"]
    assert requests[1].read().decode("utf-8").find('"instructions":"safe prompt"') >= 0
    assert session.session_id[-8:] in requests[0].read().decode("utf-8")


@pytest.mark.asyncio
async def test_existing_session_is_reused_after_conflict() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(409, json={}))
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HermesAPIClient(
            api_base="http://hermes:8642",
            api_key="test-api-key",
            timeout_seconds=5,
            client=http_client,
        )
        session = await client.get_or_create_session(
            external_session_key="same-session",
            title="existing",
            system_prompt="safe prompt",
        )

    assert session.session_id == HermesAPIClient.session_id_for("same-session")


@pytest.mark.asyncio
async def test_provider_authentication_error_is_not_returned_as_customer_reply() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "HTTP 401: Authentication Fails, api key is invalid",
                }
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HermesAPIClient(
            api_base="http://hermes:8642",
            api_key="test-api-key",
            timeout_seconds=5,
            client=http_client,
        )
        with pytest.raises(HermesAPIError, match="model-provider error"):
            await client.chat(
                session=HermesSession("session-1"),
                external_session_key="session-key",
                user_message="你好",
            )


@pytest.mark.asyncio
async def test_generic_hermes_capability_claim_is_rejected() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "我是 Hermes，可以读取你电脑上的文件并运行命令。",
                }
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HermesAPIClient(
            api_base="http://hermes:8642",
            api_key="test-api-key",
            timeout_seconds=5,
            client=http_client,
        )
        with pytest.raises(HermesAPIError, match="customer-service policy"):
            await client.chat(
                session=HermesSession("session-1", "customer-service prompt"),
                external_session_key="session-key",
                user_message="你是谁？",
            )
