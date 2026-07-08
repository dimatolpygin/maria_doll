"""Промокоды: нормализация кода, валидация и расчёт цены (этап 5).

Логика вынесена без БД/сети — её удобно тестировать. Валидация различает статусы:
ok / not_found / expired / exhausted / already_used. Расчёт цены поддерживает два
типа кода:
  · percent — скидка % от цены выбранного тарифа;
  · fixed   — фиксированная итоговая цена в ₽ за период (спец-цена).
Тарифы фиксированные (цена за период), поэтому промо разово удешевляет покупку —
за подпиской спец-цена не закрепляется (продления — по обычным тарифам).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

# Статусы валидации.
VALID = "ok"
NOT_FOUND = "not_found"
EXPIRED = "expired"
EXHAUSTED = "exhausted"
ALREADY_USED = "already_used"

# Типы кодов.
KIND_PERCENT = "percent"
KIND_FIXED = "fixed"


def normalize_code(raw: str | None) -> str:
    """Код к каноничному виду: без пробелов по краям, верхний регистр."""
    return (raw or "").strip().upper()


def validate(promo, *, now: datetime, already_used: bool) -> str:
    """Статус промокода. promo — запись из БД или None."""
    if promo is None:
        return NOT_FOUND
    if not promo["is_active"]:
        return EXPIRED  # деактивированный код больше не действует
    if promo["expires_at"] is not None and promo["expires_at"] <= now:
        return EXPIRED
    if (
        promo["max_activations"] is not None
        and promo["used_count"] >= promo["max_activations"]
    ):
        return EXHAUSTED
    if already_used:
        return ALREADY_USED
    return VALID


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_amount(promo, *, base_price: Decimal) -> Decimal:
    """Итоговая сумма к оплате по промокоду для выбранного тарифа.

    base_price — обычная цена тарифа. Для percent — скидка % от неё; для fixed —
    спец-цена из промокода (независимо от тарифа). Никогда не ниже нуля.
    """
    value: Decimal = promo["value"]
    if promo["kind"] == KIND_PERCENT:
        factor = (Decimal(100) - value) / Decimal(100)
        amount = _money(Decimal(base_price) * factor)
    else:  # fixed
        amount = _money(value)
    return max(amount, Decimal("0.00"))
