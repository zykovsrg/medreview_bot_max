from __future__ import annotations

from maxapi.types import CallbackButton, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.models import ArticleTask


def _builder() -> InlineKeyboardBuilder:
    return InlineKeyboardBuilder()


def main_menu_keyboard():
    builder = _builder()
    builder.row(CallbackButton(text="Список статей", payload="tasks_list"))
    builder.row(CallbackButton(text="Сменить аккаунт", payload="change_doctor"))
    return builder.as_markup()


def doctor_choice_keyboard(doctors: list[str]):
    builder = _builder()
    for index, doctor_name in enumerate(doctors):
        builder.row(CallbackButton(text=doctor_name, payload=f"doctor:{index}"))
    return builder.as_markup()


def tasks_keyboard(tasks: list[ArticleTask]):
    builder = _builder()
    for task in tasks:
        builder.row(CallbackButton(text=task.topic[:64], payload=f"article:{task.row_number}"))
    builder.row(CallbackButton(text="Напомнить о проверке", payload="remind_menu"))
    builder.row(CallbackButton(text="На главный экран", payload="dashboard"))
    builder.row(CallbackButton(text="Сменить аккаунт", payload="change_doctor"))
    return builder.as_markup()


def outline_keyboard(row_number: int, document_url: str):
    builder = _builder()
    builder.row(CallbackButton(text="Начать проверку", payload=f"start:{row_number}"))
    builder.row(CallbackButton(text="Как быстро проверить текст", payload=f"memo:{row_number}"))
    builder.row(LinkButton(text="Ссылка на документ", url=document_url))
    builder.row(CallbackButton(text="К списку статей", payload="tasks_list"))
    return builder.as_markup()


def memo_keyboard():
    builder = _builder()
    builder.row(CallbackButton(text="К списку статей", payload="tasks_list"))
    return builder.as_markup()


def reminder_options_keyboard():
    builder = _builder()
    builder.row(CallbackButton(text="Через 1 час", payload="remind_set:1h"))
    builder.row(CallbackButton(text="Через 3 часа", payload="remind_set:3h"))
    builder.row(CallbackButton(text="Через 6 часов", payload="remind_set:6h"))
    builder.row(CallbackButton(text="Завтра в 8:00", payload="remind_set:tomorrow8"))
    builder.row(CallbackButton(text="На главный экран", payload="dashboard"))
    return builder.as_markup()


def reminder_only_keyboard():
    builder = _builder()
    builder.row(CallbackButton(text="Напомнить о проверке", payload="remind_menu"))
    builder.row(CallbackButton(text="На главный экран", payload="dashboard"))
    return builder.as_markup()


def finish_status_keyboard():
    builder = _builder()
    builder.row(CallbackButton(text="Не проверено", payload="finish_status:Не проверено"))
    builder.row(CallbackButton(text="Проверено", payload="finish_status:Проверено"))
    return builder.as_markup()


def completed_review_keyboard(review_id: int, is_approved: bool):
    builder = _builder()
    if is_approved:
        builder.row(CallbackButton(text="Не проверено", payload=f"review_status:{review_id}:pending"))
    else:
        builder.row(CallbackButton(text="Проверено", payload=f"review_status:{review_id}:approved"))
    builder.row(CallbackButton(text="К списку статей", payload="tasks_list"))
    return builder.as_markup()


def illustrations_keyboard(row_number: int):
    builder = _builder()
    builder.row(CallbackButton(text="К структуре", payload=f"outline:{row_number}"))
    builder.row(CallbackButton(text="Завершить проверку", payload="finish"))
    return builder.as_markup()


def intro_review_keyboard(row_number: int, sections_total: int):
    builder = _builder()
    if sections_total > 0:
        builder.row(CallbackButton(text="Далее", payload=f"nav:{row_number}:0"))
    builder.row(CallbackButton(text="К структуре", payload=f"outline:{row_number}"))
    builder.row(CallbackButton(text="Завершить проверку", payload="finish"))
    return builder.as_markup()


def review_keyboard(
    row_number: int,
    section_index: int,
    sections_total: int,
    show_illustrations: bool = False,
):
    builder = _builder()
    if section_index > 0:
        builder.row(CallbackButton(text="Назад", payload=f"nav:{row_number}:{section_index - 1}"))
    if section_index < sections_total - 1:
        builder.row(CallbackButton(text="Далее", payload=f"nav:{row_number}:{section_index + 1}"))
    if show_illustrations:
        builder.row(CallbackButton(text="Проверить иллюстрации", payload=f"illustrations:{row_number}"))
    builder.row(CallbackButton(text="К структуре", payload=f"outline:{row_number}"))
    builder.row(CallbackButton(text="Завершить проверку", payload="finish"))
    return builder.as_markup()
