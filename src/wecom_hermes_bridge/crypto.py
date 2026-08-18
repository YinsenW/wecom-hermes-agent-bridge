from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WeComCryptoError(ValueError):
    """Raised when a WeCom callback cannot be authenticated or decrypted."""


class WeComCrypto:
    """Verify and decrypt encrypted WeCom callback payloads."""

    def __init__(self, token: str, encoding_aes_key: str, receiver_id: str) -> None:
        if not token:
            raise WeComCryptoError("Callback token must not be empty")
        if not receiver_id:
            raise WeComCryptoError("Receiver ID must not be empty")

        padded_key = encoding_aes_key + "=" * (-len(encoding_aes_key) % 4)
        try:
            aes_key = base64.b64decode(padded_key, validate=True)
        except ValueError as exc:
            raise WeComCryptoError("Invalid EncodingAESKey") from exc
        if len(aes_key) != 32:
            raise WeComCryptoError("EncodingAESKey must decode to 32 bytes")

        self._token = token
        self._aes_key = aes_key
        self._receiver_id = receiver_id.encode("utf-8")

    def signature(self, timestamp: str, nonce: str, encrypted: str) -> str:
        parts = sorted((self._token, timestamp, nonce, encrypted))
        return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()

    def verify_signature(
        self,
        signature: str,
        timestamp: str,
        nonce: str,
        encrypted: str,
    ) -> None:
        expected = self.signature(timestamp, nonce, encrypted)
        if not hmac.compare_digest(signature, expected):
            raise WeComCryptoError("Invalid callback signature")

    def decrypt(self, encrypted: str) -> str:
        try:
            ciphertext = base64.b64decode(encrypted, validate=True)
        except ValueError as exc:
            raise WeComCryptoError("Encrypted payload is not valid base64") from exc
        if not ciphertext or len(ciphertext) % 16:
            raise WeComCryptoError("Encrypted payload has an invalid length")

        decryptor = Cipher(
            algorithms.AES(self._aes_key),
            modes.CBC(self._aes_key[:16]),
        ).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        plaintext = self._remove_wecom_padding(padded)

        if len(plaintext) < 20:
            raise WeComCryptoError("Decrypted payload is too short")
        message_length = struct.unpack("!I", plaintext[16:20])[0]
        message_end = 20 + message_length
        if message_end > len(plaintext):
            raise WeComCryptoError("Decrypted payload length is inconsistent")

        message = plaintext[20:message_end]
        receiver_id = plaintext[message_end:]
        if receiver_id != self._receiver_id:
            raise WeComCryptoError("Callback receiver ID does not match")
        try:
            return message.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WeComCryptoError("Decrypted message is not UTF-8") from exc

    @staticmethod
    def _remove_wecom_padding(value: bytes) -> bytes:
        if not value:
            raise WeComCryptoError("Missing callback padding")
        padding_length = value[-1]
        if padding_length < 1 or padding_length > 32:
            raise WeComCryptoError("Invalid callback padding")
        if value[-padding_length:] != bytes([padding_length]) * padding_length:
            raise WeComCryptoError("Invalid callback padding bytes")
        return value[:-padding_length]

    def encrypt_for_test(self, message: str, *, random_prefix: bytes | None = None) -> str:
        """Create a protocol-compatible ciphertext for local tests only."""
        prefix = random_prefix or os.urandom(16)
        if len(prefix) != 16:
            raise WeComCryptoError("Random prefix must contain 16 bytes")
        message_bytes = message.encode("utf-8")
        plaintext = (
            prefix
            + struct.pack("!I", len(message_bytes))
            + message_bytes
            + self._receiver_id
        )
        padding_length = 32 - (len(plaintext) % 32)
        padded = plaintext + bytes([padding_length]) * padding_length
        encryptor = Cipher(
            algorithms.AES(self._aes_key),
            modes.CBC(self._aes_key[:16]),
        ).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode("ascii")
