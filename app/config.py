from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


def _parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise RuntimeError(f"Cannot parse boolean value: {value}")


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required.")
    return value


def _resolve_bot_token() -> str:
    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError("Set MAX_BOT_TOKEN for the MAX bot.")


@dataclass(frozen=True, slots=True)
class Settings:
    google_access_mode: str
    bot_token: str
    report_recipient_label: str
    spreadsheet_url: str
    comments_spreadsheet_url: str | None
    source_sheet_name: str
    pending_status_value: str
    pending_status_aliases: tuple[str, ...]
    approved_status_value: str
    db_path: Path
    google_service_account_file: Path | None
    google_service_account_json: str | None
    apps_script_webapp_url: str | None
    apps_script_secret: str | None
    comments_sheet_name: str | None
    excluded_section_titles: tuple[str, ...]
    docs_cache_ttl_seconds: int
    sheet_cache_ttl_seconds: int
    delivery_mode: str
    webhook_host: str
    webhook_port: int
    webhook_public_url: str | None
    webhook_path: str
    webhook_secret: str | None
    max_ssl_verify: bool
    max_ca_bundle: Path | None
    log_level: str


def load_settings() -> Settings:
    load_dotenv()

    db_path = Path(os.getenv("DB_PATH", "./data/medreview_bot_max.sqlite3")).expanduser()
    if not db_path.is_absolute():
        db_path = (BASE_DIR / db_path).resolve()

    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    service_account_path = Path(service_account_file).expanduser() if service_account_file else None
    google_service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip() or None
    google_access_mode = os.getenv("GOOGLE_ACCESS_MODE", "").strip().lower()
    apps_script_webapp_url = os.getenv("APPS_SCRIPT_WEBAPP_URL", "").strip() or None
    apps_script_secret = os.getenv("APPS_SCRIPT_SECRET", "").strip() or None
    pending_status_value = (
        os.getenv("GOOGLE_PENDING_STATUS_VALUE", "").strip()
        or os.getenv("GOOGLE_STATUS_VALUE", "").strip()
        or "На проверке у врача"
    )
    pending_status_aliases = _parse_csv(os.getenv("GOOGLE_PENDING_STATUS_ALIASES", "Врач проверяет"))
    if pending_status_value not in pending_status_aliases:
        pending_status_aliases = (*pending_status_aliases, pending_status_value)

    if not google_access_mode:
        google_access_mode = "apps_script" if apps_script_webapp_url else "service_account"

    delivery_mode = os.getenv("MAX_DELIVERY_MODE", "polling").strip().lower() or "polling"
    if delivery_mode not in {"polling", "webhook"}:
        raise RuntimeError("MAX_DELIVERY_MODE must be either 'polling' or 'webhook'.")

    webhook_public_url = os.getenv("MAX_WEBHOOK_PUBLIC_URL", "").strip() or None
    webhook_path = os.getenv("MAX_WEBHOOK_PATH", "").strip()
    if not webhook_path:
        webhook_path = urlparse(webhook_public_url).path if webhook_public_url else ""
    webhook_path = webhook_path or "/"
    if not webhook_path.startswith("/"):
        webhook_path = f"/{webhook_path}"

    max_ca_bundle_raw = os.getenv("MAX_CA_BUNDLE", "").strip()
    max_ca_bundle = Path(max_ca_bundle_raw).expanduser() if max_ca_bundle_raw else None

    settings = Settings(
        google_access_mode=google_access_mode,
        bot_token=_resolve_bot_token(),
        report_recipient_label=os.getenv("REPORT_RECIPIENT_LABEL", "аккаунт редактора в MAX").strip() or "аккаунт редактора в MAX",
        spreadsheet_url=_require_env("GOOGLE_SPREADSHEET_URL"),
        comments_spreadsheet_url=os.getenv("COMMENTS_SPREADSHEET_URL", "").strip() or None,
        source_sheet_name=os.getenv("GOOGLE_SOURCE_SHEET_NAME", "Темы МКБ").strip(),
        pending_status_value=pending_status_value,
        pending_status_aliases=pending_status_aliases,
        approved_status_value=os.getenv("GOOGLE_APPROVED_STATUS_VALUE", "Проверено врачом").strip(),
        db_path=db_path,
        google_service_account_file=service_account_path,
        google_service_account_json=google_service_account_json,
        apps_script_webapp_url=apps_script_webapp_url,
        apps_script_secret=apps_script_secret,
        comments_sheet_name=os.getenv("COMMENTS_SHEET_NAME", "").strip() or None,
        excluded_section_titles=_parse_csv(os.getenv("EXCLUDED_SECTION_TITLES")),
        docs_cache_ttl_seconds=int(os.getenv("DOCS_CACHE_TTL_SECONDS", "300")),
        sheet_cache_ttl_seconds=int(os.getenv("SHEET_CACHE_TTL_SECONDS", "120")),
        delivery_mode=delivery_mode,
        webhook_host=os.getenv("MAX_WEBHOOK_HOST", "0.0.0.0").strip() or "0.0.0.0",
        webhook_port=int(os.getenv("MAX_WEBHOOK_PORT", "8080")),
        webhook_public_url=webhook_public_url,
        webhook_path=webhook_path,
        webhook_secret=os.getenv("MAX_WEBHOOK_SECRET", "").strip() or None,
        max_ssl_verify=_parse_bool(os.getenv("MAX_SSL_VERIFY", "true"), default=True),
        max_ca_bundle=max_ca_bundle,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
    )

    if settings.google_access_mode == "apps_script":
        if not settings.apps_script_webapp_url or not settings.apps_script_secret:
            raise RuntimeError(
                "Set APPS_SCRIPT_WEBAPP_URL and APPS_SCRIPT_SECRET for Apps Script access."
            )
        return settings

    if settings.google_access_mode == "service_account":
        if not settings.google_service_account_file and not settings.google_service_account_json:
            raise RuntimeError(
                "Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON to access Google Sheets and Docs."
            )
        return settings

    raise RuntimeError("GOOGLE_ACCESS_MODE must be either 'apps_script' or 'service_account'.")
