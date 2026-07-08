"""Раздел админки «Тарифы» (этап 6) — фиксированные тарифы подписки.

В ОТЛИЧИЕ от аналога (ценовые ступени по числу мест + матрица периодов) — простой
фиксированный набор тарифов из таблицы `tariffs`: длительность (значение+единица),
цена за период, название, вкл/выкл, порядок показа. Бот читает тарифы прямым запросом
при каждом показе, поэтому правки из админки видны сразу, без рестарта и без кеша.
Все POST — через PRG-redirect.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import repo, texts
from ..db import get_pool
from ..logger import logger
from ..utils import fmt_price
from .deps import current_admin, templates

router = APIRouter()

# Единицы длительности (значение → подпись), порядок = порядок в форме.
UNITS: list[tuple[str, str]] = [
    ("month", "Месяц"), ("day", "День"), ("hour", "Час"), ("minute", "Минута"),
]
_UNIT_KEYS = {u for u, _ in UNITS}


def _parse_price(raw: str | None) -> Decimal | None:
    """Цена из строки (запятая/точка). Пусто/некорректная/≤0 → None."""
    raw = (raw or "").replace(",", ".").strip()
    if raw == "":
        return None
    try:
        price = Decimal(raw)
    except InvalidOperation:
        return None
    return price if price > 0 else None


async def _overview(request: Request, *, ok: str | None = None, error: str | None = None,
                    status: int = 200):
    pool = get_pool()
    rows = await repo.get_all_tariffs(pool)
    tariffs = [
        {
            "id": t["id"],
            "label": texts.period_phrase(t["months"], t["unit"]),
            "months": t["months"], "unit": t["unit"],
            "price": fmt_price(t["price"]),
            "title": t["title"] or "",
            "is_active": t["is_active"],
            "sort_order": t["sort_order"],
        }
        for t in rows
    ]
    return templates.TemplateResponse(
        request, "tariffs.html",
        {
            "active": "tariffs", "admin": request.session.get("admin"),
            "tariffs": tariffs, "units": UNITS, "ok": ok, "error": error,
        },
        status_code=status,
    )


@router.get("/tariffs")
async def tariffs_page(request: Request, ok: str | None = None, error: str | None = None):
    current_admin(request)
    return await _overview(request, ok=ok, error=error)


@router.post("/tariffs/create")
async def tariff_create(
    request: Request,
    months: str = Form(...),
    unit: str = Form(...),
    price: str = Form(...),
    title: str = Form(""),
    sort_order: str = Form("0"),
):
    current_admin(request)
    months_raw = (months or "").strip()
    if not months_raw.isdigit() or int(months_raw) <= 0:
        return await _overview(request, error="Длительность — целое число больше нуля.", status=400)
    if unit not in _UNIT_KEYS:
        return await _overview(request, error="Неизвестная единица длительности.", status=400)
    p = _parse_price(price)
    if p is None:
        return await _overview(request, error="Цена — число больше нуля.", status=400)
    sort_raw = (sort_order or "0").strip()
    sort = int(sort_raw) if sort_raw.lstrip("-").isdigit() else 0

    pool = get_pool()
    tid = await repo.create_tariff(
        pool, months=int(months_raw), unit=unit, price=p,
        title=(title or "").strip() or None, sort_order=sort,
    )
    logger.info("Админка: создан тариф #{} ({} {} — {})", tid, months_raw, unit, p)
    return RedirectResponse("/tariffs?ok=Тариф создан.", status_code=303)


@router.post("/tariffs/{tariff_id}/save")
async def tariff_save(
    request: Request,
    tariff_id: int,
    months: str = Form(...),
    unit: str = Form(...),
    price: str = Form(...),
    title: str = Form(""),
    sort_order: str = Form("0"),
):
    current_admin(request)
    months_raw = (months or "").strip()
    if not months_raw.isdigit() or int(months_raw) <= 0:
        return await _overview(request, error="Длительность — целое число больше нуля.", status=400)
    if unit not in _UNIT_KEYS:
        return await _overview(request, error="Неизвестная единица длительности.", status=400)
    p = _parse_price(price)
    if p is None:
        return await _overview(request, error="Цена — число больше нуля.", status=400)
    sort_raw = (sort_order or "0").strip()
    sort = int(sort_raw) if sort_raw.lstrip("-").isdigit() else 0

    pool = get_pool()
    t = await repo.get_tariff(pool, tariff_id)
    if t is None:
        return RedirectResponse("/tariffs?error=Тариф не найден.", status_code=303)
    # is_active сохраняем текущим (переключается отдельной кнопкой toggle).
    row = next((r for r in await repo.get_all_tariffs(pool) if r["id"] == tariff_id), None)
    is_active = row["is_active"] if row else True
    await repo.update_tariff(
        pool, tariff_id, months=int(months_raw), unit=unit, price=p,
        title=(title or "").strip() or None, is_active=is_active, sort_order=sort,
    )
    logger.info("Админка: тариф #{} сохранён", tariff_id)
    return RedirectResponse("/tariffs?ok=Тариф сохранён.", status_code=303)


@router.post("/tariffs/{tariff_id}/toggle")
async def tariff_toggle(request: Request, tariff_id: int):
    current_admin(request)
    pool = get_pool()
    row = next((r for r in await repo.get_all_tariffs(pool) if r["id"] == tariff_id), None)
    if row is not None:
        await repo.set_tariff_active(pool, tariff_id, not row["is_active"])
        logger.info("Админка: тариф #{} active={}", tariff_id, not row["is_active"])
    return RedirectResponse("/tariffs?ok=Сохранено.", status_code=303)


@router.post("/tariffs/{tariff_id}/delete")
async def tariff_delete(request: Request, tariff_id: int):
    current_admin(request)
    pool = get_pool()
    if await repo.delete_tariff(pool, tariff_id):
        logger.info("Админка: удалён тариф #{}", tariff_id)
    return RedirectResponse("/tariffs?ok=Тариф удалён.", status_code=303)
