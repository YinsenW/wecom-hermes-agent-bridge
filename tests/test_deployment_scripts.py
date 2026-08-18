from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_generate_secrets_creates_valid_protected_file(tmp_path: Path) -> None:
    destination = tmp_path / "generated.env"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate-secrets.py"),
            "--output",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    values = dict(
        line.split("=", 1)
        for line in destination.read_text(encoding="utf-8").splitlines()
    )
    assert len(values["WECOM_CALLBACK_TOKEN"]) == 32
    aes_key = values["WECOM_CALLBACK_AES_KEY"]
    assert len(aes_key) == 43
    assert aes_key.isalnum()
    assert len(base64.b64decode(aes_key + "=")) == 32
    assert len(values["HERMES_API_KEY"]) >= 60
    assert os.stat(destination).st_mode & 0o777 == 0o600


def test_generate_secrets_refuses_to_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "generated.env"
    destination.write_text("keep", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate-secrets.py"),
            "--output",
            str(destination),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert destination.read_text(encoding="utf-8") == "keep"


def test_render_domain_config_replaces_only_domain(tmp_path: Path) -> None:
    template = tmp_path / "template.conf"
    destination = tmp_path / "site.conf"
    template.write_text(
        "server_name wecom-kf.example.com; # $host stays\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/render-domain-config.py"),
            "--template",
            str(template),
            "--domain",
            "WeCom.Example.NET.",
            "--output",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert destination.read_text(encoding="utf-8") == (
        "server_name wecom.example.net; # $host stays\n"
    )


def test_render_domain_config_rejects_url(tmp_path: Path) -> None:
    template = tmp_path / "template.conf"
    template.write_text("wecom-kf.example.com", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/render-domain-config.py"),
            "--template",
            str(template),
            "--domain",
            "https://wecom.example.net/path",
            "--output",
            str(tmp_path / "site.conf"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    "template",
    [
        "deploy/nginx/wecom-http-bootstrap.conf.example",
        "deploy/nginx/wecom-hermes-agent-bridge.conf.example",
        "deploy/nginx/wecom-hermes-agent-bridge-behind-haproxy.conf.example",
        "deploy/haproxy/haproxy-sni-router.cfg.example",
    ],
)
def test_all_domain_templates_render(template: str, tmp_path: Path) -> None:
    destination = tmp_path / Path(template).name
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/render-domain-config.py"),
            "--template",
            str(ROOT / template),
            "--domain",
            "wecom.example.net",
            "--output",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rendered = destination.read_text(encoding="utf-8")
    assert "wecom-kf.example.com" not in rendered
    assert "wecom.example.net" in rendered
