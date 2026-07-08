"""Оркестрация оплаты Продамус (разовый платёж, этап 2).

Поток:
  1. start_payment — по выбранному тарифу собрать платёжную ссылку payform,
     сохранить платёж (pending), вернуть ссылку пользователю.
  2. handle_webhook — принять уведомление Продамуса: проверить Hmac-подпись,
     при payment_status=success идемпотентно активировать подписку и один раз
     уведомить пользователя об успешной оплате.

Подтверждение — вебхуком (решение заказчицы): Продамус шлёт POST на наш
HTTPS-эндпоинт (см. src/webapp.py). Архитектура заложена под будущие реккуренты
(этап 8): подписочные уведомления придут в тот же handle_webhook.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import asyncpg
from aiogram import Bot
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import keyboards as kb
from .. import repo, texts
from ..config import settings
from ..logger import logger
from ..utils import fmt_price
from . import prodamus, tariffs


def _order_num(tg_id: int) -> str:
    """Уникальный идентификатор заказа: отдаём Продамусу, он вернёт его в вебхуке."""
    return f"md-{tg_id}-{uuid.uuid4().hex[:12]}"


def _return_url() -> str:
    """Куда Продамус вернёт пользователя. Явный URL из настроек или ссылка на бота."""
    if settings.prodamus_return_url:
        return settings.prodamus_return_url
    return ""


async def start_payment(
    pool: asyncpg.Pool, *, tg_id: int, tariff_id: int
) -> dict | None:
    """Создаёт платёж под выбранный тариф и возвращает ссылку на оплату.

    None — если тариф не найден или Продамус не настроен (нет адреса payform).
    """
    tariff = await tariffs.get_tariff(pool, tariff_id)
    if tariff is None or not settings.prodamus_form_url:
        return None

    months: int = tariff["months"]
    unit: str = tariff["unit"]
    amount: Decimal = tariff["price"]
    order_num = _order_num(tg_id)
    # Продление, если у пользователя уже есть активная подписка (иначе первичная покупка).
    is_renewal = await repo.get_active_subscription(pool, tg_id) is not None
    kind = "renewal" if is_renewal else "purchase"
    product_name = f"Подписка в клуб — {texts.period_phrase(months, unit)}"

    params = prodamus.build_link_params(
        order_id=order_num,
        product_name=product_name,
        price=f"{amount:.2f}",
        customer_extra=f"tg_id={tg_id}",
        url_return=_return_url(),
        url_success=_return_url(),
        do="pay",
        # Демо-режим: платёж без реального списания (передаём только когда включён).
        extra={"demo_mode": "1"} if settings.prodamus_demo_mode else None,
    )
    pay_url = prodamus.build_payment_url(
        settings.prodamus_form_url, params, secret=settings.prodamus_secret_key or None
    )

    await repo.create_payment(
        pool,
        order_num=order_num,
        tg_id=tg_id,
        tariff_id=tariff_id,
        months=months,
        unit=unit,
        amount=amount,
        pay_url=pay_url,
        kind=kind,
    )
    logger.info(
        f"💳 Платёж создан ({kind}): tg_id={tg_id}, {texts.period_phrase(months, unit)} = "
        f"{fmt_price(amount)} ₽, order_num={order_num}"
    )
    return {
        "order_num": order_num,
        "pay_url": pay_url,
        "amount": amount,
        "months": months,
        "unit": unit,
    }


async def handle_webhook(
    pool: asyncpg.Pool,
    bot: Bot,
    form_items: list[tuple[str, str]],
    sign_header: str | None,
) -> bool:
    """Обрабатывает уведомление Продамуса. True → ответить HTTP 200.

    False (подпись не сошлась / Продамус не настроен) → вернуть код ≠ 200, тогда
    Продамус повторит попытку. Неуспешные статусы (отмена/отказ) подтверждаем 200,
    чтобы прекратить ретраи, но подписку не создаём.
    """
    secret = settings.prodamus_secret_key
    if not secret:
        logger.error("Вебхук Продамуса пришёл, но PRODAMUS_SECRET_KEY не задан — отклоняю")
        return False

    data = prodamus.parse_webhook_form(form_items)
    if not prodamus.verify(data, secret, sign_header):
        logger.warning(
            f"Вебхук Продамуса: подпись НЕ совпала (order_num={data.get('order_num')}) — отклоняю"
        )
        return False

    order_num = data.get("order_num") or data.get("order_id")
    status = data.get("payment_status")
    if not order_num:
        logger.warning("Вебхук Продамуса: нет order_num/order_id — игнорирую")
        return True  # подпись валидна, но обрабатывать нечего — не просим ретраи

    if status != "success":
        logger.info(f"Вебхук Продамуса: order_num={order_num}, статус '{status}' — не активирую")
        return True

    payment = await repo.get_payment_by_order_num(pool, order_num)
    if payment is None:
        logger.warning(f"Вебхук Продамуса: платёж order_num={order_num} не найден")
        return True

    # Сверка суммы — защита от подмены (подпись уже проверена, это доп. контроль).
    paid = data.get("sum")
    if paid is not None and Decimal(str(paid)) != Decimal(payment["amount"]):
        logger.warning(
            f"Вебхук Продамуса: сумма расходится (пришло {paid}, ожидалось "
            f"{payment['amount']}) для order_num={order_num} — активирую по подписи"
        )

    sub, created = await repo.activate_payment(
        pool,
        order_num=order_num,
        prodamus_order_id=data.get("order_id"),
        payment_type=data.get("payment_type"),
        raw=data,
    )
    if created and sub is not None:
        logger.info(
            f"✅ Подписка #{sub['id']} активирована оплатой "
            f"(tg_id={payment['tg_id']}, order_num={order_num})"
        )
        await _notify_success(bot, payment["tg_id"], sub)
    else:
        logger.info(f"↩️ Повторный вебхук order_num={order_num} — подписка уже активна")
    return True


def _success_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    if settings.club_chat_invite_url:
        b.row(InlineKeyboardButton(
            text="Перейти в закрытый чат", url=settings.club_chat_invite_url
        ))
    b.row(InlineKeyboardButton(text="В главное меню", callback_data=kb.NAV_MENU))
    return b


async def _notify_success(bot: Bot, tg_id: int, sub: asyncpg.Record) -> None:
    text = texts.pay_success(
        fmt_price(sub["price"]), sub["start_date"], sub["end_date"]
    )
    try:
        await bot.send_message(tg_id, text, reply_markup=_success_kb().as_markup())
        logger.info(f"🤖 Бот → tg_id={tg_id}: оплата подтверждена, подписка активна")
    except Exception as e:  # noqa: BLE001 — пользователь мог заблокировать бота
        logger.warning(f"Не удалось уведомить tg_id={tg_id} об активации: {e}")
