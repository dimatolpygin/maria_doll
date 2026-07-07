"""Inline-клавиатуры и callback-константы навигации (клубная часть)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import settings

# ── Навигация по экранам ─────────────────────────────────────────────────────
NAV_START = "nav:start"
NAV_ABOUT = "nav:about"
NAV_RULES = "nav:rules"
NAV_SUPPORT = "nav:support"
NAV_MENU = "nav:menu"
NAV_ABOUTMENU = "nav:aboutmenu"  # Подменю «О клубе»: что внутри / правила

# Разделы тарифов/оплаты/подписки/промокодов.
NAV_JOIN = "nav:join"        # Вступить в клуб → выбор тарифа
NAV_TARIFF = "nav:tariff"    # Выбрать тариф
NAV_MYSUB = "nav:mysub"      # Моя подписка (этап 3)
NAV_RENEW = "nav:renew"      # Продлить подписку (этап 4)
NAV_PROMO = "nav:promo"      # Ввести промокод (этап 5)

# Клавиатуры приветствия (welcome_kb) и главного меню (main_menu_kb) — в services.menu
# (они DB-управляемые: подписи/видимость правятся из админки). Здесь — вторичные экраны.


def about_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="Выбрать тариф", callback_data=NAV_TARIFF))
    b.row(InlineKeyboardButton(text="Назад", callback_data=NAV_ABOUTMENU))
    return b.as_markup()


def rules_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="Вступить в клуб", callback_data=NAV_JOIN))
    b.row(InlineKeyboardButton(text="Назад", callback_data=NAV_ABOUTMENU))
    return b.as_markup()


def support_kb(url: str | None) -> InlineKeyboardMarkup:
    """Экран поддержки: кнопка-ссылка «Перейти» (если ссылка задана) + «Назад»."""
    b = InlineKeyboardBuilder()
    if url:
        b.row(InlineKeyboardButton(text="Перейти в поддержку", url=url))
    b.row(InlineKeyboardButton(text="Назад", callback_data=NAV_START))
    return b.as_markup()


def to_menu_kb() -> InlineKeyboardMarkup:
    """Кнопка в главное меню (для заглушек и подтверждений)."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="В главное меню", callback_data=NAV_MENU))
    return b.as_markup()


def sub_ended_kb() -> InlineKeyboardMarkup:
    """Клавиатура уведомления «подписка закончилась»: вернуться / промокод."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="Вернуться в клуб", callback_data=NAV_TARIFF))
    b.row(InlineKeyboardButton(text="В главное меню", callback_data=NAV_MENU))
    return b.as_markup()


def mysub_kb() -> InlineKeyboardMarkup:
    """Клавиатура экрана «Моя подписка»: чат (если задан) + меню."""
    b = InlineKeyboardBuilder()
    if settings.club_chat_invite_url:
        b.row(InlineKeyboardButton(
            text="Перейти в закрытый чат", url=settings.club_chat_invite_url
        ))
    b.row(InlineKeyboardButton(text="В главное меню", callback_data=NAV_MENU))
    return b.as_markup()
