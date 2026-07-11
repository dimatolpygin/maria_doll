"""Экран «Моя подписка» (этап 3).

Показывает статус подписки: активная (со сроком действия и зафиксированной ценой)
или её отсутствие с предложением выбрать тариф. Кнопка вызывается из экранов
успешной оплаты и одобрения заявки в чат.
"""
from __future__ import annotations

from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
import asyncpg

from .. import keyboards as kb
from .. import repo, texts
from ..logger import logger
from ..utils import fmt_price

router = Router()


async def _edit(cb: CallbackQuery, text: str, markup) -> None:
    if cb.message.photo:
        with suppress(TelegramBadRequest):
            await cb.message.delete()
        await cb.message.answer(text, reply_markup=markup)
    else:
        with suppress(TelegramBadRequest):
            await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data == kb.NAV_MYSUB)
async def show_mysub(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    await repo.set_fsm_state(pool, cb.from_user.id, "screen:mysub")
    sub = await repo.get_active_subscription(pool, cb.from_user.id)
    if sub is None:
        await _edit(cb, texts.MYSUB_NONE, kb.to_menu_kb())
        logger.info(f"🤖 Бот → @{cb.from_user.username or '—'}: моя подписка (нет активной)")
        return

    await _edit(
        cb,
        texts.mysub_active(fmt_price(sub["price"]), sub["start_date"], sub["end_date"]),
        kb.mysub_kb(),
    )
    logger.info(
        f"🤖 Бот → @{cb.from_user.username or '—'}: моя подписка "
        f"(активна до {sub['end_date']:%d.%m.%Y})"
    )


@router.message(Command("sub"))
async def cmd_sub(message: Message, pool: asyncpg.Pool) -> None:
    """Статус подписки командой /sub (тот же экран «Моя подписка», но новым
    сообщением). Активному — срок и кнопки чат/продлить/меню; иначе — предложение
    оформить."""
    u = message.from_user
    await repo.set_fsm_state(pool, u.id, "screen:mysub")
    sub = await repo.get_active_subscription(pool, u.id)
    if sub is None:
        await message.answer(texts.MYSUB_NONE, reply_markup=kb.to_menu_kb())
        logger.info(f"🤖 Бот → @{u.username or '—'}: /sub (нет активной)")
        return
    await message.answer(
        texts.mysub_active(fmt_price(sub["price"]), sub["start_date"], sub["end_date"]),
        reply_markup=kb.mysub_kb(),
    )
    logger.info(
        f"🤖 Бот → @{u.username or '—'}: /sub (активна до {sub['end_date']:%d.%m.%Y})"
    )
