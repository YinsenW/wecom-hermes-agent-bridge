from __future__ import annotations

import base64

import pytest

from wecom_hermes_bridge.crypto import WeComCrypto, WeComCryptoError


TOKEN = "callback-token"
CORP_ID = "ww1234567890"
AES_KEY = base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")


def test_encrypt_decrypt_and_verify_signature_round_trip() -> None:
    crypto = WeComCrypto(TOKEN, AES_KEY, CORP_ID)
    message = "<xml><Event>kf_msg_or_event</Event></xml>"
    encrypted = crypto.encrypt_for_test(message, random_prefix=b"0123456789abcdef")
    signature = crypto.signature("123456", "nonce", encrypted)

    crypto.verify_signature(signature, "123456", "nonce", encrypted)
    assert crypto.decrypt(encrypted) == message


def test_rejects_invalid_signature() -> None:
    crypto = WeComCrypto(TOKEN, AES_KEY, CORP_ID)
    encrypted = crypto.encrypt_for_test("<xml />", random_prefix=b"0123456789abcdef")

    with pytest.raises(WeComCryptoError, match="signature"):
        crypto.verify_signature("not-valid", "123456", "nonce", encrypted)


def test_rejects_wrong_receiver_id() -> None:
    producer = WeComCrypto(TOKEN, AES_KEY, CORP_ID)
    consumer = WeComCrypto(TOKEN, AES_KEY, "ww-other")
    encrypted = producer.encrypt_for_test("<xml />", random_prefix=b"0123456789abcdef")

    with pytest.raises(WeComCryptoError, match="receiver"):
        consumer.decrypt(encrypted)
