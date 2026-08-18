#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _request_json(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"User-Agent": "wecom-hermes-agent-bridge"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _require_success(payload: dict[str, object], operation: str) -> None:
    errcode = int(payload.get("errcode", -1))
    if errcode != 0:
        raise SystemExit(
            f"{operation} failed: errcode={errcode}, errmsg={payload.get('errmsg', 'unknown')}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List WeCom Customer Service accounts without printing credentials."
    )
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()

    values = _read_env(args.env_file)
    corp_id = values.get("WECOM_CORP_ID", "")
    app_secret = values.get("WECOM_APP_SECRET", "")
    api_base = values.get("WECOM_API_BASE", "https://qyapi.weixin.qq.com").rstrip("/")
    if not corp_id or not app_secret or corp_id.startswith("ww_your_") or app_secret.startswith("replace_"):
        raise SystemExit("WECOM_CORP_ID and WECOM_APP_SECRET must contain real values.")

    token_query = urllib.parse.urlencode({"corpid": corp_id, "corpsecret": app_secret})
    token_payload = _request_json(f"{api_base}/cgi-bin/gettoken?{token_query}")
    _require_success(token_payload, "gettoken")
    access_token = str(token_payload.get("access_token") or "")
    if not access_token:
        raise SystemExit("gettoken returned no access_token")

    account_query = urllib.parse.urlencode({"access_token": access_token})
    account_payload = _request_json(f"{api_base}/cgi-bin/kf/account/list?{account_query}")
    _require_success(account_payload, "kf/account/list")
    accounts = account_payload.get("account_list") or []
    safe_accounts = [
        {
            "open_kfid": account.get("open_kfid"),
            "name": account.get("name"),
        }
        for account in accounts
        if isinstance(account, dict)
    ]
    print(json.dumps(safe_accounts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
