"""Рассылки по сегментам: бот-джоб отправки из очереди (этап 6).

Веб-админка не держит бота (это отдельный процесс), поэтому форма рассылки только
кладёт задачу в таблицу `broadcasts` со статусом 'pending'. Этот фоновый джоб
APScheduler забирает ожидающие рассылки по одной (атомарный claim pending → sending),
разворачивает сегмент аудитории в список получателей и шлёт им текст и/или фото,
считая отправленные / заблокировавшие бота / прочие ошибки. По завершении — статус
'done' и счётчики (их видно в админке).

Фото веб заливает в S3 (services.storage) и хранит здесь как публичные URL — бот шлёт
по ссылке: одно фото → send_photo, несколько → media_group (подпись на первом).
Аудитории: все / активные подписчики / бывшие / запускавшие без подписки.
"""
from __future__ import annotations

import asyncio
import json

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InputMediaPhoto
import asyncpg

from .. import repo
from ..logger import logger


def _media(photos: list[dict], text: str | None) -> list[InputMediaPhoto]:
    """media_group из URL; подпись — только на первом фото."""
    return [
        InputMediaPhoto(media=p["url"], caption=text if (i == 0 and text) else None)
        for i, p in enumerate(photos)
    ]


async def _send_one(bot: Bot, uid: int, text: str | None, photos: list[dict]) -> None:
    """Отправляет один экземпляр рассылки конкретному пользователю."""
    if not photos:
        await bot.send_message(uid, text)
    elif len(photos) == 1:
        await bot.send_photo(uid, photos[0]["url"], caption=text or None)
    else:
        await bot.send_media_group(uid, _media(photos, text))


async def run_broadcast_check(pool: asyncpg.Pool, bot: Bot) -> None:
    """Фоновый проход: отправить одну ожидающую рассылку (если есть)."""
    row = await repo.claim_next_broadcast(pool)
    if row is None:
        return

    bid = row["id"]
    text = row["body"] or None
    photos = json.loads(row["photos"] or "[]")
    recipients = await repo.broadcast_recipients(pool, row["audience"])
    await repo.set_broadcast_total(pool, bid, len(recipients))
    logger.info(
        "📣 Рассылка #{} [{}] стартовала: {} получателей, фото {}",
        bid, row["audience"], len(recipients), len(photos),
    )

    sent = blocked = failed = 0
    for tg_id in recipients:
        try:
            await _send_one(bot, tg_id, text, photos)
            sent += 1
        except TelegramForbiddenError:
            # Пользователь заблокировал бота — помечаем, чтобы не слать впредь.
            blocked += 1
            await repo.set_user_blocked(pool, tg_id, True)
        except TelegramRetryAfter as e:
            # Флуд-контроль: ждём и повторяем этого же получателя один раз.
            await asyncio.sleep(e.retry_after + 1)
            try:
                await _send_one(bot, tg_id, text, photos)
                sent += 1
            except Exception:  # noqa: BLE001
                failed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.warning("Рассылка #{}: ошибка для id={}: {}", bid, tg_id, e)
        # Альбом = несколько сообщений — шлём чуть медленнее (лимиты Telegram).
        await asyncio.sleep(0.1 if photos else 0.05)

    await repo.finish_broadcast(pool, bid, sent=sent, blocked=blocked, failed=failed)
    logger.info(
        "✅ Рассылка #{} завершена: отправлено {}, заблокировали {}, ошибок {}",
        bid, sent, blocked, failed,
    )
