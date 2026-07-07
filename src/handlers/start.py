"""Команда /start. Этап 0 — заглушка: подтверждает, что бот жив и отвечает.

На этапе 1 сюда придёт welcome-экран с текстом и кнопками из БД.
"""
from __future__ import annotations

import asyncpg
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from ..logger import logger

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool) -> None:
    user = message.from_user
    # Регистрируем/обновляем пользователя — задел под следующие этапы.
    await pool.execute(
        """
        INSERT INTO users (tg_id, username, first_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (tg_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            updated_at = now()
        """,
        user.id,
        user.username,
        user.first_name,
    )
    text = (
        "Бот платного клуба на связи.\n\n"
        "Каркас поднят (этап 0). Экраны, тарифы и оплата появятся на следующих этапах."
    )
    await message.answer(text)
    logger.info(f"🤖 Бот → @{user.username or '—'}: приветствие-заглушка (этап 0)")
