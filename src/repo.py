"""Доступ к БД (asyncpg). Все запросы идут в нашу схему (search_path из db.py).

Этап 1: пользователи, FSM-состояние, журнал действий, переопределения экранов/кнопок,
тарифы. Этап 2: платежи и подписки (Продамус). Промокоды — на следующих этапах.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg

from .utils import add_period

# Сколько последних записей журнала действий хранить на пользователя.
_EVENTS_KEEP = 50


# ── Пользователи ──────────────────────────────────────────────────────────────
async def upsert_user(
    pool: asyncpg.Pool, tg_id: int, username: str | None, first_name: str | None
) -> None:
    await pool.execute(
        """
        INSERT INTO users (tg_id, username, first_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (tg_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            updated_at = now()
        """,
        tg_id, username, first_name,
    )


# ── FSM-состояние (где пользователь находится/застрял) ────────────────────────
async def set_fsm_state(pool: asyncpg.Pool, tg_id: int, state: str) -> None:
    await pool.execute(
        """
        INSERT INTO fsm_states (tg_id, state, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (tg_id) DO UPDATE
        SET state = EXCLUDED.state, updated_at = now()
        """,
        tg_id, state,
    )


# ── Журнал действий (путь пользователя для админки) ───────────────────────────
async def add_event(pool: asyncpg.Pool, tg_id: int, event: str) -> None:
    """Пишет действие в журнал и подрезает хвост старше последних N на пользователя."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_events (tg_id, event) VALUES ($1, $2)", tg_id, event
        )
        await conn.execute(
            """
            DELETE FROM user_events
            WHERE tg_id = $1 AND id NOT IN (
                SELECT id FROM user_events
                WHERE tg_id = $1 ORDER BY id DESC LIMIT $2
            )
            """,
            tg_id, _EVENTS_KEEP,
        )


# ── Переопределения текстов экранов ───────────────────────────────────────────
async def get_screen_overrides(pool: asyncpg.Pool) -> dict[str, dict]:
    """{key: {'body': str|None, 'photo_url': str|None}} — из таблицы screen_texts."""
    rows = await pool.fetch("SELECT key, body, photo_url FROM screen_texts")
    return {r["key"]: {"body": r["body"], "photo_url": r["photo_url"]} for r in rows}


# ── Переопределения кнопок меню ───────────────────────────────────────────────
async def get_menu_overrides(pool: asyncpg.Pool) -> dict[str, dict]:
    """{key: {'label': str|None, 'is_visible': bool}} — из таблицы menu_buttons."""
    rows = await pool.fetch("SELECT key, label, is_visible FROM menu_buttons")
    return {r["key"]: {"label": r["label"], "is_visible": r["is_visible"]} for r in rows}


# ── Тарифы (фиксированные) ────────────────────────────────────────────────────
async def get_active_tariffs(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Активные тарифы в порядке показа (sort_order, затем длительность)."""
    return await pool.fetch(
        """
        SELECT id, months, unit, price, title, sort_order
        FROM tariffs
        WHERE is_active = true
        ORDER BY sort_order, months
        """
    )


async def get_tariff(pool: asyncpg.Pool, tariff_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT id, months, unit, price, title FROM tariffs WHERE id = $1", tariff_id
    )


# ── Платежи (Продамус) ────────────────────────────────────────────────────────
async def create_payment(
    pool: asyncpg.Pool,
    *,
    order_num: str,
    tg_id: int,
    tariff_id: int | None,
    months: int,
    unit: str,
    amount: Decimal,
    pay_url: str,
    kind: str = "purchase",
) -> asyncpg.Record:
    """Создаёт платёж в статусе pending и возвращает его запись."""
    return await pool.fetchrow(
        """
        INSERT INTO payments
            (order_num, tg_id, tariff_id, months, unit, amount, pay_url, kind, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')
        RETURNING *
        """,
        order_num, tg_id, tariff_id, months, unit, amount, pay_url, kind,
    )


async def get_payment_by_order_num(
    pool: asyncpg.Pool, order_num: str
) -> asyncpg.Record | None:
    return await pool.fetchrow("SELECT * FROM payments WHERE order_num = $1", order_num)


async def activate_payment(
    pool: asyncpg.Pool,
    *,
    order_num: str,
    prodamus_order_id: str | None = None,
    payment_type: str | None = None,
    raw: dict | None = None,
) -> tuple[asyncpg.Record | None, bool]:
    """Идемпотентно активирует оплаченный платёж и создаёт подписку.

    Возвращает (подписка, создана_ли_только_что). Повторный вызов по тому же
    order_num (второй вебхук) не создаёт вторую подписку — вернёт (существующая,
    False). Если платёж не найден — (None, False). Всё под FOR UPDATE в транзакции.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            pay = await conn.fetchrow(
                "SELECT * FROM payments WHERE order_num = $1 FOR UPDATE", order_num
            )
            if pay is None:
                return None, False
            if pay["status"] == "succeeded":
                sub = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE payment_id = $1", pay["id"]
                )
                return sub, False

            now = datetime.now(timezone.utc)
            # Продление: если у пользователя есть действующая подписка, новый срок
            # продолжает её от текущей end_date — оплаченные дни не теряются. Иначе
            # (первичная покупка) отсчёт от now. start_date = момент оплаты (для показа).
            base_row = await conn.fetchrow(
                """
                SELECT max(end_date) AS end_date FROM subscriptions
                WHERE tg_id = $1 AND status = 'active' AND end_date > now()
                """,
                pay["tg_id"],
            )
            base = now
            if base_row is not None and base_row["end_date"] is not None:
                base = max(now, base_row["end_date"])
            end = add_period(base, pay["months"], pay["unit"])
            await conn.execute(
                """
                UPDATE payments
                SET status = 'succeeded', paid_at = now(),
                    prodamus_order_id = $2, payment_type = $3, raw = $4::jsonb
                WHERE id = $1
                """,
                pay["id"], prodamus_order_id, payment_type,
                json.dumps(raw, ensure_ascii=False) if raw is not None else None,
            )
            sub = await conn.fetchrow(
                """
                INSERT INTO subscriptions
                    (tg_id, payment_id, tariff_id, price, months, unit,
                     start_date, end_date, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active')
                RETURNING *
                """,
                pay["tg_id"], pay["id"], pay["tariff_id"], pay["amount"],
                pay["months"], pay["unit"], now, end,
            )
            return sub, True


# ── Подписки ──────────────────────────────────────────────────────────────────
async def get_active_subscription(
    pool: asyncpg.Pool, tg_id: int
) -> asyncpg.Record | None:
    """Активная (не истёкшая) подписка пользователя — или None."""
    return await pool.fetchrow(
        """
        SELECT * FROM subscriptions
        WHERE tg_id = $1 AND status = 'active' AND end_date > now()
        ORDER BY end_date DESC
        LIMIT 1
        """,
        tg_id,
    )


async def get_last_subscription(
    pool: asyncpg.Pool, tg_id: int
) -> asyncpg.Record | None:
    """Последняя по времени подписка пользователя (для экрана успеха)."""
    return await pool.fetchrow(
        "SELECT * FROM subscriptions WHERE tg_id = $1 ORDER BY id DESC LIMIT 1",
        tg_id,
    )


async def expire_due_subscriptions(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Помечает истёкшие активные подписки 'expired'. Возвращает строки (id, tg_id).

    Вызывается фоновой проверкой окончаний (этап 3): по возвращённым юзерам бот
    кикает из закрытой группы и шлёт уведомление «подписка закончилась».
    """
    return await pool.fetch(
        """
        UPDATE subscriptions
        SET status = 'expired'
        WHERE status = 'active' AND end_date <= now()
        RETURNING id, tg_id
        """
    )


# ── Рантайм-настройки (bot_settings) — правятся из админки на лету (этап 4/6) ──
async def get_settings(pool: asyncpg.Pool, keys: list[str]) -> dict[str, str]:
    """Значения настроек по списку ключей (отсутствующие ключи просто не попадут)."""
    rows = await pool.fetch(
        "SELECT key, value FROM bot_settings WHERE key = ANY($1::text[])", keys
    )
    return {r["key"]: r["value"] for r in rows}


async def set_setting(pool: asyncpg.Pool, key: str, value: str) -> None:
    await pool.execute(
        """
        INSERT INTO bot_settings(key, value)
        VALUES($1, $2)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        key, value,
    )


# ── Напоминания о продлении (этап 4) ──────────────────────────────────────────
async def get_subscriptions_for_reminders(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Активные ещё не истёкшие подписки + уже отправленные по ним типы напоминаний.

    Возвращает строки с полями подписки/пользователя и массивом `sent` (типы
    напоминаний, уже отправленные по этой подписке) — джоб по нему решает, что
    ещё нужно отправить.
    """
    return await pool.fetch(
        """
        SELECT s.id, s.tg_id, s.end_date, s.price, s.unit,
               u.username, u.first_name,
               COALESCE(
                   array_agg(r.kind) FILTER (WHERE r.kind IS NOT NULL),
                   ARRAY[]::text[]
               ) AS sent
        FROM subscriptions s
        JOIN users u ON u.tg_id = s.tg_id
        LEFT JOIN subscription_reminders r ON r.subscription_id = s.id
        WHERE s.status = 'active' AND s.end_date > now()
        GROUP BY s.id, u.username, u.first_name
        """
    )


async def claim_reminder(pool: asyncpg.Pool, subscription_id: int, kind: str) -> bool:
    """Атомарно «занимает» отправку напоминания (подписка, тип). True — занято нами.

    INSERT ... ON CONFLICT DO NOTHING: если строка уже была (напоминание этого типа
    отправлялось), вернётся False и сообщение повторно не уйдёт — защита от дублей
    при повторных прогонах планировщика и гонке параллельных проходов.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO subscription_reminders(subscription_id, kind)
        VALUES($1, $2)
        ON CONFLICT (subscription_id, kind) DO NOTHING
        RETURNING subscription_id
        """,
        subscription_id, kind,
    )
    return row is not None
