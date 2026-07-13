"""Загрузка медиа в S3-совместимое хранилище (этап 6).

Фото из веб-админки (рассылки и картинки экранов бота) заливаются в S3 и раздаются
Telegram по публичной ссылке. boto3 синхронный, поэтому вызовы выполняем в пуле
потоков, чтобы не блокировать event loop. Если S3 не настроен (settings.s3_enabled) —
функции не вызываются, рассылки/экраны остаются текстовыми.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from uuid import uuid4

from ..config import settings
from ..logger import logger


@lru_cache(maxsize=1)
def _client():
    """Ленивая инициализация S3-клиента (один на процесс)."""
    import boto3  # импорт здесь — boto3 нужен только при включённом S3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region or None,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


def _put_sync(key: str, data: bytes, content_type: str) -> None:
    _client().put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        ACL="public-read",
    )


async def upload_photo(data: bytes, ext: str = "jpg", *, prefix: str = "broadcast") -> str:
    """Заливает фото в S3 и возвращает публичный URL. Бросает исключение при ошибке.

    prefix — папка в бакете (рассылка → broadcast, картинки экранов → screens).
    """
    key = f"{prefix}/{uuid4().hex}.{ext}"
    content_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    await asyncio.to_thread(_put_sync, key, data, content_type)
    base = (settings.s3_public_base_url or settings.s3_endpoint).rstrip("/")
    url = f"{base}/{settings.s3_bucket}/{key}"
    logger.info(f"🖼 Фото загружено в S3: {url}")
    return url


# Разрешённые к рассылке типы файлов → MIME. Ключи (расширения) заодно служат
# белым списком в веб-форме: что не в этом наборе — не грузим.
DOC_CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "html": "text/html",
    "txt": "text/plain",
}


async def upload_document(data: bytes, filename: str, *, prefix: str = "broadcast-doc") -> str:
    """Заливает файл рассылки (pdf/csv/xlsx/…) в S3 и возвращает публичный URL.

    В ключе — uuid, чтобы имена не сталкивались; исходное имя (filename) хранится
    отдельно в БД и отдаётся Telegram как имя документа. Бросает исключение при ошибке.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    key = f"{prefix}/{uuid4().hex}.{ext}"
    content_type = DOC_CONTENT_TYPES.get(ext, "application/octet-stream")
    await asyncio.to_thread(_put_sync, key, data, content_type)
    base = (settings.s3_public_base_url or settings.s3_endpoint).rstrip("/")
    url = f"{base}/{settings.s3_bucket}/{key}"
    logger.info(f"📎 Файл загружен в S3: {url} ({filename})")
    return url
