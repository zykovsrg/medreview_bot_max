from __future__ import annotations

import json
from dataclasses import asdict

import requests

from app.config import load_settings


def _check_apps_script(settings) -> tuple[bool, str]:
    if not settings.apps_script_webapp_url or not settings.apps_script_secret:
        return False, "APPS_SCRIPT_WEBAPP_URL или APPS_SCRIPT_SECRET не заполнены."

    payload = {
        "secret": settings.apps_script_secret,
        "action": "getPendingTasks",
        "spreadsheetUrl": settings.spreadsheet_url,
        "commentsSpreadsheetUrl": settings.comments_spreadsheet_url or settings.spreadsheet_url,
        "sourceSheetName": settings.source_sheet_name,
        "statusValue": settings.pending_status_value,
        "statusValues": [settings.pending_status_value, *settings.pending_status_aliases],
        "commentsSheetName": settings.comments_sheet_name or "",
    }
    try:
        response = requests.post(settings.apps_script_webapp_url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return False, f"Не удалось достучаться до Apps Script: {exc}"

    if not data.get("ok"):
        return False, f"Apps Script ответил ошибкой: {data.get('error', 'unknown error')}"

    tasks = data.get("tasks", [])
    return True, f"Apps Script доступен, найдено статей в ответе: {len(tasks)}"


def main() -> None:
    settings = load_settings()
    redacted = asdict(settings)
    if redacted.get("bot_token"):
        redacted["bot_token"] = "***hidden***"
    if redacted.get("apps_script_secret"):
        redacted["apps_script_secret"] = "***hidden***"
    if redacted.get("google_service_account_json"):
        redacted["google_service_account_json"] = "***hidden***"

    print("Проверка конфигурации MAX-бота")
    print(json.dumps(redacted, ensure_ascii=False, indent=2, default=str))
    print()

    if settings.google_access_mode == "apps_script":
        ok, message = _check_apps_script(settings)
        print(message)
        if not ok:
            raise SystemExit(1)
    else:
        print("Режим service_account выбран. Сетевая проверка Apps Script пропущена.")

    print("Базовая проверка завершена.")


if __name__ == "__main__":
    main()
