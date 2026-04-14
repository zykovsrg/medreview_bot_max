from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.models import CommentRecord, CompletedReview, ReminderRecord, ReportChat, ReviewSession, StoredDoctor


class Storage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS doctors (
                    max_user_id INTEGER PRIMARY KEY,
                    surname TEXT NOT NULL,
                    doctor_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_sessions (
                    max_user_id INTEGER PRIMARY KEY,
                    sheet_row_number INTEGER NOT NULL,
                    article_id TEXT NOT NULL,
                    article_title TEXT NOT NULL,
                    document_url TEXT NOT NULL,
                    current_section_index INTEGER NOT NULL DEFAULT 0,
                    review_started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    max_user_id INTEGER NOT NULL,
                    doctor_name TEXT NOT NULL,
                    sheet_row_number INTEGER NOT NULL,
                    article_id TEXT NOT NULL,
                    article_title TEXT NOT NULL,
                    document_url TEXT NOT NULL,
                    section_index INTEGER NOT NULL,
                    section_title TEXT NOT NULL,
                    review_started_at TEXT NOT NULL,
                    quote_text TEXT,
                    comment_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS completed_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    max_user_id INTEGER NOT NULL,
                    doctor_name TEXT NOT NULL,
                    sheet_row_number INTEGER NOT NULL,
                    article_id TEXT NOT NULL,
                    article_title TEXT NOT NULL,
                    document_url TEXT NOT NULL,
                    task_topic TEXT NOT NULL,
                    review_started_at TEXT NOT NULL,
                    final_status TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    max_user_id INTEGER NOT NULL,
                    doctor_name TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_doctor(self, max_user_id: int, surname: str, doctor_name: str) -> None:
        timestamp = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO doctors (max_user_id, surname, doctor_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(max_user_id) DO UPDATE SET
                    surname=excluded.surname,
                    doctor_name=excluded.doctor_name,
                    updated_at=excluded.updated_at
                """,
                (max_user_id, surname, doctor_name, timestamp, timestamp),
            )

    def get_doctor(self, max_user_id: int) -> StoredDoctor | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT max_user_id, surname, doctor_name FROM doctors WHERE max_user_id = ?",
                (max_user_id,),
            ).fetchone()

        if row is None:
            return None
        return StoredDoctor(
            max_user_id=row["max_user_id"],
            surname=row["surname"],
            doctor_name=row["doctor_name"],
        )

    def clear_doctor(self, max_user_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM doctors WHERE max_user_id = ?", (max_user_id,))
            connection.execute("DELETE FROM review_sessions WHERE max_user_id = ?", (max_user_id,))

    def save_session(
        self,
        max_user_id: int,
        sheet_row_number: int,
        article_id: str,
        article_title: str,
        document_url: str,
        current_section_index: int = 0,
    ) -> None:
        review_started_at = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO review_sessions (
                    max_user_id,
                    sheet_row_number,
                    article_id,
                    article_title,
                    document_url,
                    current_section_index,
                    review_started_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(max_user_id) DO UPDATE SET
                    sheet_row_number=excluded.sheet_row_number,
                    article_id=excluded.article_id,
                    article_title=excluded.article_title,
                    document_url=excluded.document_url,
                    current_section_index=excluded.current_section_index,
                    review_started_at=excluded.review_started_at,
                    updated_at=excluded.updated_at
                """,
                (
                    max_user_id,
                    sheet_row_number,
                    article_id,
                    article_title,
                    document_url,
                    current_section_index,
                    review_started_at,
                    self._now(),
                ),
            )

    def get_session(self, max_user_id: int) -> ReviewSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT max_user_id, sheet_row_number, article_id, article_title, document_url, current_section_index, review_started_at
                FROM review_sessions
                WHERE max_user_id = ?
                """,
                (max_user_id,),
            ).fetchone()

        if row is None:
            return None
        return ReviewSession(
            max_user_id=row["max_user_id"],
            sheet_row_number=row["sheet_row_number"],
            article_id=row["article_id"],
            article_title=row["article_title"],
            document_url=row["document_url"],
            current_section_index=row["current_section_index"],
            review_started_at=row["review_started_at"],
        )

    def update_session_section(self, max_user_id: int, section_index: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE review_sessions
                SET current_section_index = ?, updated_at = ?
                WHERE max_user_id = ?
                """,
                (section_index, self._now(), max_user_id),
            )

    def clear_session(self, max_user_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM review_sessions WHERE max_user_id = ?", (max_user_id,))

    def add_comment(self, comment: CommentRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO comments (
                    max_user_id,
                    doctor_name,
                    sheet_row_number,
                    article_id,
                    article_title,
                    document_url,
                    section_index,
                    section_title,
                    review_started_at,
                    quote_text,
                    comment_text,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    comment.max_user_id,
                    comment.doctor_name,
                    comment.sheet_row_number,
                    comment.article_id,
                    comment.article_title,
                    comment.document_url,
                    comment.section_index,
                    comment.section_title,
                    comment.review_started_at,
                    comment.quote_text,
                    comment.comment_text,
                    comment.created_at,
                ),
            )

    def get_comment_summary(self, max_user_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    article_title,
                    COUNT(*) AS comments_count,
                    MAX(created_at) AS last_comment_at
                FROM comments
                WHERE max_user_id = ?
                GROUP BY article_title
                ORDER BY last_comment_at DESC
                """,
                (max_user_id,),
            ).fetchall()
        return rows

    def get_recent_comments(self, max_user_id: int, limit: int = 10) -> list[sqlite3.Row]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT article_title, section_title, quote_text, comment_text, created_at
                FROM comments
                WHERE max_user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max_user_id, limit),
            ).fetchall()
        return rows

    def get_comments_for_review(self, max_user_id: int, sheet_row_number: int, review_started_at: str) -> list[sqlite3.Row]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT article_title, section_title, quote_text, comment_text, created_at
                FROM comments
                WHERE max_user_id = ? AND sheet_row_number = ? AND review_started_at = ?
                ORDER BY created_at ASC
                """,
                (max_user_id, sheet_row_number, review_started_at),
            ).fetchall()
        return rows

    def replace_pending_reminder(
        self,
        max_user_id: int,
        doctor_name: str,
        due_at: str,
        label: str,
    ) -> ReminderRecord:
        timestamp = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE review_reminders
                SET status = 'cancelled', updated_at = ?
                WHERE max_user_id = ? AND status = 'pending'
                """,
                (timestamp, max_user_id),
            )
            cursor = connection.execute(
                """
                INSERT INTO review_reminders (
                    max_user_id,
                    doctor_name,
                    due_at,
                    label,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (max_user_id, doctor_name, due_at, label, timestamp, timestamp),
            )
            reminder_id = int(cursor.lastrowid)

        return ReminderRecord(
            id=reminder_id,
            max_user_id=max_user_id,
            doctor_name=doctor_name,
            due_at=due_at,
            label=label,
        )

    def get_pending_reminders(self) -> list[ReminderRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, max_user_id, doctor_name, due_at, label
                FROM review_reminders
                WHERE status = 'pending'
                ORDER BY due_at ASC
                """
            ).fetchall()

        return [
            ReminderRecord(
                id=row["id"],
                max_user_id=row["max_user_id"],
                doctor_name=row["doctor_name"],
                due_at=row["due_at"],
                label=row["label"],
            )
            for row in rows
        ]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE review_reminders
                SET status = 'sent', updated_at = ?
                WHERE id = ?
                """,
                (self._now(), reminder_id),
            )

    def create_completed_review(
        self,
        max_user_id: int,
        doctor_name: str,
        sheet_row_number: int,
        article_id: str,
        article_title: str,
        document_url: str,
        task_topic: str,
        review_started_at: str,
        final_status: str,
    ) -> int:
        completed_at = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO completed_reviews (
                    max_user_id,
                    doctor_name,
                    sheet_row_number,
                    article_id,
                    article_title,
                    document_url,
                    task_topic,
                    review_started_at,
                    final_status,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    max_user_id,
                    doctor_name,
                    sheet_row_number,
                    article_id,
                    article_title,
                    document_url,
                    task_topic,
                    review_started_at,
                    final_status,
                    completed_at,
                ),
            )
            return int(cursor.lastrowid)

    def get_completed_review(self, review_id: int) -> CompletedReview | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    max_user_id,
                    doctor_name,
                    sheet_row_number,
                    article_id,
                    article_title,
                    document_url,
                    task_topic,
                    review_started_at,
                    final_status,
                    completed_at
                FROM completed_reviews
                WHERE id = ?
                """,
                (review_id,),
            ).fetchone()

        if row is None:
            return None

        return CompletedReview(
            id=row["id"],
            max_user_id=row["max_user_id"],
            doctor_name=row["doctor_name"],
            sheet_row_number=row["sheet_row_number"],
            article_id=row["article_id"],
            article_title=row["article_title"],
            document_url=row["document_url"],
            task_topic=row["task_topic"],
            review_started_at=row["review_started_at"],
            final_status=row["final_status"],
            completed_at=row["completed_at"],
        )

    def update_completed_review_status(self, review_id: int, new_status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE completed_reviews
                SET final_status = ?, completed_at = ?
                WHERE id = ?
                """,
                (new_status, self._now(), review_id),
            )

    def set_report_chat(self, chat_id: int | None, user_id: int | None, label: str) -> None:
        with self._connect() as connection:
            if chat_id is not None:
                connection.execute(
                    """
                    INSERT INTO bot_settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    ("report_chat_id", str(chat_id)),
                )
            if user_id is not None:
                connection.execute(
                    """
                    INSERT INTO bot_settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    ("report_user_id", str(user_id)),
                )
            connection.execute(
                """
                INSERT INTO bot_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("report_chat_label", label),
            )

    def get_report_chat(self) -> ReportChat | None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM bot_settings WHERE key IN ('report_chat_id', 'report_user_id', 'report_chat_label')"
            ).fetchall()

        data = {row["key"]: row["value"] for row in rows}
        chat_id = int(data["report_chat_id"]) if "report_chat_id" in data else None
        user_id = int(data["report_user_id"]) if "report_user_id" in data else None
        if chat_id is None and user_id is None:
            return None

        return ReportChat(
            chat_id=chat_id,
            user_id=user_id,
            label=data.get("report_chat_label", ""),
        )
