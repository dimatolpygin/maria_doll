"""Сервис тарифов: фиксированные тарифы подписки.

В ОТЛИЧИЕ от аналога (скрытые ступени по числу мест) — простые фиксированные тарифы
из таблицы `tariffs`. Читаем прямым запросом при каждом показе, поэтому правка цены
или состава из админки отражается сразу, без рестарта и без инвалидации кеша.
"""
from __future__ import annotations

from decimal import Decimal

import asyncpg

from .. import repo


def _to_tariff(r: asyncpg.Record) -> dict:
    return {
        "id": r["id"],
        "months": r["months"],
        "unit": r["unit"],
        "price": Decimal(r["price"]),
        "title": r["title"],
    }


async def get_active_tariffs(pool: asyncpg.Pool) -> list[dict]:
    """Активные тарифы в порядке показа."""
    rows = await repo.get_active_tariffs(pool)
    return [_to_tariff(r) for r in rows]


async def get_tariff(pool: asyncpg.Pool, tariff_id: int) -> dict | None:
    """Один тариф по id (или None)."""
    r = await repo.get_tariff(pool, tariff_id)
    return _to_tariff(r) if r is not None else None
