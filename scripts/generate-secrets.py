#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import os
import secrets
import string
from pathlib import Path


def _callback_token(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _encoding_aes_key() -> str:
    while True:
        value = base64.b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
        if len(value) == 43 and value.isalnum():
            return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate WeCom callback and Hermes API secrets into a mode-0600 file."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output file.",
    )
    args = parser.parse_args()

    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_TRUNC if args.force else os.O_EXCL
    payload = (
        f"WECOM_CALLBACK_TOKEN={_callback_token()}\n"
        f"WECOM_CALLBACK_AES_KEY={_encoding_aes_key()}\n"
        f"HERMES_API_KEY={secrets.token_urlsafe(48)}\n"
    )
    descriptor = os.open(args.output, flags, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(args.output, 0o600)
    print(f"Generated secrets at {args.output} with mode 0600.")
    print("Do not commit, paste, or include this file in logs.")


if __name__ == "__main__":
    main()
