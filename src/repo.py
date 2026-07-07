"""Доступ к БД (asyncpg). Все запросы идут в нашу схему (search_path из db.py).

Этап 1: пользователи, FSM-состояние, журнал действий, переопределения экранов/кнопок,
тарифы. Подписки/оплаты/промокоды добавятся на следующих этапах.
"""
from __future__ import annotations

import asyncpg

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


# ── Подписки (заглушка до этапа 2) ────────────────────────────────────────────
async def get_active_subscription(pool: asyncpg.Pool, tg_id: int):
    """Активная подписка пользователя. Этап 1: таблицы подписок ещё нет → None.

    Появится на этапе 2 вместе с оплатой Продамус.
    """
    return None
