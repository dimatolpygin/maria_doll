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


async def set_user_blocked(pool: asyncpg.Pool, tg_id: int, blocked: bool) -> None:
    """Отметка «заблокировал бота» (ставится, когда Telegram вернул Forbidden)."""
    await pool.execute(
        "UPDATE users SET is_blocked = $2, updated_at = now() WHERE tg_id = $1",
        tg_id, blocked,
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
    promo_id: int | None = None,
) -> asyncpg.Record:
    """Создаёт платёж в статусе pending и возвращает его запись."""
    return await pool.fetchrow(
        """
        INSERT INTO payments
            (order_num, tg_id, tariff_id, months, unit, amount, pay_url, kind,
             promo_id, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending')
        RETURNING *
        """,
        order_num, tg_id, tariff_id, months, unit, amount, pay_url, kind, promo_id,
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
            # Промокод применён — фиксируем активацию (лимит/история) и инкрементим
            # счётчик. Только при первой активации платежа (идемпотентно): повторный
            # вебхук сюда не заходит (status уже 'succeeded' выше). Уникум (promo,tg)
            # защищает от повторного учёта одним пользователем.
            if pay["promo_id"] is not None:
                claimed = await conn.fetchrow(
                    """
                    INSERT INTO promo_redemptions (promo_id, tg_id, payment_id)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (promo_id, tg_id) DO NOTHING
                    RETURNING id
                    """,
                    pay["promo_id"], pay["tg_id"], pay["id"],
                )
                if claimed is not None:
                    await conn.execute(
                        "UPDATE promo_codes SET used_count = used_count + 1, "
                        "updated_at = now() WHERE id = $1",
                        pay["promo_id"],
                    )
            return sub, True


# ── Промокоды (этап 5) ────────────────────────────────────────────────────────
async def get_promo_by_code(pool: asyncpg.Pool, code: str) -> asyncpg.Record | None:
    """Промокод по нормализованному коду (или None)."""
    return await pool.fetchrow("SELECT * FROM promo_codes WHERE code = $1", code)


async def get_promo(pool: asyncpg.Pool, promo_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow("SELECT * FROM promo_codes WHERE id = $1", promo_id)


async def user_redeemed_promo(
    pool: asyncpg.Pool, promo_id: int, tg_id: int
) -> bool:
    """Применял ли пользователь этот промокод раньше (лимит: один код — одна активация)."""
    row = await pool.fetchrow(
        "SELECT 1 FROM promo_redemptions WHERE promo_id = $1 AND tg_id = $2",
        promo_id, tg_id,
    )
    return row is not None


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


# ══════════════════════════════════════════════════════════════════════════════
# Запросы веб-админки (этап 6). Веб-процесс отдельный от бота, но ходит в ту же БД
# через общий пул. Фактические действия в Telegram (кик из группы, рассылка) делает
# бот — веб только пишет в общую БД, бот подхватывает фоновыми джобами.
# ══════════════════════════════════════════════════════════════════════════════

# ── Подписки / участники ──────────────────────────────────────────────────────
async def list_active_subscriptions(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Активные (не истёкшие) подписки с данными пользователя — для списка в админке."""
    return await pool.fetch(
        """
        SELECT s.id, s.tg_id, s.price, s.end_date, s.months, s.unit,
               u.username, u.first_name
        FROM subscriptions s
        JOIN users u ON u.tg_id = s.tg_id
        WHERE s.status = 'active' AND s.end_date > now()
        ORDER BY s.end_date
        """
    )


async def get_subscription(pool: asyncpg.Pool, sub_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow("SELECT * FROM subscriptions WHERE id = $1", sub_id)


async def set_subscription_end_date(
    pool: asyncpg.Pool, sub_id: int, end_date: datetime
) -> None:
    """Ручное продление из админки: двигаем окончание активной подписки."""
    await pool.execute(
        "UPDATE subscriptions SET end_date = $2 WHERE id = $1", sub_id, end_date
    )


async def disable_subscription_via_expiry(
    pool: asyncpg.Pool, sub_id: int
) -> int | None:
    """Отключение подписки из админки: ставим окончание «сейчас».

    Саму подписку не помечаем expired и из группы не кикаем прямо здесь — это сделает
    фоновая проверка окончаний бота (`expire_due_subscriptions` + кик) на ближайшем
    проходе. Возвращает tg_id отключённого (или None, если подписки нет/уже неактивна).
    """
    row = await pool.fetchrow(
        """
        UPDATE subscriptions SET end_date = now()
        WHERE id = $1 AND status = 'active' AND end_date > now()
        RETURNING tg_id
        """,
        sub_id,
    )
    return row["tg_id"] if row else None


async def add_subscription(
    pool: asyncpg.Pool,
    tg_id: int,
    tariff_id: int | None,
    price: Decimal,
    months: int,
    unit: str,
    end_date: datetime,
    *,
    status: str = "active",
) -> int:
    """Ручная выдача подписки из админки (без платежа). Возвращает id подписки."""
    return await pool.fetchval(
        """
        INSERT INTO subscriptions
            (tg_id, tariff_id, price, months, unit, start_date, end_date, status)
        VALUES ($1, $2, $3, $4, $5, now(), $6, $7)
        RETURNING id
        """,
        tg_id, tariff_id, price, months, unit, end_date, status,
    )


async def get_user(pool: asyncpg.Pool, tg_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT tg_id, username, first_name, email, is_blocked FROM users WHERE tg_id = $1",
        tg_id,
    )


async def search_users(pool: asyncpg.Pool, term: str) -> list[asyncpg.Record]:
    """Поиск пользователей по username/имени (частичное совпадение, до 25)."""
    like = f"%{term}%"
    return await pool.fetch(
        """
        SELECT tg_id, username, first_name FROM users
        WHERE username ILIKE $1 OR first_name ILIKE $1
        ORDER BY first_name NULLS LAST
        LIMIT 25
        """,
        like,
    )


async def get_user_events(
    pool: asyncpg.Pool, tg_id: int, limit: int = 20
) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT event, created_at FROM user_events WHERE tg_id = $1 "
        "ORDER BY id DESC LIMIT $2",
        tg_id, limit,
    )


async def get_fsm_stuck(pool: asyncpg.Pool, limit: int = 30) -> list[asyncpg.Record]:
    """Последние FSM-состояния пользователей (свежие сверху) — «кто на каком экране»."""
    return await pool.fetch(
        """
        SELECT f.tg_id, f.state, f.updated_at, u.username, u.first_name
        FROM fsm_states f
        LEFT JOIN users u ON u.tg_id = f.tg_id
        WHERE f.state IS NOT NULL
        ORDER BY f.updated_at DESC
        LIMIT $1
        """,
        limit,
    )


# ── Тарифы (фиксированные) — CRUD из админки ──────────────────────────────────
async def get_all_tariffs(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Все тарифы (включая выключенные) в порядке показа."""
    return await pool.fetch(
        "SELECT id, months, unit, price, title, is_active, sort_order "
        "FROM tariffs ORDER BY sort_order, months"
    )


async def create_tariff(
    pool: asyncpg.Pool, *, months: int, unit: str, price: Decimal,
    title: str | None, sort_order: int,
) -> int:
    return await pool.fetchval(
        """
        INSERT INTO tariffs (months, unit, price, title, sort_order)
        VALUES ($1, $2, $3, $4, $5) RETURNING id
        """,
        months, unit, price, title, sort_order,
    )


async def update_tariff(
    pool: asyncpg.Pool, tariff_id: int, *, months: int, unit: str, price: Decimal,
    title: str | None, is_active: bool, sort_order: int,
) -> None:
    await pool.execute(
        """
        UPDATE tariffs
        SET months = $2, unit = $3, price = $4, title = $5,
            is_active = $6, sort_order = $7
        WHERE id = $1
        """,
        tariff_id, months, unit, price, title, is_active, sort_order,
    )


async def set_tariff_active(pool: asyncpg.Pool, tariff_id: int, active: bool) -> None:
    await pool.execute(
        "UPDATE tariffs SET is_active = $2 WHERE id = $1", tariff_id, active
    )


async def delete_tariff(pool: asyncpg.Pool, tariff_id: int) -> bool:
    res = await pool.execute("DELETE FROM tariffs WHERE id = $1", tariff_id)
    return res.endswith("1")


# ── Промокоды — CRUD из админки ───────────────────────────────────────────────
async def get_all_promos(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch("SELECT * FROM promo_codes ORDER BY id DESC")


async def create_promo(
    pool: asyncpg.Pool, *, code: str, kind: str, value: Decimal,
    max_activations: int | None, expires_at: datetime | None,
) -> int | None:
    """Создаёт промокод. None — если код уже существует (гонка/дубль)."""
    return await pool.fetchval(
        """
        INSERT INTO promo_codes (code, kind, value, max_activations, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (code) DO NOTHING
        RETURNING id
        """,
        code, kind, value, max_activations, expires_at,
    )


async def set_promo_active(pool: asyncpg.Pool, promo_id: int, active: bool) -> None:
    await pool.execute(
        "UPDATE promo_codes SET is_active = $2, updated_at = now() WHERE id = $1",
        promo_id, active,
    )


async def delete_promo(pool: asyncpg.Pool, promo_id: int) -> bool:
    res = await pool.execute("DELETE FROM promo_codes WHERE id = $1", promo_id)
    return res.endswith("1")


# ── Экраны и кнопки — правки из админки ───────────────────────────────────────
async def upsert_screen_text(pool: asyncpg.Pool, key: str, body: str | None) -> None:
    """Своя версия текста экрана (NULL body → дефолт из реестра). Фото не трогаем."""
    await pool.execute(
        """
        INSERT INTO screen_texts (key, body) VALUES ($1, $2)
        ON CONFLICT (key) DO UPDATE SET body = EXCLUDED.body, updated_at = now()
        """,
        key, body,
    )


async def upsert_screen_photo(pool: asyncpg.Pool, key: str, photo_url: str | None) -> None:
    """Картинка экрана (URL из S3; NULL → без фото). Текст не трогаем."""
    await pool.execute(
        """
        INSERT INTO screen_texts (key, photo_url) VALUES ($1, $2)
        ON CONFLICT (key) DO UPDATE SET photo_url = EXCLUDED.photo_url, updated_at = now()
        """,
        key, photo_url,
    )


async def upsert_menu_button(
    pool: asyncpg.Pool, key: str, label: str | None, is_visible: bool
) -> None:
    await pool.execute(
        """
        INSERT INTO menu_buttons (key, label, is_visible) VALUES ($1, $2, $3)
        ON CONFLICT (key) DO UPDATE
        SET label = EXCLUDED.label, is_visible = EXCLUDED.is_visible, updated_at = now()
        """,
        key, label, is_visible,
    )


# ── Рассылки (очередь веб → бот-джоб) ─────────────────────────────────────────
AUDIENCE_ALL = "all"
AUDIENCE_ACTIVE = "active"
AUDIENCE_FORMER = "former"
AUDIENCE_NEVER = "never"

# Сегмент → SQL-условие отбора tg_id из users (u — алиас users).
_AUDIENCE_WHERE = {
    AUDIENCE_ALL: "TRUE",
    AUDIENCE_ACTIVE:
        "EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = u.tg_id "
        "AND s.status = 'active' AND s.end_date > now())",
    AUDIENCE_FORMER:
        "EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = u.tg_id) "
        "AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = u.tg_id "
        "AND s.status = 'active' AND s.end_date > now())",
    AUDIENCE_NEVER:
        "NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.tg_id = u.tg_id)",
}


async def create_broadcast(
    pool: asyncpg.Pool, *, audience: str, body: str | None,
    photos: list[dict] | None = None, created_by: str | None,
) -> int:
    """Ставит рассылку в очередь. photos — список {"url": ...} (публичные S3-ссылки)."""
    return await pool.fetchval(
        """
        INSERT INTO broadcasts (audience, body, photos, created_by)
        VALUES ($1, $2, $3::json, $4) RETURNING id
        """,
        audience, body, json.dumps(photos or [], ensure_ascii=False), created_by,
    )


async def list_broadcasts(pool: asyncpg.Pool, limit: int = 50) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT * FROM broadcasts ORDER BY id DESC LIMIT $1", limit
    )


async def claim_next_broadcast(pool: asyncpg.Pool) -> asyncpg.Record | None:
    """Атомарно берёт одну pending-рассылку в работу (pending → sending).

    SKIP LOCKED защищает от двойной отправки при гонке проходов джоба.
    """
    return await pool.fetchrow(
        """
        UPDATE broadcasts SET status = 'sending', started_at = now()
        WHERE id = (
            SELECT id FROM broadcasts WHERE status = 'pending'
            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """
    )


async def broadcast_recipients(pool: asyncpg.Pool, audience: str) -> list[int]:
    """Список tg_id получателей рассылки по сегменту (только не заблокировавшие бота)."""
    where = _AUDIENCE_WHERE.get(audience, "FALSE")
    rows = await pool.fetch(
        f"SELECT u.tg_id FROM users u WHERE u.is_blocked = false AND ({where})"
    )
    return [r["tg_id"] for r in rows]


async def set_broadcast_total(pool: asyncpg.Pool, bid: int, total: int) -> None:
    await pool.execute("UPDATE broadcasts SET total = $2 WHERE id = $1", bid, total)


async def finish_broadcast(
    pool: asyncpg.Pool, bid: int, *, sent: int, blocked: int, failed: int
) -> None:
    await pool.execute(
        """
        UPDATE broadcasts
        SET status = 'done', sent = $2, blocked = $3, failed = $4, finished_at = now()
        WHERE id = $1
        """,
        bid, sent, blocked, failed,
    )
