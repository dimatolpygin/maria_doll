"""Запросы статистики для дашборда (этап 6).

Источник дохода — успешные платежи (`payments.status='succeeded'`), момент дохода —
`payments.paid_at` (туда пишется now() при активации, см. repo.activate_payment).
Весь доход — подписки (билетов/событий в этом проекте нет), поэтому разбивки по
направлениям нет — только суммарный доход и его динамика.

Все запросы — read-only агрегаты (чистый слой данных, без бизнес-логики).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import asyncpg


async def revenue_total(pool: asyncpg.Pool, start: datetime, end: datetime) -> Decimal:
    """Суммарный доход за [start, end)."""
    val = await pool.fetchval(
        """
        SELECT COALESCE(SUM(amount), 0) FROM payments
        WHERE status = 'succeeded' AND paid_at >= $1 AND paid_at < $2
        """,
        start, end,
    )
    return val or Decimal(0)


async def revenue_buckets(
    pool: asyncpg.Pool, start: datetime, end: datetime, granularity: str
) -> dict[datetime, Decimal]:
    """Доход по корзинам времени для графика: {начало_корзины(UTC): сумма}."""
    unit = granularity if granularity in ("day", "week", "month") else "day"
    rows = await pool.fetch(
        f"""
        SELECT date_trunc('{unit}', paid_at) AS bucket,
               COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE status = 'succeeded' AND paid_at >= $1 AND paid_at < $2
        GROUP BY bucket
        ORDER BY bucket
        """,
        start, end,
    )
    return {r["bucket"]: r["total"] for r in rows}


async def payments_count(pool: asyncpg.Pool, start: datetime, end: datetime) -> int:
    """Число успешных платежей за период (для среднего чека)."""
    val = await pool.fetchval(
        """
        SELECT count(*) FROM payments
        WHERE status = 'succeeded' AND paid_at >= $1 AND paid_at < $2
        """,
        start, end,
    )
    return int(val or 0)


async def first_payment_at(pool: asyncpg.Pool) -> datetime | None:
    """Дата первого успешного платежа — для режима «всё время»."""
    return await pool.fetchval(
        "SELECT min(paid_at) FROM payments WHERE status = 'succeeded'"
    )


async def active_subscribers(pool: asyncpg.Pool) -> int:
    """Число активных подписчиков на текущий момент."""
    val = await pool.fetchval(
        "SELECT count(*) FROM subscriptions WHERE status = 'active' AND end_date > now()"
    )
    return int(val or 0)


async def expiring_soon(pool: asyncpg.Pool, days: int = 7) -> int:
    """Сколько активных подписок закончится в ближайшие N дней."""
    val = await pool.fetchval(
        """
        SELECT count(*) FROM subscriptions
        WHERE status = 'active' AND end_date > now()
          AND end_date <= now() + ($1 || ' days')::interval
        """,
        str(days),
    )
    return int(val or 0)


async def subscriber_distribution(pool: asyncpg.Pool) -> dict[str, int]:
    """Распределение всех пользователей бота по статусу подписки (срез сейчас).

      · active — есть активная подписка (status='active' и end_date>now());
      · former — подписка была, но сейчас активной нет (просроченные/ушедшие);
      · none   — ни одной подписки в истории (запускали бота без подписки).
    """
    row = await pool.fetchrow(
        """
        WITH sub AS (
            SELECT tg_id,
                   bool_or(status = 'active' AND end_date > now()) AS has_active
            FROM subscriptions
            GROUP BY tg_id
        )
        SELECT
            count(*) FILTER (WHERE s.has_active) AS active,
            count(*) FILTER (WHERE s.tg_id IS NOT NULL AND NOT s.has_active) AS former,
            count(*) FILTER (WHERE s.tg_id IS NULL) AS none
        FROM users u
        LEFT JOIN sub s ON s.tg_id = u.tg_id
        """
    )
    return {"active": int(row["active"]), "former": int(row["former"]),
            "none": int(row["none"])}


async def registrations_buckets(
    pool: asyncpg.Pool, start: datetime, end: datetime, granularity: str
) -> dict[datetime, int]:
    """Новые пользователи бота по корзинам времени: {начало_корзины(UTC): count}."""
    unit = granularity if granularity in ("day", "week", "month") else "day"
    rows = await pool.fetch(
        f"""
        SELECT date_trunc('{unit}', created_at) AS bucket, count(*) AS cnt
        FROM users
        WHERE created_at >= $1 AND created_at < $2
        GROUP BY bucket
        ORDER BY bucket
        """,
        start, end,
    )
    return {r["bucket"]: int(r["cnt"]) for r in rows}


async def activity_buckets(
    pool: asyncpg.Pool, start: datetime, end: datetime, granularity: str
) -> dict[datetime, int]:
    """Активность в боте по корзинам: {начало_корзины(UTC): уникальных пользователей}."""
    unit = granularity if granularity in ("day", "week", "month") else "day"
    rows = await pool.fetch(
        f"""
        SELECT date_trunc('{unit}', created_at) AS bucket,
               count(DISTINCT tg_id) AS cnt
        FROM user_events
        WHERE created_at >= $1 AND created_at < $2
        GROUP BY bucket
        ORDER BY bucket
        """,
        start, end,
    )
    return {r["bucket"]: int(r["cnt"]) for r in rows}


async def active_users_total(pool: asyncpg.Pool, start: datetime, end: datetime) -> int:
    """Уникальных активных пользователей за весь период (gran-независимый итог для bar)."""
    val = await pool.fetchval(
        """
        SELECT count(DISTINCT tg_id) FROM user_events
        WHERE created_at >= $1 AND created_at < $2
        """,
        start, end,
    )
    return int(val or 0)
