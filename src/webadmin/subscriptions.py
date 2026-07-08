"""Раздел админки «Подписки» + участники (этап 6).

Список активных подписок со сроками, ручное продление (на N единиц) и отключение.
Поиск участника по tg_id/@username/имени → карточка (подписка, даты, история
действий), ручная выдача подписки и аннулирование.

Веб-процесс отдельный от бота: продление/отключение/выдача — это запись в общую БД,
а фактический кик из группы делает фоновая проверка окончаний бота
(disable_subscription_via_expiry: end_date=now() → бот кикает на ближайшем проходе).
Ручную подписку бот применяет при join-request (проверяет активную подписку).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import repo, texts
from ..db import get_pool
from ..logger import logger
from ..utils import add_period, fmt_price
from .deps import current_admin, templates

router = APIRouter()

# Единицы продления (как у длительностей бота).
UNIT_LABELS = {"month": "мес.", "day": "дн.", "hour": "ч.", "minute": "мин."}
VALID_UNITS = tuple(UNIT_LABELS)


async def _render(request: Request, *, error: str | None = None, ok: str | None = None,
                  results: list | None = None, results_query: str | None = None,
                  status: int = 200):
    pool = get_pool()
    rows = await repo.list_active_subscriptions(pool)
    now = datetime.now(timezone.utc)
    subs = []
    for r in rows:
        left = r["end_date"] - now
        subs.append({
            "id": r["id"], "tg_id": r["tg_id"],
            "username": r["username"], "first_name": r["first_name"],
            "price": r["price"], "end_date": r["end_date"],
            "days_left": max(0, left.days),
        })
    return templates.TemplateResponse(
        request, "subscriptions.html",
        {
            "active": "subscriptions", "admin": request.session.get("admin"),
            "subs": subs, "unit_labels": UNIT_LABELS,
            "error": error, "ok": ok,
            "results": results, "results_query": results_query,
        },
        status_code=status,
    )


@router.get("/subscriptions")
async def subscriptions_page(request: Request):
    current_admin(request)
    return await _render(request)


@router.post("/subscriptions/extend")
async def subscriptions_extend(
    request: Request,
    sub_id: str = Form(...),
    value: str = Form(...),
    unit: str = Form(...),
):
    current_admin(request)
    try:
        sid = int((sub_id or "").strip())
        n = int((value or "").strip())
    except ValueError:
        return await _render(request, error="Срок продления — целое число.", status=400)
    if n <= 0:
        return await _render(request, error="Срок продления должен быть больше нуля.", status=400)
    if unit not in VALID_UNITS:
        return await _render(request, error="Неизвестная единица срока.", status=400)

    pool = get_pool()
    sub = await repo.get_subscription(pool, sid)
    if sub is None or sub["status"] != "active":
        return await _render(request, error="Подписка не найдена или уже неактивна.", status=400)
    new_end = add_period(sub["end_date"], n, unit)
    await repo.set_subscription_end_date(pool, sid, new_end)
    logger.info(
        "Админка: подписка #{} продлена на {} {} (до {:%d.%m.%Y %H:%M}) для id={}",
        sid, n, unit, new_end, sub["tg_id"],
    )
    return await _render(
        request,
        ok=f"Подписка #{sid} продлена на {n} {UNIT_LABELS[unit]} — до "
           f"{new_end:%d.%m.%Y %H:%M}.",
    )


@router.post("/subscriptions/disable")
async def subscriptions_disable(request: Request, sub_id: str = Form(...)):
    current_admin(request)
    try:
        sid = int((sub_id or "").strip())
    except ValueError:
        return await _render(request, error="Некорректный идентификатор подписки.", status=400)
    pool = get_pool()
    tg_id = await repo.disable_subscription_via_expiry(pool, sid)
    if tg_id is None:
        return await _render(request, error="Подписка не найдена или уже неактивна.", status=400)
    logger.info("Админка: подписка #{} отключена вручную (id={})", sid, tg_id)
    return await _render(
        request,
        ok=f"Подписка #{sid} отключена. Доступ закрыт; бот удалит участника из группы "
           f"на ближайшей проверке окончаний.",
    )


# ── Участники: поиск / карточка / история / выдача / аннулирование ────────────
def _sub_view(sub) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": sub["id"],
        "price": f"{fmt_price(sub['price'])} ₽",
        "period": texts.period_phrase(sub["months"], sub["unit"]),
        "start": sub["start_date"].strftime("%d.%m.%Y · %H:%M"),
        "end": sub["end_date"].strftime("%d.%m.%Y · %H:%M"),
        "remaining": texts.remaining_phrase(sub["end_date"], now, sub["unit"]),
        "status": sub["status"],
    }


async def _member_card(request: Request, tg_id: int, *, ok: str | None = None,
                       error: str | None = None, status: int = 200):
    pool = get_pool()
    user = await repo.get_user(pool, tg_id)
    active = await repo.get_active_subscription(pool, tg_id)
    last = await repo.get_last_subscription(pool, tg_id)
    events = await repo.get_user_events(pool, tg_id, limit=20)

    member = {
        "tg_id": tg_id,
        "found": not (user is None and last is None),
        "username": user["username"] if user else None,
        "first_name": user["first_name"] if user else None,
        "email": user["email"] if user else None,
        "is_blocked": user["is_blocked"] if user else False,
        "has_active": active is not None,
        "active": _sub_view(active) if active is not None else None,
        "last": _sub_view(last) if last is not None else None,
    }
    history = [
        {"event": e["event"], "when": e["created_at"].strftime("%d.%m.%Y · %H:%M")}
        for e in events
    ]
    return templates.TemplateResponse(
        request, "member.html",
        {
            "active": "subscriptions", "admin": request.session.get("admin"),
            "member": member, "history": history, "unit_labels": UNIT_LABELS,
            "ok": ok, "error": error,
        },
        status_code=status,
    )


@router.get("/subscriptions/find")
async def member_find(request: Request, q: str = "", tg_id: str = ""):
    current_admin(request)
    raw = (q or tg_id or "").strip()
    if not raw:
        return await _render(request, error="Введите Telegram ID, @username или имя.", status=400)
    if raw.lstrip("-").isdigit():
        return RedirectResponse(f"/subscriptions/member/{int(raw)}", status_code=303)
    pool = get_pool()
    found = await repo.search_users(pool, raw.lstrip("@"))
    if not found:
        return await _render(request, error=f"Никого не найдено по запросу «{raw}».", status=404)
    if len(found) == 1:
        return RedirectResponse(f"/subscriptions/member/{found[0]['tg_id']}", status_code=303)
    results = [
        {"tg_id": u["tg_id"], "username": u["username"], "first_name": u["first_name"]}
        for u in found
    ]
    return await _render(request, results=results, results_query=raw)


@router.get("/subscriptions/member/{tg_id}")
async def member_page(request: Request, tg_id: int, ok: str | None = None,
                      error: str | None = None):
    current_admin(request)
    return await _member_card(request, tg_id, ok=ok, error=error)


@router.post("/subscriptions/member/add")
async def member_add(
    request: Request,
    tg_id: str = Form(...),
    value: str = Form(...),
    unit: str = Form(...),
):
    current_admin(request)
    raw_id = (tg_id or "").strip()
    if not raw_id.lstrip("-").isdigit():
        return await _render(request, error="Telegram ID участника — это число.", status=400)
    mid = int(raw_id)
    try:
        n = int((value or "").strip())
    except ValueError:
        return await _member_card(request, mid, error="Срок — целое число.", status=400)
    if n <= 0:
        return await _member_card(request, mid, error="Срок должен быть больше нуля.", status=400)
    if unit not in VALID_UNITS:
        return await _member_card(request, mid, error="Неизвестная единица срока.", status=400)

    pool = get_pool()
    await repo.upsert_user(pool, mid, None, None)
    # Цена — из тарифа с той же длительностью (если есть), иначе 0 (комплимент/грант).
    price = Decimal("0")
    tariff_id = None
    for t in await repo.get_all_tariffs(pool):
        if t["months"] == n and t["unit"] == unit:
            price, tariff_id = Decimal(t["price"]), t["id"]
            break
    end_date = add_period(datetime.now(timezone.utc), n, unit)
    sub_id = await repo.add_subscription(
        pool, mid, tariff_id, price, n, unit, end_date, status="active",
    )
    logger.info(
        "Админка: вручную выдана подписка #{} для id={} ({} {}, до {:%d.%m.%Y %H:%M})",
        sub_id, mid, n, unit, end_date,
    )
    msg = (
        f"Подписка #{sub_id} создана: {fmt_price(price)} ₽ · "
        f"{texts.period_phrase(n, unit)}, до {end_date:%d.%m.%Y %H:%M}. "
        f"Бот пустит участника в группу по заявке на вступление."
    )
    return RedirectResponse(f"/subscriptions/member/{mid}?ok={msg}", status_code=303)


@router.post("/subscriptions/member/{tg_id}/annul")
async def member_annul(request: Request, tg_id: int):
    current_admin(request)
    pool = get_pool()
    active = await repo.get_active_subscription(pool, tg_id)
    if active is None:
        return await _member_card(
            request, tg_id, error="У участника нет активной подписки.", status=400
        )
    await repo.disable_subscription_via_expiry(pool, active["id"])
    logger.info("Админка: подписка #{} аннулирована (id={})", active["id"], tg_id)
    msg = (
        f"Подписка #{active['id']} аннулирована. Доступ закрыт; бот удалит участника "
        f"из группы на ближайшей проверке окончаний."
    )
    return RedirectResponse(f"/subscriptions/member/{tg_id}?ok={msg}", status_code=303)
