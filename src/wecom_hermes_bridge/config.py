from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Raised when a feature is used without its required configuration."""


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean value: {value!r}")


def _as_csv_set(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _as_csv_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    host: str
    port: int
    database_path: Path
    dry_run: bool
    wecom_corp_id: str | None
    wecom_app_secret: str | None
    wecom_callback_token: str | None
    wecom_callback_aes_key: str | None
    wecom_api_base: str
    wecom_allowed_kf_ids: frozenset[str]
    hermes_api_base: str
    hermes_api_key: str | None
    hermes_timeout_seconds: float
    knowledge_base_path: Path | None
    knowledge_trigger_terms: tuple[str, ...]
    knowledge_top_k: int
    knowledge_max_chars: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            environment=os.getenv("BRIDGE_ENV", "development"),
            host=os.getenv("BRIDGE_HOST", "127.0.0.1"),
            port=int(os.getenv("BRIDGE_PORT", "8080")),
            database_path=Path(os.getenv("BRIDGE_DB_PATH", "data/bridge.db")),
            dry_run=_as_bool(os.getenv("BRIDGE_DRY_RUN"), default=True),
            wecom_corp_id=os.getenv("WECOM_CORP_ID") or None,
            wecom_app_secret=os.getenv("WECOM_APP_SECRET") or None,
            wecom_callback_token=os.getenv("WECOM_CALLBACK_TOKEN") or None,
            wecom_callback_aes_key=os.getenv("WECOM_CALLBACK_AES_KEY") or None,
            wecom_api_base=os.getenv("WECOM_API_BASE", "https://qyapi.weixin.qq.com").rstrip("/"),
            wecom_allowed_kf_ids=_as_csv_set(os.getenv("WECOM_ALLOWED_KF_IDS")),
            hermes_api_base=os.getenv("HERMES_API_BASE", "http://127.0.0.1:8642").rstrip("/"),
            hermes_api_key=os.getenv("HERMES_API_KEY") or None,
            hermes_timeout_seconds=float(os.getenv("HERMES_TIMEOUT_SECONDS", "25")),
            knowledge_base_path=(
                Path(value)
                if (value := os.getenv("KNOWLEDGE_BASE_PATH", "").strip())
                else None
            ),
            knowledge_trigger_terms=_as_csv_tuple(
                os.getenv("KNOWLEDGE_TRIGGER_TERMS")
            ),
            knowledge_top_k=int(os.getenv("KNOWLEDGE_TOP_K", "4")),
            knowledge_max_chars=int(os.getenv("KNOWLEDGE_MAX_CHARS", "6000")),
        )

    @property
    def wecom_callback_configured(self) -> bool:
        return bool(
            self.wecom_corp_id
            and self.wecom_callback_token
            and self.wecom_callback_aes_key
        )

    @property
    def hermes_configured(self) -> bool:
        return bool(self.hermes_api_base and self.hermes_api_key)

    @property
    def wecom_api_configured(self) -> bool:
        return bool(self.wecom_corp_id and self.wecom_app_secret)

    def require_wecom_callback(self) -> None:
        if not self.wecom_callback_configured:
            raise ConfigurationError(
                "WECOM_CORP_ID, WECOM_CALLBACK_TOKEN and "
                "WECOM_CALLBACK_AES_KEY are required"
            )

    def require_wecom_api(self) -> None:
        if not self.wecom_api_configured:
            raise ConfigurationError("WECOM_CORP_ID and WECOM_APP_SECRET are required")
