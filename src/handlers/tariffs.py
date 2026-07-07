"""Экран выбора тарифа и сводка перед оплатой.

Тарифы фиксированные (из таблицы `tariffs`, правятся в админке). Кнопка «Перейти к
оплате» на этапе 1 — заглушка; реальная оплата Продамус придёт на этапе 2.
"""
from __future__ import annotations

from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncpg

from .. import keyboards as kb
from .. import repo, texts
from ..logger import logger
from ..services import tariffs
from ..utils import fmt_price

router = Router()


async def _edit(cb: CallbackQuery, text: str, markup) -> None:
    # Текущее сообщение может быть фото (инфо-экран с картинкой): по фото edit_text
    # невозможен — заменяем сообщение (удаляем и шлём заново).
    if cb.message.photo:
        with suppress(TelegramBadRequest):
            await cb.message.delete()
        await cb.message.answer(text, reply_markup=markup)
    else:
        with suppress(TelegramBadRequest):  # «message is not modified» — не критично
            await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


# ── Экран выбора тарифа ───────────────────────────────────────────────────────
@router.callback_query(F.data.in_({kb.NAV_JOIN, kb.NAV_TARIFF}))
async def show_tariffs(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    # Уже оплатившим выбор тарифа не показываем (появится «Моя подписка», этап 3).
    if await repo.get_active_subscription(pool, cb.from_user.id) is not None:
        await cb.answer("У тебя уже есть активная подписка.", show_alert=True)
        return

    await repo.set_fsm_state(pool, cb.from_user.id, "screen:tariffs")
    items = await tariffs.get_active_tariffs(pool)
    if not items:
        await _edit(cb, texts.TARIFF_NONE, kb.to_menu_kb())
        logger.info(f"🤖 Бот → @{cb.from_user.username or '—'}: тарифы (пусто)")
        return

    b = InlineKeyboardBuilder()
    for t in items:
        b.row(
            InlineKeyboardButton(
                text=f"{t['months']} {texts.period_short(t['unit'])} — {fmt_price(t['price'])} ₽",
                callback_data=f"buy:{t['id']}",
            )
        )
    b.row(InlineKeyboardButton(text="Назад", callback_data=kb.NAV_START))
    await _edit(cb, texts.TARIFF_CHOOSE, b.as_markup())
    logger.info(f"🤖 Бот → @{cb.from_user.username or '—'}: экран тарифов ({len(items)} шт.)")


# ── Сводка к оплате (кнопка оплаты — заглушка до этапа 2) ─────────────────────
@router.callback_query(F.data.startswith("buy:"))
async def show_summary(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tariff = await tariffs.get_tariff(pool, int(cb.data.split(":", 1)[1]))
    if tariff is None:
        await _edit(cb, texts.TARIFF_NONE, kb.to_menu_kb())
        return

    months, unit = tariff["months"], tariff["unit"]
    await repo.set_fsm_state(pool, cb.from_user.id, f"screen:summary:{tariff['id']}")

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="Перейти к оплате", callback_data=f"pay:create:{tariff['id']}"))
    b.row(InlineKeyboardButton(text="Назад", callback_data=kb.NAV_TARIFF))
    await _edit(cb, texts.tariff_summary(months, unit, fmt_price(tariff["price"])), b.as_markup())
    logger.info(
        f"🤖 Бот → @{cb.from_user.username or '—'}: сводка "
        f"{texts.period_phrase(months, unit)} = {fmt_price(tariff['price'])} ₽"
    )


# ── Заглушка оплаты (реальная оплата Продамус — этап 2) ───────────────────────
@router.callback_query(F.data.startswith("pay:create:"))
async def pay_stub(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    await _edit(cb, texts.SOON, kb.to_menu_kb())
    logger.info(f"🤖 Бот → @{cb.from_user.username or '—'}: заглушка оплаты (этап 1)")
