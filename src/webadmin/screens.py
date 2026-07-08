"""Раздел админки «Экраны бота» (этап 6).

Правка текстов инфо-экранов бота (приветствие, меню, что внутри, правила, поддержка)
без правки кода. Состав экранов и дефолтные тексты — в реестре services.screens; здесь
правится текст (таблица screen_texts через repo). Бот применяет правки на лету.

Картинки экранов не поддерживаем (в проекте нет S3 — решение этапа 6): только текст.
Подписи кнопок меню правятся на отдельной странице «Кнопки меню».
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .. import repo
from ..db import get_pool
from ..logger import logger
from ..services import screens
from .deps import current_admin, templates

router = APIRouter()


@router.get("/screens")
async def screens_page(request: Request, ok: int = 0):
    current_admin(request)
    pool = get_pool()
    items = await screens.screen_list(pool)
    return templates.TemplateResponse(
        request, "screens_list.html",
        {
            "active": "screens", "admin": request.session.get("admin"),
            "screens": items, "ok": bool(ok),
        },
    )


async def _render_form(request: Request, key: str, *, ok: bool = False, error: str = ""):
    pool = get_pool()
    screen = await screens.screen_one(pool, key)
    if screen is None:
        return RedirectResponse("/screens", status_code=303)
    return templates.TemplateResponse(
        request, "screen_form.html",
        {
            "active": "screens", "admin": request.session.get("admin"),
            "screen": screen, "ok": ok, "error": error,
        },
    )


@router.get("/screens/{key}")
async def screen_form(request: Request, key: str, ok: int = 0):
    current_admin(request)
    return await _render_form(request, key, ok=bool(ok))


@router.post("/screens/{key}")
async def screen_save(request: Request, key: str):
    current_admin(request)
    pool = get_pool()
    screen = await screens.screen_one(pool, key)
    if screen is None:
        return RedirectResponse("/screens", status_code=303)
    form = await request.form()

    # Пустой текст или совпал с дефолтом → храним NULL (дефолт из реестра).
    raw = (form.get("body") or "").replace("\r\n", "\n").strip()
    default = screens.default_text(key).strip()
    body = None if (not raw or raw == default) else raw

    await repo.upsert_screen_text(pool, key, body)
    logger.info("Админка: экран «{}» сохранён", key)
    return RedirectResponse(f"/screens/{key}?ok=1", status_code=303)
