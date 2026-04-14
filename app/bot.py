from __future__ import annotations

import base64
import html
import logging
from datetime import datetime, timezone

from maxapi import F, Router
from maxapi.context import MemoryContext, State, StatesGroup
from maxapi.enums.parse_mode import ParseMode
from maxapi.types import BotCommand, BotStarted, Command, InputMediaBuffer, LinkButton, MessageCallback, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.google_clients import GoogleRepository
from app.keyboards import (
    completed_review_keyboard,
    doctor_choice_keyboard,
    finish_status_keyboard,
    illustrations_keyboard,
    intro_review_keyboard,
    main_menu_keyboard,
    memo_keyboard,
    outline_keyboard,
    reminder_options_keyboard,
    reminder_only_keyboard,
    review_keyboard,
    tasks_keyboard,
)
from app.models import CommentRecord, Illustration, StoredDoctor, normalize_surname
from app.reminders import ReminderService
from app.storage import Storage


logger = logging.getLogger(__name__)


class BotStates(StatesGroup):
    waiting_surname = State()
    viewing_section = State()
    viewing_illustrations = State()


def build_commands() -> list[BotCommand]:
    return [
        BotCommand(name="/start", description="Перезапустить сценарий"),
        BotCommand(name="/register_report_chat", description="Зарегистрировать чат редактора"),
    ]


def split_long_text(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = paragraph
            continue
        for index in range(0, len(paragraph), limit):
            chunks.append(paragraph[index : index + limit])
        current = ""
    if current:
        chunks.append(current)
    return chunks


def _single_link_keyboard(document_url: str):
    builder = InlineKeyboardBuilder()
    builder.row(LinkButton(text="Открыть документ", url=document_url))
    return builder.as_markup()


def _quote_from_reply(message) -> str | None:
    link = getattr(message, "link", None)
    if link is None or getattr(link, "type", None) != "reply":
        return None
    linked_message = getattr(link, "message", None)
    if linked_message is None:
        return None
    quote_text = (linked_message.text or "").strip()
    return quote_text or None


def _has_attachments(message) -> bool:
    body = getattr(message, "body", None)
    attachments = getattr(body, "attachments", None) or []
    return bool(attachments)


def create_router(repository: GoogleRepository, storage: Storage, settings, reminders: ReminderService) -> Router:
    router = Router()
    memo_text = (
        "Как проверить текст\n\n"
        "<b>1. Проверяем только факты, но не стиль</b>\n"
        "Стиль сильно упрощён под пациентские запросы в поисковиках, это важно для продвижения.\n\n"
        "<b>2. Прочитать текст и оставить комментарии</b>\n"
        "Текст переписывать не нужно. Чтобы оставить комментарий к разделу, просто отправьте сообщение. "
        "В MAX можно ответить на сообщение раздела реплаем: тогда комментарий сохранится вместе с цитатой этого сообщения. "
        "Можно записать голосовое сообщение или отправить файл.\n\n"
        "<b>3. Проверить продуктовый блок</b>\n"
        "В документе он выделен рамкой. В нём указаны сильные стороны нашей клиники: если есть, что добавить — это очень нам поможет. "
        "Например, информацию про оборудование, процедуры, которые выгодно выделяют нас на фоне других клиник.\n\n"
        "<b>Если нет времени читать или много замечаний</b>\n"
        "Свяжитесь с редактором:\n"
        "Телеграм: @zykovsrg;\n"
        "Макс и другие мессенджеры: +7 922 990-48-00;\n"
        "почта: s.zykov@hadassah.moscow.\n"
        "Договоримся о встрече и вместе пройдёмся по тексту."
    )

    async def send_google_error(target_message) -> None:
        text = (
            "Не удалось получить данные из Google. "
            "Скорее всего, не совпал секрет между ботом и Apps Script."
        )
        await target_message.answer(text, attachments=[main_menu_keyboard()])

    async def send_document_link(target_message, document_url: str, intro_text: str | None = None) -> None:
        lines = []
        if intro_text:
            lines.append(intro_text)
            lines.append("")
        lines.append("Ссылка на документ:")
        lines.append(document_url)
        await target_message.answer(
            "\n".join(lines),
            attachments=[_single_link_keyboard(document_url)],
        )

    async def send_section_illustrations(
        target_message,
        illustrations: tuple[Illustration, ...],
        reply_markup=None,
    ) -> None:
        if not illustrations:
            return

        for index, illustration in enumerate(illustrations):
            attachments = [
                InputMediaBuffer(
                    base64.b64decode(illustration.content_base64),
                    filename=illustration.filename or f"illustration-{index + 1}.jpg",
                )
            ]
            if index == len(illustrations) - 1 and reply_markup is not None:
                attachments.append(reply_markup)
            await target_message.answer(attachments=attachments)

    async def send_intro_block(
        target_message,
        row_number: int,
        sections_total: int,
        context: MemoryContext,
        intro_text: str,
        intro_illustrations: tuple[Illustration, ...],
    ) -> None:
        await context.set_state(BotStates.viewing_section)
        await context.update_data(comment_context="intro", active_row_number=row_number)

        if intro_text:
            chunks = split_long_text(html.escape(intro_text))
            reply_markup = (
                intro_review_keyboard(row_number, sections_total)
                if not intro_illustrations and chunks
                else None
            )
            for chunk_index, chunk in enumerate(chunks):
                attachments = None
                if chunk_index == len(chunks) - 1 and reply_markup is not None:
                    attachments = [reply_markup]
                await target_message.answer(
                    chunk,
                    attachments=attachments,
                    parse_mode=ParseMode.HTML,
                )

        if intro_illustrations:
            await send_section_illustrations(
                target_message,
                intro_illustrations,
                reply_markup=intro_review_keyboard(row_number, sections_total),
            )

    async def forward_media_comment(
        message,
        media_label: str,
        success_text: str,
        missing_chat_text: str,
        forward_error_text: str,
        section_title_override: str | None = None,
    ) -> str:
        doctor = storage.get_doctor(message.sender.user_id)
        session = storage.get_session(message.sender.user_id)
        if doctor is None or session is None:
            await message.answer("Сначала выберите врача и статью.")
            return "missing_context"

        task = repository.get_task_by_row(doctor.doctor_name, session.sheet_row_number)
        if task is None:
            await message.answer("Не удалось найти актуальную статью. Обновите список и выберите её заново.")
            return "missing_task"

        if section_title_override is None:
            document = repository.get_document(session.document_url)
            current_index = max(0, min(session.current_section_index, len(document.sections) - 1))
            section_title = document.sections[current_index].title
        else:
            section_title = section_title_override

        report_chat = storage.get_report_chat()
        if report_chat is None:
            await message.answer(
                f"{missing_chat_text} "
                f"Нужно сначала зарегистрировать чат {settings.report_recipient_label} через /register_report_chat."
            )
            return "missing_report_chat"

        context_text = (
            f"{media_label} от врача\n"
            f"Врач: {doctor.doctor_name}\n"
            f"Тема: {task.topic or session.article_title}\n"
            f"Раздел: {section_title}\n"
            f"Документ: {session.document_url}"
        )

        try:
            await message.bot.send_message(
                chat_id=report_chat.chat_id,
                user_id=report_chat.user_id,
                text=context_text,
            )
            await message.forward(chat_id=report_chat.chat_id, user_id=report_chat.user_id)
        except Exception:
            logger.exception("Failed to forward media comment")
            await message.answer(forward_error_text)
            return "forward_failed"

        await message.answer(success_text)
        return "ok"

    def format_report_text(
        doctor_name: str,
        task_topic: str,
        document_title: str,
        document_url: str,
        final_status: str,
        comments: list,
    ) -> str:
        text_lines = [
            "Итог проверки статьи",
            f"Врач: {doctor_name}",
            f"Тема: {task_topic}",
            f"Документ: {document_title}",
            f"Статус: {final_status}",
            f"Комментарии: {len(comments)}",
            f"Ссылка: {document_url}",
        ]

        if comments:
            text_lines.append("")
            text_lines.append("Замечания:")
            for index, row in enumerate(comments, start=1):
                line = f"{index}. {row['section_title']}"
                if row["quote_text"]:
                    line += f"\nЦитата: {row['quote_text']}"
                line += f"\nКомментарий: {row['comment_text']}"
                text_lines.append(line)
        else:
            text_lines.append("")
            text_lines.append("Замечаний не добавлено.")

        return "\n".join(text_lines)

    async def send_report_to_registered_chat(source_message, report_text: str) -> str:
        report_chat = storage.get_report_chat()
        if report_chat is None:
            return (
                "Отчёт не отправлен: чат для отчётов ещё не зарегистрирован. "
                f"Откройте бота из аккаунта {settings.report_recipient_label} и отправьте команду /register_report_chat."
            )

        try:
            await source_message.bot.send_message(
                chat_id=report_chat.chat_id,
                user_id=report_chat.user_id,
                text=report_text,
            )
            return f"Итоговый отчёт отправлен в {report_chat.label or settings.report_recipient_label}."
        except Exception:
            logger.exception("Failed to send report message")
            return "Статус обновлён, но отчёт отправить не удалось."

    async def send_dashboard(target_message, doctor: StoredDoctor, context: MemoryContext) -> None:
        try:
            tasks = repository.get_tasks_for_doctor(doctor.doctor_name)
        except Exception:
            logger.exception("Failed to load doctor tasks")
            await send_google_error(target_message)
            return

        text_lines = [
            f"Врач: {doctor.doctor_name}",
            f"Публикаций на проверку: {len(tasks)}",
            "",
            "Нажмите «Список статей», чтобы открыть публикации, или «Сменить аккаунт».",
        ]
        await target_message.answer(
            "\n".join(text_lines),
            attachments=[main_menu_keyboard()],
        )
        await context.clear()

    async def send_tasks_list(target_message, doctor: StoredDoctor) -> None:
        try:
            tasks = repository.get_tasks_for_doctor(doctor.doctor_name)
        except Exception:
            logger.exception("Failed to load doctor tasks")
            await send_google_error(target_message)
            return

        if not tasks:
            await target_message.answer(
                "Сейчас у Вас нет статей на проверке. Если это ошибка — напишите редактору.",
                attachments=[main_menu_keyboard()],
            )
            return

        await target_message.answer(
            "Список статей на проверке:",
            attachments=[tasks_keyboard(tasks)],
        )

    async def send_outline(
        target_message,
        doctor: StoredDoctor,
        row_number: int,
        context: MemoryContext,
    ) -> None:
        task = repository.get_task_by_row(doctor.doctor_name, row_number)
        if task is None:
            await target_message.answer("Не нашёл эту статью в актуальном списке. Откройте список заново.")
            return

        document = repository.get_document(task.document_url)
        storage.save_session(
            max_user_id=doctor.max_user_id,
            sheet_row_number=task.row_number,
            article_id=task.article_id,
            article_title=document.title,
            document_url=task.document_url,
            current_section_index=0,
        )

        outline = "\n".join(f"{section.index}. {section.title}" for section in document.sections)
        text = (
            f"Тема: {task.topic}\n"
            "\n"
            f"Структура:\n{outline}"
        )

        await target_message.answer(
            text,
            attachments=[outline_keyboard(task.row_number, task.document_url)],
        )
        await context.clear()

    async def send_section(
        target_message,
        doctor: StoredDoctor,
        row_number: int,
        section_index: int,
        context: MemoryContext,
    ) -> None:
        task = repository.get_task_by_row(doctor.doctor_name, row_number)
        if task is None:
            await target_message.answer("Статья больше не найдена в списке. Обновите список статей.")
            return

        document = repository.get_document(task.document_url)
        if not document.sections:
            await target_message.answer("В документе не удалось выделить разделы H2.")
            return

        section_index = max(0, min(section_index, len(document.sections) - 1))
        section = document.sections[section_index]
        storage.update_session_section(doctor.max_user_id, section_index)
        await context.set_state(BotStates.viewing_section)
        await context.update_data(comment_context="section", active_row_number=row_number)

        header = f"Раздел {section.index}/{len(document.sections)}\n\n"
        title = f"<b>{html.escape(section.title)}</b>\n\n"
        body = html.escape(section.body or "В этом разделе пока нет текста.")
        chunks = split_long_text(f"{header}{title}{body}")
        reply_markup = review_keyboard(
            row_number,
            section_index,
            len(document.sections),
            show_illustrations=(section_index == len(document.sections) - 1),
        )

        illustrations = section.illustrations

        for chunk_index, chunk in enumerate(chunks):
            attachments = None
            if chunk_index == len(chunks) - 1 and not illustrations:
                attachments = [reply_markup]
            await target_message.answer(
                chunk,
                attachments=attachments,
                parse_mode=ParseMode.HTML,
            )

        if illustrations:
            await send_section_illustrations(target_message, illustrations, reply_markup=reply_markup)

    async def persist_comment(
        message,
        context: MemoryContext,
        comment_text: str,
        section_title_override: str | None = None,
        section_index_override: int | None = None,
    ) -> None:
        doctor = storage.get_doctor(message.sender.user_id)
        session = storage.get_session(message.sender.user_id)
        if doctor is None or session is None:
            await message.answer("Сначала выберите врача и статью.")
            return

        task = repository.get_task_by_row(doctor.doctor_name, session.sheet_row_number)
        if task is None:
            await message.answer("Не удалось найти актуальную статью. Обновите список и выберите её заново.")
            return

        if section_title_override is None or section_index_override is None:
            state_data = await context.get_data()
            if state_data.get("comment_context") == "intro":
                section_index = 0
                section_title = "Вводная часть"
            else:
                document = repository.get_document(session.document_url)
                current_index = max(0, min(session.current_section_index, len(document.sections) - 1))
                section = document.sections[current_index]
                section_index = section.index
                section_title = section.title
        else:
            section_index = section_index_override
            section_title = section_title_override

        quote_text = _quote_from_reply(message)

        record = CommentRecord(
            max_user_id=message.sender.user_id,
            doctor_name=doctor.doctor_name,
            sheet_row_number=session.sheet_row_number,
            article_id=session.article_id,
            article_title=session.article_title,
            document_url=session.document_url,
            section_index=section_index,
            section_title=section_title,
            review_started_at=session.review_started_at,
            quote_text=quote_text,
            comment_text=comment_text.strip(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        storage.add_comment(record)

        try:
            repository.append_comment(record)
        except Exception:
            logger.exception("Failed to append comment to Google Sheets")

        if quote_text:
            await message.answer("Комментарий с цитатой сохранён.")
        else:
            await message.answer("Комментарий сохранён.")

        state_data = await context.get_data()
        if state_data.get("comment_context") == "illustrations":
            await context.set_state(BotStates.viewing_illustrations)
        else:
            await context.set_state(BotStates.viewing_section)

    @router.bot_started()
    async def handle_bot_started(event: BotStarted, context: MemoryContext) -> None:
        doctor = storage.get_doctor(event.user.user_id)
        if doctor is not None:
            await event.bot.send_message(
                chat_id=event.chat_id,
                user_id=event.user.user_id,
                text="Вы уже ввели фамилию. Ниже можно посмотреть список статей или сменить аккаунт.",
                attachments=[main_menu_keyboard()],
            )
            return

        await context.set_state(BotStates.waiting_surname)
        await event.bot.send_message(
            chat_id=event.chat_id,
            user_id=event.user.user_id,
            text=(
                "Здравствуйте! Этот бот помогает проверить публикации для сайта нашей клиники. "
                "Если бот плохо или неудобно работает, напишите @zykovsrg.\n\n"
                "Введите Вашу фамилию:"
            ),
        )

    @router.message_created(Command("start"))
    async def handle_start(event: MessageCreated, context: MemoryContext) -> None:
        doctor = storage.get_doctor(event.message.sender.user_id)
        if doctor is not None:
            await event.message.answer(
                "Вы уже ввели фамилию. Ниже можно посмотреть список статей или сменить аккаунт.",
                attachments=[main_menu_keyboard()],
            )
            await send_dashboard(event.message, doctor, context)
            return

        await context.set_state(BotStates.waiting_surname)
        await event.message.answer(
            "Здравствуйте! Этот бот помогает проверить публикации для сайта нашей клиники. "
            "Если бот плохо или неудобно работает, напишите @zykovsrg.\n\n"
            "Введите Вашу фамилию:",
        )

    @router.message_created(Command("register_report_chat"))
    async def handle_register_report_chat(event: MessageCreated) -> None:
        user = event.message.sender
        label = f"@{user.username}" if user.username else user.full_name
        storage.set_report_chat(
            chat_id=event.chat.chat_id,
            user_id=user.user_id,
            label=label,
        )
        await event.message.answer(f"Этот чат зарегистрирован для итоговых отчётов. Сейчас отчёты будут уходить сюда: {label}")

    @router.message_created(F.message.body.text, BotStates.waiting_surname)
    async def handle_surname(event: MessageCreated, context: MemoryContext) -> None:
        surname = normalize_surname(event.message.body.text or "")
        if not surname:
            await event.message.answer("Не понял фамилию. Напишите её ещё раз одним сообщением.")
            return

        try:
            doctor_choices = repository.get_doctor_choices(surname)
        except Exception:
            logger.exception("Failed to load doctor choices")
            await send_google_error(event.message)
            return

        if not doctor_choices:
            await event.message.answer(
                "Сейчас у Вас нет статей на проверку. Если это ошибка — напишите @zykovsrg",
                attachments=[main_menu_keyboard()],
            )
            return

        if len(doctor_choices) == 1:
            doctor_name = doctor_choices[0]
            storage.upsert_doctor(event.message.sender.user_id, surname, doctor_name)
            await context.clear()
            await event.message.answer(
                f"Привязал вас к врачу: {doctor_name}",
                attachments=[main_menu_keyboard()],
            )
            doctor = storage.get_doctor(event.message.sender.user_id)
            assert doctor is not None
            await send_dashboard(event.message, doctor, context)
            return

        await context.update_data(doctor_choices=doctor_choices, surname=surname)
        await event.message.answer(
            "Нашёл несколько врачей. Выберите нужного.",
            attachments=[doctor_choice_keyboard(doctor_choices)],
        )

    @router.message_created(BotStates.viewing_section)
    async def handle_section_message(event: MessageCreated, context: MemoryContext) -> None:
        if _has_attachments(event.message):
            await forward_media_comment(
                event.message,
                media_label="Медиа-комментарий",
                success_text="Комментарий с вложением отправлен редактору.",
                missing_chat_text="Комментарий с вложением пока некуда переслать.",
                forward_error_text="Не удалось переслать вложение редактору. Попробуйте ещё раз чуть позже.",
            )
            return

        text = (event.message.body.text or "").strip()
        if not text:
            await event.message.answer("Если хотите оставить комментарий, отправьте его текстом.")
            return
        await persist_comment(event.message, context, text)

    @router.message_created(BotStates.viewing_illustrations)
    async def handle_illustrations_message(event: MessageCreated, context: MemoryContext) -> None:
        if _has_attachments(event.message):
            await forward_media_comment(
                event.message,
                media_label="Медиа-комментарий",
                success_text="Комментарий с вложением по иллюстрациям отправлен редактору.",
                missing_chat_text="Комментарий с вложением по иллюстрациям пока некуда переслать.",
                forward_error_text="Не удалось переслать вложение редактору. Попробуйте ещё раз чуть позже.",
                section_title_override="Иллюстрации",
            )
            return

        text = (event.message.body.text or "").strip()
        if not text:
            await event.message.answer("Если хотите оставить комментарий по иллюстрациям, отправьте его текстом.")
            return
        await persist_comment(
            event.message,
            context,
            text,
            section_title_override="Иллюстрации",
            section_index_override=999,
        )

    @router.message_callback()
    async def handle_callback(callback: MessageCallback, context: MemoryContext) -> None:
        payload = callback.callback.payload or ""
        user_id = callback.callback.user.user_id
        doctor = storage.get_doctor(user_id)

        try:
            await callback.answer()
        except Exception:
            logger.exception("Failed to acknowledge MAX callback")

        if payload == "change_doctor":
            storage.clear_doctor(user_id)
            await context.set_state(BotStates.waiting_surname)
            await callback.message.answer("Введите фамилию врача заново.")
            return

        if payload == "dashboard":
            if doctor is None:
                await context.set_state(BotStates.waiting_surname)
                await callback.message.answer("Сначала введите фамилию врача.")
                return
            await send_dashboard(callback.message, doctor, context)
            return

        if payload == "tasks_list":
            if doctor is None:
                await context.set_state(BotStates.waiting_surname)
                await callback.message.answer("Сначала введите фамилию врача.")
                return
            await send_tasks_list(callback.message, doctor)
            return

        if payload.startswith("doctor:"):
            data = await context.get_data()
            doctor_choices: list[str] = data.get("doctor_choices", [])
            surname = data.get("surname", "")
            index = int(payload.split(":", maxsplit=1)[1])

            if index >= len(doctor_choices):
                await context.set_state(BotStates.waiting_surname)
                await callback.message.answer("Вариант устарел. Введите фамилию заново.")
                return

            doctor_name = doctor_choices[index]
            storage.upsert_doctor(user_id, surname, doctor_name)
            await context.clear()
            await callback.message.answer(
                f"Привязал вас к врачу: {doctor_name}",
                attachments=[main_menu_keyboard()],
            )
            doctor = storage.get_doctor(user_id)
            assert doctor is not None
            await send_dashboard(callback.message, doctor, context)
            return

        if doctor is None:
            await context.set_state(BotStates.waiting_surname)
            await callback.message.answer("Сначала введите фамилию врача.")
            return

        if payload.startswith("article:"):
            row_number = int(payload.split(":", maxsplit=1)[1])
            await send_outline(callback.message, doctor, row_number, context)
            return

        if payload.startswith("outline:"):
            row_number = int(payload.split(":", maxsplit=1)[1])
            await send_outline(callback.message, doctor, row_number, context)
            return

        if payload.startswith("start:"):
            row_number = int(payload.split(":", maxsplit=1)[1])
            task = repository.get_task_by_row(doctor.doctor_name, row_number)
            if task is None:
                await callback.message.answer("Статья больше не найдена в списке. Обновите список статей.")
                return
            document = repository.get_document(task.document_url)
            storage.save_session(
                max_user_id=doctor.max_user_id,
                sheet_row_number=task.row_number,
                article_id=task.article_id,
                article_title=document.title,
                document_url=task.document_url,
                current_section_index=0,
            )
            if document.intro or document.intro_illustrations:
                await send_intro_block(
                    callback.message,
                    row_number=row_number,
                    sections_total=len(document.sections),
                    context=context,
                    intro_text=document.intro,
                    intro_illustrations=document.intro_illustrations,
                )
                return
            await send_section(callback.message, doctor, row_number, section_index=0, context=context)
            return

        if payload.startswith("nav:"):
            _, row_number, section_index = payload.split(":")
            await send_section(callback.message, doctor, int(row_number), int(section_index), context)
            return

        if payload.startswith("memo:"):
            await callback.message.answer(
                memo_text,
                parse_mode=ParseMode.HTML,
                attachments=[memo_keyboard()],
            )
            return

        if payload == "remind_menu":
            await callback.message.answer(
                "Когда напомнить?",
                attachments=[reminder_options_keyboard()],
            )
            return

        if payload.startswith("remind_set:"):
            option = payload.split(":", maxsplit=1)[1]
            label = reminders.describe_option(option)
            due_at = reminders.calculate_due_at(option)
            reminders.schedule_for_doctor(
                max_user_id=user_id,
                doctor_name=doctor.doctor_name,
                due_at=due_at,
                label=label,
            )
            await callback.message.answer(f"Хорошо, напомню {label}.")
            return

        if payload.startswith("illustrations:"):
            session = storage.get_session(user_id)
            if session is None:
                await callback.message.answer("Сначала выберите статью.")
                return
            row_number = int(payload.split(":", maxsplit=1)[1])
            task = repository.get_task_by_row(doctor.doctor_name, row_number)
            if task is None:
                await callback.message.answer("Не нашёл эту статью в актуальном списке.")
                return

            await context.set_state(BotStates.viewing_illustrations)
            await context.update_data(comment_context="illustrations", active_row_number=row_number)
            await send_document_link(
                callback.message,
                task.document_url,
                intro_text=(
                    "Откройте документ, проверьте иллюстрации и пришлите замечания сюда сообщением. "
                    "Я сохраню их как комментарии к иллюстрациям."
                ),
            )
            await callback.message.answer(
                "После проверки иллюстраций можно оставить комментарии сюда или завершить статью.",
                attachments=[illustrations_keyboard(row_number)],
            )
            return

        if payload == "finish":
            await callback.message.answer(
                "Выберите итог проверки:",
                attachments=[finish_status_keyboard()],
            )
            return

        if payload.startswith("finish_status:"):
            session = storage.get_session(user_id)
            if session is None:
                await callback.message.answer("Сессия статьи уже завершена.")
                return

            selected_action = payload.split(":", maxsplit=1)[1]
            final_status = (
                settings.approved_status_value
                if selected_action == "Проверено"
                else settings.pending_status_value
            )
            task = repository.get_task_by_row(doctor.doctor_name, session.sheet_row_number)
            topic = task.topic if task is not None else session.article_title

            try:
                repository.update_article_status(session.sheet_row_number, final_status)
            except Exception:
                logger.exception("Failed to update article status")
                await callback.message.answer(
                    "Не удалось обновить статус в таблице. Попробуйте ещё раз чуть позже.",
                    attachments=[main_menu_keyboard()],
                )
                return

            comments = storage.get_comments_for_review(
                user_id,
                session.sheet_row_number,
                session.review_started_at,
            )
            report_text = format_report_text(
                doctor_name=doctor.doctor_name,
                task_topic=topic,
                document_title=session.article_title,
                document_url=session.document_url,
                final_status=final_status,
                comments=comments,
            )
            review_id = storage.create_completed_review(
                max_user_id=user_id,
                doctor_name=doctor.doctor_name,
                sheet_row_number=session.sheet_row_number,
                article_id=session.article_id,
                article_title=session.article_title,
                document_url=session.document_url,
                task_topic=topic,
                review_started_at=session.review_started_at,
                final_status=final_status,
            )
            report_result = await send_report_to_registered_chat(callback.message, report_text)

            storage.clear_session(user_id)
            await context.clear()
            await callback.message.answer(
                f"Статус статьи обновлён: {final_status}.\n{report_result}",
                attachments=[completed_review_keyboard(
                    review_id,
                    is_approved=(final_status == settings.approved_status_value),
                )],
            )
            await send_dashboard(callback.message, doctor, context)
            return

        if payload.startswith("review_status:"):
            _, review_id_raw, action = payload.split(":", maxsplit=2)
            review = storage.get_completed_review(int(review_id_raw))
            if review is None or review.max_user_id != user_id:
                await callback.message.answer("Не нашёл эту завершённую проверку.")
                return

            new_status = (
                settings.approved_status_value
                if action == "approved"
                else settings.pending_status_value
            )

            try:
                repository.update_article_status(review.sheet_row_number, new_status)
            except Exception:
                logger.exception("Failed to rewrite article status")
                await callback.message.answer("Не удалось переписать статус.")
                return

            storage.update_completed_review_status(review.id, new_status)
            comments = storage.get_comments_for_review(
                review.max_user_id,
                review.sheet_row_number,
                review.review_started_at,
            )
            report_text = format_report_text(
                doctor_name=review.doctor_name,
                task_topic=review.task_topic,
                document_title=review.article_title,
                document_url=review.document_url,
                final_status=new_status,
                comments=comments,
            )
            report_result = await send_report_to_registered_chat(callback.message, report_text)

            await callback.message.answer(
                f"Статус статьи переписан: {new_status}.\n{report_result}",
                attachments=[completed_review_keyboard(
                    review.id,
                    is_approved=(new_status == settings.approved_status_value),
                )],
            )
            return

    return router
