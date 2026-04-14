from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from maxapi import Bot

from app.google_clients import GoogleRepository
from app.keyboards import reminder_only_keyboard, tasks_keyboard
from app.models import ReminderRecord
from app.storage import Storage


logger = logging.getLogger(__name__)


class ReminderService:
    def __init__(self, storage: Storage, repository: GoogleRepository, timezone_name: str = "Europe/Moscow") -> None:
        self._storage = storage
        self._repository = repository
        self._timezone = ZoneInfo(timezone_name)
        self._bot: Bot | None = None
        self._jobs: dict[int, asyncio.Task] = {}

    async def start(self, bot: Bot) -> None:
        self._bot = bot
        for reminder in self._storage.get_pending_reminders():
            self._schedule(reminder)

    def describe_option(self, option: str) -> str:
        mapping = {
            "1h": "через 1 час",
            "3h": "через 3 часа",
            "6h": "через 6 часов",
            "tomorrow8": "завтра в 8:00",
        }
        return mapping[option]

    def calculate_due_at(self, option: str) -> datetime:
        now_utc = datetime.now(timezone.utc)
        if option == "1h":
            return now_utc + timedelta(hours=1)
        if option == "3h":
            return now_utc + timedelta(hours=3)
        if option == "6h":
            return now_utc + timedelta(hours=6)
        if option == "tomorrow8":
            now_local = now_utc.astimezone(self._timezone)
            tomorrow = now_local.date() + timedelta(days=1)
            due_local = datetime.combine(tomorrow, time(8, 0), tzinfo=self._timezone)
            return due_local.astimezone(timezone.utc)
        raise ValueError(f"Unsupported reminder option: {option}")

    def schedule_for_doctor(self, max_user_id: int, doctor_name: str, due_at: datetime, label: str) -> ReminderRecord:
        self.cancel_for_user(max_user_id)
        reminder = self._storage.replace_pending_reminder(
            max_user_id=max_user_id,
            doctor_name=doctor_name,
            due_at=due_at.astimezone(timezone.utc).isoformat(),
            label=label,
        )
        self._schedule(reminder)
        return reminder

    def cancel_for_user(self, max_user_id: int) -> None:
        job = self._jobs.pop(max_user_id, None)
        if job is not None:
            job.cancel()

    def _schedule(self, reminder: ReminderRecord) -> None:
        self.cancel_for_user(reminder.max_user_id)
        self._jobs[reminder.max_user_id] = asyncio.create_task(self._run(reminder))

    async def _run(self, reminder: ReminderRecord) -> None:
        try:
            due_at = datetime.fromisoformat(reminder.due_at)
            delay = max(0.0, (due_at - datetime.now(timezone.utc)).total_seconds())
            await asyncio.sleep(delay)
            await self._send(reminder)
            self._storage.mark_reminder_sent(reminder.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to send reminder")
        finally:
            self._jobs.pop(reminder.max_user_id, None)

    async def _send(self, reminder: ReminderRecord) -> None:
        if self._bot is None:
            raise RuntimeError("ReminderService bot is not initialized.")

        tasks = self._repository.get_tasks_for_doctor(reminder.doctor_name)
        if tasks:
            text = (
                "Напоминание о проверке\n\n"
                f"Сейчас у вас на проверке: {len(tasks)}\n\n"
                "Можно открыть статью из списка ниже или отложить проверку ещё раз."
            )
            attachments = [tasks_keyboard(tasks)]
        else:
            text = (
                "Напоминание о проверке\n\n"
                "Сейчас статей на проверке нет. Если нужно, можно снова поставить напоминание."
            )
            attachments = [reminder_only_keyboard()]

        await self._bot.send_message(
            user_id=reminder.max_user_id,
            text=text,
            attachments=attachments,
        )
