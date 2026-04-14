from __future__ import annotations

import json
import time

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app.config import Settings
from app.doc_parser import parse_google_document
from app.models import ArticleDocument, ArticleTask, CommentRecord, Illustration, Section, normalize_surname


SCOPES = (
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
)


def extract_google_file_id(url: str) -> str:
    marker = "/d/"
    if marker not in url:
        raise ValueError(f"Unsupported Google URL: {url}")
    return url.split(marker, maxsplit=1)[1].split("/", maxsplit=1)[0]


def quote_sheet_name(sheet_name: str) -> str:
    escaped = sheet_name.replace("'", "''")
    return f"'{escaped}'"


class ExpiringValue:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.value = None
        self.loaded_at = 0.0

    def get(self):
        if self.value is None:
            return None
        if time.monotonic() - self.loaded_at > self.ttl_seconds:
            return None
        return self.value

    def set(self, value) -> None:
        self.value = value
        self.loaded_at = time.monotonic()


class GoogleRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._spreadsheet_id = extract_google_file_id(settings.spreadsheet_url)
        self._comments_spreadsheet_id = (
            extract_google_file_id(settings.comments_spreadsheet_url)
            if settings.comments_spreadsheet_url
            else self._spreadsheet_id
        )
        self._mode = settings.google_access_mode
        self._sheets_service = None
        self._docs_service = None
        if self._mode == "service_account":
            credentials = self._build_credentials(settings)
            self._sheets_service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
            self._docs_service = build("docs", "v1", credentials=credentials, cache_discovery=False)
        self._tasks_cache = ExpiringValue(settings.sheet_cache_ttl_seconds)
        self._comments_sheet_ready = False
        self._document_cache: dict[str, ExpiringValue] = {}

    @staticmethod
    def _build_credentials(settings: Settings) -> Credentials:
        if settings.google_service_account_json:
            info = json.loads(settings.google_service_account_json)
            return Credentials.from_service_account_info(info, scopes=SCOPES)
        assert settings.google_service_account_file is not None
        return Credentials.from_service_account_file(settings.google_service_account_file, scopes=SCOPES)

    @staticmethod
    def _build_illustration(payload: dict, fallback_name: str) -> Illustration | None:
        content_base64 = str(payload.get("contentBase64", "")).strip()
        if not content_base64:
            return None
        mime_type = str(payload.get("mimeType", "")).strip() or "image/jpeg"
        filename = str(payload.get("filename", "")).strip() or fallback_name
        return Illustration(
            content_base64=content_base64,
            mime_type=mime_type,
            filename=filename,
            alt_title=str(payload.get("altTitle", "")).strip(),
            alt_description=str(payload.get("altDescription", "")).strip(),
        )

    @staticmethod
    def _cell(row: list[str], index: int) -> str:
        return row[index].strip() if index < len(row) and row[index] else ""

    def _get_all_pending_tasks(self) -> list[ArticleTask]:
        cached = self._tasks_cache.get()
        if cached is not None:
            return cached

        pending_statuses = {
            self._settings.pending_status_value,
            *self._settings.pending_status_aliases,
        }

        if self._mode == "apps_script":
            data = self._post_webapp(
                {
                    "action": "getPendingTasks",
                    "statusValues": list(pending_statuses),
                }
            )
            tasks = [
                ArticleTask(
                    row_number=int(item["rowNumber"]),
                    article_id=str(item.get("articleId", "")),
                    direction=str(item.get("direction", "")),
                    topic=str(item.get("topic", "")),
                    status=str(item.get("status", "")),
                    author=str(item.get("author", "")),
                    due_date=str(item.get("dueDate", "")),
                    document_url=str(item.get("documentUrl", "")),
                    site_url=str(item.get("siteUrl", "")),
                    doctor_name=str(item.get("doctorName", "")),
                    priority=str(item.get("priority", "")),
                )
                for item in data.get("tasks", [])
            ]
            self._tasks_cache.set(tasks)
            return tasks

        sheet_range = f"{quote_sheet_name(self._settings.source_sheet_name)}!A2:N"
        response = (
            self._sheets_service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self._spreadsheet_id,
                range=sheet_range,
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
        rows = response.get("values", [])
        tasks: list[ArticleTask] = []

        for offset, row in enumerate(rows, start=2):
            status = self._cell(row, 3)
            doctor_name = self._cell(row, 9)
            document_url = self._cell(row, 7)

            if status not in pending_statuses:
                continue
            if not doctor_name or not document_url:
                continue

            tasks.append(
                ArticleTask(
                    row_number=offset,
                    article_id=self._cell(row, 0),
                    direction=self._cell(row, 1),
                    topic=self._cell(row, 2),
                    status=status,
                    author=self._cell(row, 4),
                    due_date=self._cell(row, 5),
                    document_url=document_url,
                    site_url=self._cell(row, 8),
                    doctor_name=doctor_name,
                    priority=self._cell(row, 13),
                )
            )

        self._tasks_cache.set(tasks)
        return tasks

    def get_doctor_choices(self, surname: str) -> list[str]:
        normalized = normalize_surname(surname)
        names = {
            task.doctor_name
            for task in self._get_all_pending_tasks()
            if task.doctor_surname == normalized
        }
        return sorted(names)

    def get_tasks_for_doctor(self, doctor_name: str) -> list[ArticleTask]:
        return [task for task in self._get_all_pending_tasks() if task.doctor_name == doctor_name]

    def get_task_by_row(self, doctor_name: str, row_number: int) -> ArticleTask | None:
        for task in self.get_tasks_for_doctor(doctor_name):
            if task.row_number == row_number:
                return task
        return None

    def get_document(self, document_url: str) -> ArticleDocument:
        doc_id = extract_google_file_id(document_url)
        cached = self._document_cache.get(doc_id)
        if cached:
            document = cached.get()
            if document is not None:
                return document

        if self._mode == "apps_script":
            data = self._post_webapp(
                {
                    "action": "getDocumentStructure",
                    "documentUrl": document_url,
                    "excludedTitles": list(self._settings.excluded_section_titles),
                }
            )
            document_data = data.get("document", {})
            parsed = ArticleDocument(
                doc_id=str(document_data.get("docId", doc_id)),
                title=str(document_data.get("title", "Без названия")),
                intro=str(document_data.get("intro", "")),
                intro_illustrations=tuple(
                    illustration
                    for index, item in enumerate(document_data.get("introIllustrations", []), start=1)
                    for illustration in [self._build_illustration(item, f"intro-illustration-{index}.jpg")]
                    if illustration is not None
                ),
                document_url=document_url,
                sections=[
                    Section(
                        index=int(section.get("index", index + 1)),
                        title=str(section.get("title", f"Раздел {index + 1}")),
                        body=str(section.get("body", "")),
                        illustrations=tuple(
                            illustration
                            for image_index, image in enumerate(section.get("illustrations", []), start=1)
                            for illustration in [
                                self._build_illustration(
                                    image,
                                    f"section-{index + 1}-illustration-{image_index}.jpg",
                                )
                            ]
                            if illustration is not None
                        ),
                    )
                    for index, section in enumerate(document_data.get("sections", []))
                ],
            )
            cache_entry = self._document_cache.setdefault(doc_id, ExpiringValue(self._settings.docs_cache_ttl_seconds))
            cache_entry.set(parsed)
            return parsed

        try:
            raw_document = (
                self._docs_service.documents()
                .get(documentId=doc_id, includeTabsContent=True)
                .execute()
            )
        except TypeError:
            raw_document = self._docs_service.documents().get(documentId=doc_id).execute()

        parsed = parse_google_document(
            raw_document,
            document_url,
            excluded_titles=self._settings.excluded_section_titles,
        )
        cache_entry = self._document_cache.setdefault(doc_id, ExpiringValue(self._settings.docs_cache_ttl_seconds))
        cache_entry.set(parsed)
        return parsed

    def append_comment(self, comment: CommentRecord) -> bool:
        sheet_name = self._settings.comments_sheet_name
        if not sheet_name:
            return False

        external_user_id = f"max:{comment.max_user_id}"

        if self._mode == "apps_script":
            self._post_webapp(
                {
                    "action": "appendComment",
                    "comment": {
                        "createdAt": comment.created_at,
                        "doctorName": comment.doctor_name,
                        "articleTitle": comment.article_title,
                        "sectionTitle": comment.section_title,
                        "quoteText": comment.quote_text or "",
                        "commentText": comment.comment_text,
                        "documentUrl": comment.document_url,
                        "sheetRowNumber": comment.sheet_row_number,
                        "articleId": comment.article_id,
                        "telegramUserId": external_user_id,
                    },
                }
            )
            return True

        self._ensure_comments_sheet(sheet_name)
        row = [
            comment.created_at,
            comment.doctor_name,
            comment.article_title,
            comment.section_title,
            comment.quote_text or "",
            comment.comment_text,
            comment.document_url,
            str(comment.sheet_row_number),
            comment.article_id,
            external_user_id,
        ]

        (
            self._sheets_service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self._comments_spreadsheet_id,
                range=f"{quote_sheet_name(sheet_name)}!A:J",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            )
            .execute()
        )
        return True

    def _ensure_comments_sheet(self, sheet_name: str) -> None:
        if self._comments_sheet_ready:
            return

        spreadsheet = (
            self._sheets_service.spreadsheets()
            .get(spreadsheetId=self._comments_spreadsheet_id)
            .execute()
        )
        existing_titles = {
            sheet["properties"]["title"]
            for sheet in spreadsheet.get("sheets", [])
        }

        if sheet_name not in existing_titles:
            (
                self._sheets_service.spreadsheets()
                .batchUpdate(
                    spreadsheetId=self._comments_spreadsheet_id,
                    body={
                        "requests": [
                            {
                                "addSheet": {
                                    "properties": {
                                        "title": sheet_name,
                                    }
                                }
                            }
                        ]
                    },
                )
                .execute()
            )

        (
            self._sheets_service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self._comments_spreadsheet_id,
                range=f"{quote_sheet_name(sheet_name)}!A1:J1",
                valueInputOption="RAW",
                body={
                    "values": [
                        [
                            "Создано",
                            "Врач",
                            "Статья",
                            "Раздел",
                            "Цитата",
                            "Комментарий",
                            "Документ",
                            "Строка таблицы",
                            "ID статьи",
                            "Messenger user id",
                        ]
                    ]
                },
            )
            .execute()
        )

        self._comments_sheet_ready = True

    def update_article_status(self, row_number: int, new_status: str) -> None:
        self._tasks_cache.set(None)

        if self._mode == "apps_script":
            self._post_webapp(
                {
                    "action": "updateArticleStatus",
                    "rowNumber": row_number,
                    "newStatus": new_status,
                }
            )
            return

        self._sheets_service.spreadsheets().values().update(
            spreadsheetId=self._spreadsheet_id,
            range=f"{quote_sheet_name(self._settings.source_sheet_name)}!D{row_number}",
            valueInputOption="RAW",
            body={"values": [[new_status]]},
        ).execute()

    def _post_webapp(self, payload: dict) -> dict:
        if not self._settings.apps_script_webapp_url or not self._settings.apps_script_secret:
            raise RuntimeError("Apps Script URL or secret is not configured.")

        body = {
            "secret": self._settings.apps_script_secret,
            "spreadsheetUrl": self._settings.spreadsheet_url,
            "commentsSpreadsheetUrl": self._settings.comments_spreadsheet_url or self._settings.spreadsheet_url,
            "sourceSheetName": self._settings.source_sheet_name,
            "statusValue": self._settings.pending_status_value,
            "statusValues": [self._settings.pending_status_value, *self._settings.pending_status_aliases],
            "commentsSheetName": self._settings.comments_sheet_name or "",
            **payload,
        }
        response = requests.post(self._settings.apps_script_webapp_url, json=body, timeout=30)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(str(data.get("error", "Apps Script request failed.")))
        return data
