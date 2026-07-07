"""Реестр кнопок меню бота + сборка клавиатур с учётом переопределений из админки.

Структура и раскладка меню (какие кнопки, в каком порядке) — здесь, это источник
истины. Текст и видимость каждой кнопки можно переопределить из веб-админки (таблица
`menu_buttons`); бот сливает реестр с переопределениями при каждом рендере, поэтому
правки применяются без рестарта.

Верхний уровень контекстен по статусу подписки: гость видит «Вступить в клуб»,
подписчик — «Моя подписку» (появится на этапе 3).
"""
from __future__ import annotations

import asyncpg
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import keyboards as kb
from .. import repo

# Реестр: ключ → (дефолтная подпись, callback). Порядок определяет порядок в админке.
BUTTON_DEFS: dict[str, tuple[str, str]] = {
    "join": ("Вступить в клуб", kb.NAV_JOIN),
    "mysub": ("Моя подписка", kb.NAV_MYSUB),
    "aboutmenu": ("О клубе", kb.NAV_ABOUTMENU),
    "about": ("Что внутри клуба", kb.NAV_ABOUT),
    "rules": ("Правила клуба", kb.NAV_RULES),
    "support": ("Поддержка", kb.NAV_SUPPORT),
}

# Контекстные раскладки верхнего уровня по статусу подписки. Главное действие —
# первым рядом во всю ширину (иерархия позицией, без эмодзи).
LAYOUT_GUEST: list[list[str]] = [
    ["join"], ["aboutmenu", "support"],
]
LAYOUT_SUB: list[list[str]] = [
    ["mysub"], ["aboutmenu", "support"],
]
# Подменю «О клубе».
ABOUTMENU_LAYOUT: list[list[str]] = [
    ["about"], ["rules"],
]


def _top_layout(subscribed: bool) -> list[list[str]]:
    return LAYOUT_SUB if subscribed else LAYOUT_GUEST


async def resolve_config(pool: asyncpg.Pool) -> dict[str, dict]:
    """Слитый конфиг кнопок: реестр + переопределения из БД.

    {key: {'label', 'default_label', 'is_visible', 'custom'}} для всех кнопок реестра.
    """
    overrides = await repo.get_menu_overrides(pool)
    config: dict[str, dict] = {}
    for key, (default_label, _cb) in BUTTON_DEFS.items():
        ov = overrides.get(key)
        custom_label = ov["label"] if ov and ov["label"] else None
        config[key] = {
            "label": custom_label or default_label,
            "default_label": default_label,
            "is_visible": ov["is_visible"] if ov is not None else True,
            "custom": custom_label is not None,
        }
    return config


def _build(layout: list[list[str]], config: dict[str, dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for row in layout:
        visible = [k for k in row if config[k]["is_visible"]]
        if not visible:
            continue
        b.row(*[
            InlineKeyboardButton(text=config[k]["label"], callback_data=BUTTON_DEFS[k][1])
            for k in visible
        ])
    return b.as_markup()


async def welcome_kb(pool: asyncpg.Pool, subscribed: bool) -> InlineKeyboardMarkup:
    """Клавиатура приветствия (/start): контекстна по статусу подписки."""
    return _build(_top_layout(subscribed), await resolve_config(pool))


async def main_menu_kb(pool: asyncpg.Pool, subscribed: bool) -> InlineKeyboardMarkup:
    """Клавиатура главного меню (/menu): контекстна по статусу подписки."""
    return _build(_top_layout(subscribed), await resolve_config(pool))


async def aboutmenu_kb(pool: asyncpg.Pool) -> InlineKeyboardMarkup:
    """Подменю «О клубе»: инфо-экраны + «Назад» в меню."""
    b = InlineKeyboardBuilder()
    config = await resolve_config(pool)
    for row in ABOUTMENU_LAYOUT:
        visible = [k for k in row if config[k]["is_visible"]]
        if not visible:
            continue
        b.row(*[
            InlineKeyboardButton(text=config[k]["label"], callback_data=BUTTON_DEFS[k][1])
            for k in visible
        ])
    b.row(InlineKeyboardButton(text="Назад", callback_data=kb.NAV_MENU))
    return b.as_markup()


async def button_list(pool: asyncpg.Pool) -> list[dict]:
    """Список кнопок для формы веб-админки (в порядке реестра)."""
    config = await resolve_config(pool)
    return [{"key": key, **config[key]} for key in BUTTON_DEFS]
