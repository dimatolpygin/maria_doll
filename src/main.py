"""Точка входа: миграции (Alembic), инициализация БД/Redis, запуск бота (polling)
и приёмника вебхуков Продамуса (этап 2).

Планировщик проверки окончаний подписок добавится на этапе 3.
"""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand

from . import __version__
from .cache import close_redis, init_redis
from .config import settings
from .db import close_pool, init_pool
from .handlers import get_main_router
from .logger import setup_logging
from .middlewares import LoggingMiddleware
from .webapp import start_webhook_server


async def main() -> None:
    log = setup_logging(settings.log_level)
    log.info(f"⏳ Запуск бота платного клуба @Fit_it_bot (v{__version__})...")

    pool = await init_pool()
    redis = await init_redis()

    # FSM-состояния храним в Redis. parse_mode=HTML включён глобально.
    storage = RedisStorage.from_url(settings.redis_url)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)

    # Зависимости, доступные во всех хендлерах как аргументы pool / redis.
    dp["pool"] = pool
    dp["redis"] = redis

    dp.update.middleware(LoggingMiddleware())
    dp.include_router(get_main_router())

    # Приёмник вебхуков Продамуса — рядом с поллингом, в том же event loop
    # (общий pool и bot: уведомляем пользователя сразу после активации подписки).
    webhook_runner = await start_webhook_server(pool, bot)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_my_commands([
            BotCommand(command="start", description="Главный экран"),
        ])
        me = await bot.get_me()
        # Явно запрашиваем нужные типы апдейтов (chat_join_request понадобится на этапе 3).
        allowed = dp.resolve_used_update_types()
        log.info(f"✅ Бот @{me.username} запущен (polling); апдейты: {allowed}")
        await dp.start_polling(bot, allowed_updates=allowed)
    finally:
        log.info("Останавливаю бота...")
        await webhook_runner.cleanup()
        await close_redis()
        await close_pool()
        await bot.session.close()


def run() -> None:
    # Миграции применяем ДО основного event loop: Alembic (async) сам поднимает
    # временный loop, поэтому вызывать его внутри работающего loop нельзя.
    setup_logging(settings.log_level)
    from .migrate import run_migrations

    run_migrations()
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    run()
