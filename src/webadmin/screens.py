"""Раздел админки «Экраны бота» (этап 6).

Правка текстов инфо-экранов бота (приветствие, меню, что внутри, правила, поддержка)
без правки кода и картинки экрана (S3). Состав экранов и дефолтные тексты — в реестре
services.screens; здесь правится текст (screen_texts.body) и картинка
(screen_texts.photo_url через S3, services.storage). Бот применяет правки на лету.

Картинка опциональна. Telegram кладёт текст в подпись под фото, поэтому при наличии
картинки текст ограничен 1024 символами (серверная валидация). Если S3 не настроен —
доступна только правка текста. Подписи кнопок меню правятся на отдельной странице.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .. import repo
from ..config import settings
from ..db import get_pool
from ..logger import logger
from ..services import screens, storage
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
            "s3_enabled": settings.s3_enabled, "caption_limit": screens.CAPTION_LIMIT,
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

    # Текст: пустой или совпал с дефолтом → храним NULL (дефолт из реестра).
    raw = (form.get("body") or "").replace("\r\n", "\n").strip()
    default = screens.default_text(key).strip()
    body = None if (not raw or raw == default) else raw

    # Картинка: снять / загрузить новую / оставить как есть. Текст и фото независимы.
    current_photo = screen.get("photo_url")
    new_photo = current_photo
    if form.get("remove_photo") is not None:
        new_photo = None
    else:
        upload = form.get("photo")
        if upload is not None and getattr(upload, "filename", ""):
            if not settings.s3_enabled:
                return await _render_form(
                    request, key,
                    error="Картинка недоступна: хранилище S3 не настроено.",
                )
            data = await upload.read()
            if data:
                ext = "png" if (upload.content_type or "").endswith("png") else "jpg"
                try:
                    new_photo = await storage.upload_photo(data, ext, prefix="screens")
                except Exception as e:  # noqa: BLE001 — не валим сохранение из-за S3
                    logger.error("Экраны (веб): не удалось загрузить фото в S3: {}", e)
                    return await _render_form(
                        request, key,
                        error="Не удалось загрузить картинку в хранилище. Попробуйте ещё раз.",
                    )

    # При наличии картинки текст идёт подписью под фото — лимит 1024 символа.
    if new_photo:
        effective = raw if raw else default
        if len(effective) > screens.CAPTION_LIMIT:
            return await _render_form(
                request, key,
                error=(
                    f"Текст экрана с картинкой не может быть длиннее "
                    f"{screens.CAPTION_LIMIT} символов (сейчас {len(effective)}). "
                    f"Сократите текст или уберите картинку."
                ),
            )

    await repo.upsert_screen_text(pool, key, body)
    if new_photo != current_photo:
        await repo.upsert_screen_photo(pool, key, new_photo)

    logger.info("Админка: экран «{}» сохранён (фото: {})", key, "да" if new_photo else "нет")
    return RedirectResponse(f"/screens/{key}?ok=1", status_code=303)
