"""Раздел админки «Настройки» (этап 6).

На одной странице:
  - напоминания о продлении (единица + пороги early/soon/last);
  - ссылка поддержки (кнопка «Перейти» у пользователя);
  - FSM-диагностика «кто на каком экране» (read-only).

Все значения живут в bot_settings (services.app_settings) — тот же источник истины,
что у бота. Напоминания и ссылку бот читает из БД на каждом проходе/рендере, поэтому
правки применяются сразу, без рестарта. Все POST — через PRG-redirect.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import repo
from ..config import settings
from ..db import get_pool
from ..logger import logger
from ..services import app_settings
from ..texts import UNIT_LABELS
from .deps import current_admin, templates

router = APIRouter()

_REM_KINDS = (
    ("early", "Раннее («осталось N»)"),
    ("soon", "«Скоро заканчивается»"),
    ("last", "Последний период"),
)

# Человеческие подписи экранов (коды state из repo.set_fsm_state).
_FSM_LABELS = {
    "screen:start": "Главный экран (/start)",
    "screen:menu": "Главное меню",
    "screen:about": "Что внутри клуба",
    "screen:rules": "Правила участия",
    "screen:support": "Поддержка",
    "screen:tariffs": "Выбор тарифа",
    "screen:renew": "Продление подписки",
    "screen:mysub": "Моя подписка",
    "screen:pay:pending": "Оплата — ожидание оплаты",
    "screen:pay:success": "Оплата — успех",
    "screen:pay:canceled": "Оплата — отменена",
    "screen:promo": "Ввод промокода",
    "screen:promo:applied": "Промокод применён",
}

_FSM_PREFIXES = (
    ("screen:summary:", "Оформление заказа (сводка)"),
)


def _fsm_label(state: str | None) -> str:
    if not state:
        return "—"
    if state in _FSM_LABELS:
        return _FSM_LABELS[state]
    for prefix, label in _FSM_PREFIXES:
        if state.startswith(prefix):
            return label
    return state


def _ago(dt: datetime, now: datetime) -> str:
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 3600:
        return f"{secs // 60} мин назад"
    if secs < 86400:
        return f"{secs // 3600} ч назад"
    return f"{secs // 86400} дн назад"


def _normalize_support_url(raw: str) -> str | None:
    """Ввод админа → валидный URL для кнопки. None — не распознано.

    @username / голый username → https://t.me/username; t.me/... → https://...;
    http(s)://… и tg://… — как есть.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith(("https://", "http://", "tg://")):
        return raw
    if raw.startswith("t.me/"):
        return "https://" + raw
    if raw.startswith("@"):
        raw = raw[1:]
    if re.fullmatch(r"[A-Za-z0-9_]{4,32}", raw):
        return "https://t.me/" + raw
    return None


async def _overview(request: Request, *, ok: str | None = None, error: str | None = None,
                    status: int = 200):
    pool = get_pool()
    cfg = await app_settings.reminder_config(pool)
    support = await app_settings.support_url(pool)
    rows = await repo.get_fsm_stuck(pool)
    now = datetime.now(timezone.utc)
    fsm = [
        {
            "screen": _fsm_label(r["state"]),
            "username": f"@{r['username']}" if r["username"] else "—",
            "name": r["first_name"] or "",
            "tg_id": r["tg_id"],
            "ago": _ago(r["updated_at"], now),
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "active": "settings", "admin": request.session.get("admin"),
            "unit": cfg["unit"], "unit_labels": UNIT_LABELS, "offsets": cfg["offsets"],
            "rem_kinds": _REM_KINDS,
            "check_interval": settings.reminder_check_interval_min,
            "support_url": support,
            "fsm": fsm, "ok": ok, "error": error,
        },
        status_code=status,
    )


@router.get("/settings")
async def settings_page(request: Request, ok: str | None = None, error: str | None = None):
    current_admin(request)
    return await _overview(request, ok=ok, error=error)


@router.post("/settings/reminders")
async def settings_reminders(
    request: Request,
    unit: str = Form(...),
    early: str = Form(...),
    soon: str = Form(...),
    last: str = Form(...),
):
    current_admin(request)
    pool = get_pool()
    if unit not in app_settings.VALID_UNITS:
        return await _overview(request, error="Неизвестная единица напоминаний.", status=400)
    values: dict[str, int] = {}
    for kind, raw in (("early", early), ("soon", soon), ("last", last)):
        raw = (raw or "").strip()
        if not raw.isdigit():
            return await _overview(
                request, error="Пороги напоминаний — целые числа (0 или больше).", status=400
            )
        values[kind] = int(raw)
    await app_settings.set_reminder_unit(pool, unit)
    for kind, value in values.items():
        await app_settings.set_reminder_offset(pool, kind, value)
    logger.info(
        "Админка: напоминания unit={} early={} soon={} last={}",
        unit, values["early"], values["soon"], values["last"],
    )
    return RedirectResponse("/settings?ok=Напоминания сохранены.", status_code=303)


@router.post("/settings/support")
async def settings_support(request: Request, url: str = Form("")):
    current_admin(request)
    pool = get_pool()
    raw = (url or "").strip()
    if not raw:
        await app_settings.set_support_url(pool, "")
        logger.info("Админка: ссылка поддержки очищена")
        return RedirectResponse(
            "/settings?ok=Ссылка поддержки очищена — кнопка «Перейти» скрыта.",
            status_code=303,
        )
    normalized = _normalize_support_url(raw)
    if not normalized:
        return await _overview(
            request,
            error="Не похоже на ссылку. Укажите @username или ссылку вида https://t.me/…",
            status=400,
        )
    await app_settings.set_support_url(pool, normalized)
    logger.info("Админка: ссылка поддержки = {}", normalized)
    return RedirectResponse("/settings?ok=Ссылка поддержки сохранена.", status_code=303)
