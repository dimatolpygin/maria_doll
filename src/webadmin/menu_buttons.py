"""Раздел админки «Кнопки меню» (этап 6).

Переименование и показ/скрытие кнопок меню бота (приветствие + главное меню) без
правки кода. Состав/раскладка/дефолты — в реестре services.menu; здесь правим только
подпись и видимость (таблица menu_buttons через repo). Бот применяет на лету.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import repo
from ..db import get_pool
from ..logger import logger
from ..services import menu
from .deps import current_admin, templates

router = APIRouter()


async def _render(request: Request, *, ok: str | None = None):
    pool = get_pool()
    buttons = await menu.button_list(pool)
    return templates.TemplateResponse(
        request, "menu_buttons.html",
        {
            "active": "menu", "admin": request.session.get("admin"),
            "buttons": buttons, "ok": ok,
        },
    )


@router.get("/menu-buttons")
async def menu_buttons_page(request: Request):
    current_admin(request)
    return await _render(request)


@router.post("/menu-buttons")
async def menu_buttons_save(request: Request):
    current_admin(request)
    form = await request.form()
    pool = get_pool()
    for key, (default_label, _cb) in menu.BUTTON_DEFS.items():
        raw = (form.get(f"label_{key}") or "").strip()
        label = None if (not raw or raw == default_label) else raw
        is_visible = form.get(f"visible_{key}") is not None
        await repo.upsert_menu_button(pool, key, label, is_visible)
    logger.info("Админка: настройки кнопок меню сохранены")
    return await _render(request, ok="Настройки кнопок меню сохранены.")
