"""Рассылки по сегментам: бот-джоб отправки из очереди (этап 6).

Веб-админка не держит бота (это отдельный процесс), поэтому форма рассылки только
кладёт задачу в таблицу `broadcasts` со статусом 'pending'. Этот фоновый джоб
APScheduler забирает ожидающие рассылки по одной (атомарный claim pending → sending),
разворачивает сегмент аудитории в список получателей и шлёт им текст, считая
отправленные / заблокировавшие бота / прочие ошибки. По завершении — статус 'done'
и счётчики (их видно в админке).

Только текст (у проекта нет S3 для фото — решение этапа 6). Аудитории: все / активные
подписчики / бывшие / запускавшие без подписки (см. repo.AUDIENCE_*).
"""
from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
import asyncpg

from .. import repo
from ..logger import logger

# Пауза между сообщениями — держим темп в пределах лимитов Telegram (~30 msg/s).
_SEND_DELAY = 0.05


async def run_broadcast_check(pool: asyncpg.Pool, bot: Bot) -> None:
    """Фоновый проход: отправить одну ожидающую рассылку (если есть)."""
    row = await repo.claim_next_broadcast(pool)
    if row is None:
        return

    bid = row["id"]
    recipients = await repo.broadcast_recipients(pool, row["audience"])
    await repo.set_broadcast_total(pool, bid, len(recipients))
    logger.info(
        "📣 Рассылка #{} [{}] стартовала: {} получателей",
        bid, row["audience"], len(recipients),
    )

    sent = blocked = failed = 0
    for tg_id in recipients:
        try:
            await bot.send_message(tg_id, row["body"])
            sent += 1
        except TelegramForbiddenError:
            # Пользователь заблокировал бота — помечаем, чтобы не слать впредь.
            blocked += 1
            await repo.set_user_blocked(pool, tg_id, True)
        except TelegramRetryAfter as e:
            # Флуд-контроль: ждём и повторяем этого же получателя один раз.
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(tg_id, row["body"])
                sent += 1
            except Exception:  # noqa: BLE001
                failed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.warning("Рассылка #{}: ошибка для id={}: {}", bid, tg_id, e)
        await asyncio.sleep(_SEND_DELAY)

    await repo.finish_broadcast(pool, bid, sent=sent, blocked=blocked, failed=failed)
    logger.info(
        "✅ Рассылка #{} завершена: отправлено {}, заблокировали {}, ошибок {}",
        bid, sent, blocked, failed,
    )
