"""Раздел админки «Рассылка» (этап 6).

Форма рассылки (сегмент аудитории + текст + до 10 фото/альбом). Веб заливает фото в
S3 (services.storage) и кладёт задачу в очередь `broadcasts` (status='pending') с
текстом и URL фото — саму отправку делает бот-джоб services.broadcasts (веб-процесс
бота не держит). Ниже — список последних рассылок со статусом и счётчиками
(отправлено / заблокировали / ошибки). Если S3 не настроен — только текст.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from .. import repo
from ..config import settings
from ..db import get_pool
from ..logger import logger
from ..services import storage
from .deps import current_admin, templates

router = APIRouter()

MAX_PHOTOS = 10  # лимит Telegram на media_group
MAX_DOCS = 10  # разумный лимит файлов в одной рассылке
# Разрешённые расширения файлов — берём из карты MIME в storage (единый источник).
ALLOWED_DOC_EXTS = set(storage.DOC_CONTENT_TYPES)

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
    photos = json.loads(b["photos"] or "[]")
    documents = json.loads(b["documents"] or "[]")
    return {
        "id": b["id"],
        "audience": _AUD_LABELS.get(b["audience"], b["audience"]),
        "preview": (body[:80] + "…") if len(body) > 80 else (body or "—"),
        "photos": len(photos),
        "documents": len(documents),
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
            "audiences": AUDIENCES, "s3_enabled": settings.s3_enabled,
            "max_photos": MAX_PHOTOS, "max_docs": MAX_DOCS,
            "doc_exts": ", ".join(sorted(ALLOWED_DOC_EXTS)),
            "rows": [_row_view(b) for b in rows],
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
    photos: list[UploadFile] = File(default=[]),
    documents: list[UploadFile] = File(default=[]),
):
    admin = current_admin(request)
    pool = get_pool()

    if audience not in _AUD_KEYS:
        return await _overview(request, error="Выберите аудиторию.", status=400)

    text = (body or "").strip() or None
    # Реальные файлы (пустой input может прислать одну пустую запись без имени).
    files = [f for f in photos if f is not None and f.filename]
    doc_files = [f for f in documents if f is not None and f.filename]
    if not text and not files and not doc_files:
        return await _overview(
            request, error="Добавьте текст, фото или файл.", status=400
        )
    if (files or doc_files) and not settings.s3_enabled:
        return await _overview(
            request,
            error="Вложения недоступны: хранилище S3 не настроено. Отправьте только текст.",
            status=400,
        )
    if len(files) > MAX_PHOTOS:
        return await _overview(
            request, error=f"Слишком много фото: максимум {MAX_PHOTOS}.", status=400
        )
    if len(doc_files) > MAX_DOCS:
        return await _overview(
            request, error=f"Слишком много файлов: максимум {MAX_DOCS}.", status=400
        )
    # Валидация расширений файлов до заливки.
    for f in doc_files:
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_DOC_EXTS:
            return await _overview(
                request,
                error=f"Файл «{f.filename}»: тип .{ext} не поддерживается. "
                      f"Разрешены: {', '.join(sorted(ALLOWED_DOC_EXTS))}.",
                status=400,
            )

    uploaded: list[dict] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        ext = "png" if (f.content_type or "").endswith("png") else "jpg"
        try:
            url = await storage.upload_photo(data, ext, prefix="broadcast")
        except Exception as e:  # noqa: BLE001 — не валим всю рассылку из-за одного фото
            logger.error("Рассылка (веб): не удалось загрузить фото в S3: {}", e)
            return await _overview(
                request, error="Не удалось загрузить фото в хранилище. Попробуйте ещё раз.",
                status=400,
            )
        uploaded.append({"url": url})

    uploaded_docs: list[dict] = []
    for f in doc_files:
        data = await f.read()
        if not data:
            continue
        try:
            url = await storage.upload_document(data, f.filename, prefix="broadcast-doc")
        except Exception as e:  # noqa: BLE001
            logger.error("Рассылка (веб): не удалось загрузить файл в S3: {}", e)
            return await _overview(
                request, error="Не удалось загрузить файл в хранилище. Попробуйте ещё раз.",
                status=400,
            )
        uploaded_docs.append({"url": url, "name": f.filename})

    if not text and not uploaded and not uploaded_docs:
        return await _overview(
            request, error="Добавьте текст, фото или файл.", status=400
        )

    bid = await repo.create_broadcast(
        pool, audience=audience, body=text, photos=uploaded, documents=uploaded_docs,
        created_by=admin.get("login"),
    )
    logger.info(
        "Админка: создана рассылка #{} [{}] (фото {}, файлов {}) пользователем {}",
        bid, audience, len(uploaded), len(uploaded_docs), admin.get("login"),
    )
    return RedirectResponse(
        f"/broadcasts?ok=Рассылка #{bid} поставлена в очередь — бот отправит её в ближайшее время.",
        status_code=303,
    )
