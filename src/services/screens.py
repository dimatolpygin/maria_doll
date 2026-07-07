"""Реестр редактируемых экранов бота + резолв текста/картинки с учётом БД.

Тексты инфо-экранов правятся из веб-админки без правки кода. Состав экранов и
дефолтные тексты — здесь (источник истины, ссылается на `texts.py`). Переопределения
хранятся в таблице `screen_texts`; бот сливает реестр с переопределениями при каждом
рендере, поэтому правки применяются без рестарта.

Редактируемы только СТАТИЧНЫЕ инфо-экраны. Динамические (тарифы, оплата) собираются
из данных и здесь не значатся.
"""
from __future__ import annotations

from contextlib import suppress

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto, Message
import asyncpg

from .. import repo, texts

# Лимит подписи под фото в Telegram (экран с картинкой = одно сообщение фото+подпись).
CAPTION_LIMIT = 1024

# Реестр: ключ → метаданные экрана. Порядок определяет порядок в админке.
SCREEN_DEFS: dict[str, dict] = {
    "start": {
        "title": "Приветствие (/start)",
        "default": texts.WELCOME,
        "menu": "welcome",
        "hint": "Первый экран при входе в бота.",
    },
    "menu": {
        "title": "Главное меню (/menu)",
        "default": texts.MAIN_MENU,
        "menu": "main",
        "hint": "Главное меню бота.",
    },
    "aboutmenu": {
        "title": "Подменю «О клубе»",
        "default": texts.ABOUTMENU,
        "menu": "aboutmenu",
        "hint": "Хаб: Что внутри · Правила. Подписи кнопок правятся здесь.",
    },
    "about": {
        "title": "Что внутри клуба",
        "default": texts.ABOUT,
        "menu": None,
        "hint": "Описание клуба. Кнопки экрана — навигационные, не редактируются.",
    },
    "rules": {
        "title": "Правила участия",
        "default": texts.RULES,
        "menu": None,
        "hint": "Правила пространства. Кнопки экрана — навигационные, не редактируются.",
    },
    "support": {
        "title": "Поддержка",
        "default": texts.SUPPORT,
        "menu": None,
        "hint": "Экран поддержки (когда ссылка задана). Сама ссылка — в настройках бота.",
    },
    "support_no_link": {
        "title": "Поддержка — ссылка не задана",
        "default": texts.SUPPORT_NO_LINK,
        "menu": None,
        "hint": "Запасной текст, если контакт поддержки ещё не настроен.",
    },
}


def default_text(key: str) -> str:
    return SCREEN_DEFS[key]["default"]


async def text(pool: asyncpg.Pool, key: str) -> str:
    """Текст экрана: переопределение из БД или дефолт из реестра (fallback)."""
    ov = (await repo.get_screen_overrides(pool)).get(key) or {}
    return ov.get("body") or SCREEN_DEFS[key]["default"]


async def resolve(pool: asyncpg.Pool, key: str) -> dict:
    """Что показать: {"text": ..., "photo_url": str|None}."""
    ov = (await repo.get_screen_overrides(pool)).get(key) or {}
    return {
        "text": ov.get("body") or SCREEN_DEFS[key]["default"],
        "photo_url": ov.get("photo_url"),
    }


async def screen_list(pool: asyncpg.Pool) -> list[dict]:
    """Список экранов для админки (в порядке реестра)."""
    overrides = await repo.get_screen_overrides(pool)
    result: list[dict] = []
    for key, meta in SCREEN_DEFS.items():
        ov = overrides.get(key) or {}
        result.append({
            "key": key,
            "title": meta["title"],
            "hint": meta["hint"],
            "menu": meta["menu"],
            "body": ov.get("body") or meta["default"],
            "default_body": meta["default"],
            "custom": bool(ov.get("body")),
            "photo_url": ov.get("photo_url"),
        })
    return result


async def render(
    message: Message,
    *,
    text: str,
    markup: InlineKeyboardMarkup,
    photo_url: str | None,
    edit: bool,
) -> None:
    """Безопасный показ инфо-экрана (с фото или без), не ломающий навигацию.

    Telegram не умеет edit_text по сообщению-фото. Единое правило для всех точек показа:
    - edit=False (/start, /menu): answer_photo если фото, иначе answer.
    - edit=True (переход по кнопке): аккуратно заменяем сообщение с учётом фото↔текст.
    """
    if not edit:
        if photo_url:
            await message.answer_photo(photo_url, caption=text, reply_markup=markup)
        else:
            await message.answer(text, reply_markup=markup)
        return

    current_is_photo = bool(message.photo)

    if not photo_url:
        if current_is_photo:
            with suppress(TelegramBadRequest):
                await message.delete()
            await message.answer(text, reply_markup=markup)
        else:
            with suppress(TelegramBadRequest):  # «message is not modified» — не критично
                await message.edit_text(text, reply_markup=markup)
        return

    # Целевой экран с фото.
    if current_is_photo:
        try:
            await message.edit_media(
                InputMediaPhoto(media=photo_url, caption=text), reply_markup=markup,
            )
        except TelegramBadRequest:
            with suppress(TelegramBadRequest):
                await message.delete()
            await message.answer_photo(photo_url, caption=text, reply_markup=markup)
    else:
        with suppress(TelegramBadRequest):
            await message.delete()
        await message.answer_photo(photo_url, caption=text, reply_markup=markup)


async def screen_one(pool: asyncpg.Pool, key: str) -> dict | None:
    """Один экран для формы админки (или None, если ключ неизвестен)."""
    if key not in SCREEN_DEFS:
        return None
    for screen in await screen_list(pool):
        if screen["key"] == key:
            return screen
    return None
