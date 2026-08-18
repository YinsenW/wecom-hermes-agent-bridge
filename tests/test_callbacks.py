from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from wecom_hermes_bridge.crypto import WeComCrypto
from wecom_hermes_bridge.main import app


TOKEN = "callback-token"
CORP_ID = "ww1234567890"
AES_KEY = base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")


def _set_callback_env(monkeypatch, tmp_path) -> WeComCrypto:
    monkeypatch.setenv("WECOM_CORP_ID", CORP_ID)
    monkeypatch.setenv("WECOM_CALLBACK_TOKEN", TOKEN)
    monkeypatch.setenv("WECOM_CALLBACK_AES_KEY", AES_KEY)
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "bridge.db"))
    return WeComCrypto(TOKEN, AES_KEY, CORP_ID)


def test_get_callback_verification(monkeypatch, tmp_path) -> None:
    crypto = _set_callback_env(monkeypatch, tmp_path)
    encrypted = crypto.encrypt_for_test("verified", random_prefix=b"0123456789abcdef")
    params = {
        "msg_signature": crypto.signature("123", "abc", encrypted),
        "timestamp": "123",
        "nonce": "abc",
        "echostr": encrypted,
    }

    with TestClient(app) as client:
        response = client.get("/callbacks/wecom/kf", params=params)

    assert response.status_code == 200
    assert response.text == "verified"


def test_post_callback_is_decrypted_and_queued(monkeypatch, tmp_path) -> None:
    crypto = _set_callback_env(monkeypatch, tmp_path)
    callback_xml = """
    <xml>
      <ToUserName>ww1234567890</ToUserName>
      <MsgType>event</MsgType>
      <Event>kf_msg_or_event</Event>
      <Token>temporary-pull-token</Token>
      <OpenKfId>wk-test-account</OpenKfId>
    </xml>
    """.strip()
    encrypted = crypto.encrypt_for_test(callback_xml, random_prefix=b"0123456789abcdef")
    envelope = f"<xml><Encrypt>{encrypted}</Encrypt></xml>"
    params = {
        "msg_signature": crypto.signature("123", "abc", encrypted),
        "timestamp": "123",
        "nonce": "abc",
    }

    with TestClient(app) as client:
        response = client.post("/callbacks/wecom/kf", params=params, content=envelope)
        health = client.get("/health")

    assert response.status_code == 200
    assert response.text == "success"
    assert health.json()["pending_callback_triggers"] == 1
