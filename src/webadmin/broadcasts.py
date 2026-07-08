"""Раздел админки «Рассылка» (этап 6).

Форма рассылки (сегмент аудитории + текст). Веб только кладёт задачу в очередь
`broadcasts` (status='pending') — саму отправку делает бот-джоб services.broadcasts
(веб-процесс бота не держит). Ниже — список последних рассылок со статусом и
счётчиками (отправлено / заблокировали / ошибки). Только текст (в проекте нет S3).
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import repo
from ..db import get_pool
from ..logger import logger
from .deps import current_admin, templates

router = APIRouter()

# Сегменты аудитории: значение repo.AUDIENCE_* → подпись.
AUDIENCES: list[tuple[str, str]] = [
    (repo.AUDIENCE_ALL, "Всем"),
    (repo.AUDIENCE_ACTIVE, "Активным подписчикам"),
    (repo.AUDIENCE_FORMER, "Ушедшим (была подписка)"),
    (repo.AUDIENCE_NEVER, "Запускавшим без подписки"),
]
_AUD_KEYS = {a for a, _ in AUDIENCES}
_AUD_LABELS = dict(AUDIENCES)

_STATUS_LABELS = {"pending": "в очереди", "sending": "отправляется", "done": "завершена"}


def _row_view(b) -> dict:
    body = b["body"] or ""
    return {
        "id": b["id"],
        "audience": _AUD_LABELS.get(b["audience"], b["audience"]),
        "preview": (body[:80] + "…") if len(body) > 80 else (body or "—"),
        "status": _STATUS_LABELS.get(b["status"], b["status"]),
        "is_done": b["status"] == "done",
        "total": b["total"], "sent": b["sent"],
        "blocked": b["blocked"], "failed": b["failed"],
        "created_at": b["created_at"].strftime("%d.%m.%Y · %H:%M"),
    }


async def _overview(request: Request, *, ok: str | None = None, error: str | None = None,
                    status: int = 200):
    pool = get_pool()
    rows = await repo.list_broadcasts(pool, limit=50)
    return templates.TemplateResponse(
        request, "broadcasts.html",
        {
            "active": "broadcasts", "admin": request.session.get("admin"),
            "audiences": AUDIENCES, "rows": [_row_view(b) for b in rows],
            "ok": ok, "error": error,
        },
        status_code=status,
    )


@router.get("/broadcasts")
async def broadcasts_page(request: Request, ok: str | None = None, error: str | None = None):
    current_admin(request)
    return await _overview(request, ok=ok, error=error)


@router.post("/broadcasts/create")
async def broadcast_create(
    request: Request,
    audience: str = Form(...),
    body: str = Form(""),
):
    admin = current_admin(request)
    pool = get_pool()

    if audience not in _AUD_KEYS:
        return await _overview(request, error="Выберите аудиторию.", status=400)
    text = (body or "").strip()
    if not text:
        return await _overview(request, error="Добавьте текст рассылки.", status=400)

    bid = await repo.create_broadcast(
        pool, audience=audience, body=text, created_by=admin.get("login"),
    )
    logger.info(
        "Админка: создана рассылка #{} [{}] пользователем {}",
        bid, audience, admin.get("login"),
    )
    return RedirectResponse(
        f"/broadcasts?ok=Рассылка #{bid} поставлена в очередь — бот отправит её в ближайшее время.",
        status_code=303,
    )
