"""Пользовательский флоу промокодов (этап 5).

Сводка тарифа → «Ввести промокод» → ввод кода (FSM, tariff_id в контексте) →
валидация. Валидный код показывает сводку со спец-ценой и ведёт к оплате по
промокоду (платёж с promo_id; активация учитывает применение в activate_payment).
Невалидный код даёт корректное сообщение (не найден / истёк / исчерпан / уже
использован) с возможностью ввести другой код или оплатить без промокода.
"""
from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncpg

from .. import keyboards as kb
from .. import repo, texts
from ..logger import logger
from ..services import payments, promo as promo_service, tariffs
from ..states import PromoStates
from ..utils import fmt_price

router = Router()

# Сообщение на каждый невалидный статус.
_FAIL_TEXTS = {
    promo_service.NOT_FOUND: texts.PROMO_NOT_FOUND,
    promo_service.EXPIRED: texts.PROMO_EXPIRED,
    promo_service.EXHAUSTED: texts.PROMO_EXHAUSTED,
    promo_service.ALREADY_USED: texts.PROMO_ALREADY_USED,
}


async def _edit(cb: CallbackQuery, text: str, markup) -> None:
    if cb.message.photo:
        with suppress(TelegramBadRequest):
            await cb.message.delete()
        await cb.message.answer(text, reply_markup=markup)
    else:
        with suppress(TelegramBadRequest):
            await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


async def _check_promo(pool: asyncpg.Pool, code: str, tg_id: int):
    """Возвращает (promo, status). promo — запись или None."""
    promo = await repo.get_promo_by_code(pool, code) if code else None
    already = (
        await repo.user_redeemed_promo(pool, promo["id"], tg_id)
        if promo is not None else False
    )
    status = promo_service.validate(
        promo, now=datetime.now(timezone.utc), already_used=already
    )
    return promo, status


# ── Ввод промокода ────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("promo:enter:"))
async def promo_enter(cb: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    tariff_id = int(cb.data.rsplit(":", 1)[1])
    await state.set_state(PromoStates.waiting_code)
    await state.update_data(tariff_id=tariff_id)
    await repo.set_fsm_state(pool, cb.from_user.id, "screen:promo")
    await _edit(cb, texts.PROMO_ENTER, kb.promo_enter_kb(tariff_id))
    logger.info(f"🤖 Бот → @{cb.from_user.username or '—'}: ввод промокода (тариф {tariff_id})")


@router.message(PromoStates.waiting_code)
async def promo_code_entered(
    message: Message, state: FSMContext, pool: asyncpg.Pool
) -> None:
    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    await state.clear()

    tariff = await tariffs.get_tariff(pool, tariff_id) if tariff_id else None
    if tariff is None:  # тариф исчез (деактивирован) — вернём к списку
        await message.answer(texts.TARIFF_NONE, reply_markup=kb.to_menu_kb())
        return

    code = promo_service.normalize_code(message.text)
    promo, status = await _check_promo(pool, code, message.from_user.id)
    if status != promo_service.VALID:
        await message.answer(_FAIL_TEXTS[status], reply_markup=kb.promo_fail_kb(tariff_id))
        logger.info(f"🤖 Бот → @{message.from_user.username or '—'}: промокод «{code}» — {status}")
        return

    amount = promo_service.compute_amount(promo, base_price=tariff["price"])
    await repo.set_fsm_state(pool, message.from_user.id, "screen:promo:applied")
    await message.answer(
        texts.promo_applied(
            tariff["months"], tariff["unit"],
            fmt_price(tariff["price"]), fmt_price(amount), code,
        ),
        reply_markup=kb.promo_applied_kb(promo["id"], tariff_id),
    )
    logger.info(
        f"🤖 Бот → @{message.from_user.username or '—'}: промокод «{code}» применён, "
        f"итого {fmt_price(amount)} ₽ (тариф {tariff_id})"
    )


# ── Оплата по промокоду ───────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("promo:buy:"))
async def promo_buy(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    _, _, promo_id_s, tariff_id_s = cb.data.split(":")
    promo_id, tariff_id = int(promo_id_s), int(tariff_id_s)

    # Перепроверка на момент оплаты: код мог истечь/исчерпаться между вводом и оплатой.
    promo = await repo.get_promo(pool, promo_id)
    code = promo["code"] if promo is not None else ""
    _, status = await _check_promo(pool, code, cb.from_user.id)
    if status != promo_service.VALID:
        await _edit(cb, _FAIL_TEXTS[status], kb.promo_fail_kb(tariff_id))
        logger.info(f"🤖 Бот → @{cb.from_user.username or '—'}: промокод при оплате — {status}")
        return

    result = await _start_promo_safe(pool, cb.from_user.id, tariff_id, promo_id)
    if result is None:
        await _edit(cb, texts.PAY_UNAVAILABLE, kb.to_menu_kb())
        logger.info(f"🤖 Бот → @{cb.from_user.username or '—'}: оплата по промокоду недоступна")
        return

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(
        text=f"Оплатить {fmt_price(result['amount'])} ₽", url=result["pay_url"]
    ))
    b.row(InlineKeyboardButton(text="Назад", callback_data=kb.NAV_TARIFF))
    await _edit(
        cb,
        texts.pay_link(
            texts.period_phrase(result["months"], result["unit"]),
            fmt_price(result["amount"]),
        ),
        b.as_markup(),
    )
    logger.info(
        f"🤖 Бот → @{cb.from_user.username or '—'}: ссылка на оплату по промокоду "
        f"{fmt_price(result['amount'])} ₽ (order_num={result['order_num']})"
    )


async def _start_promo_safe(pool, tg_id, tariff_id, promo_id):
    try:
        return await payments.start_payment(
            pool, tg_id=tg_id, tariff_id=tariff_id, promo_id=promo_id
        )
    except Exception as e:  # noqa: BLE001 — сбой оплаты не должен ронять хендлер
        logger.error(f"Не удалось создать платёж по промокоду для tg_id={tg_id}: {e}")
        return None
