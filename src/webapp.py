"""Лёгкий HTTP-приёмник вебхуков Продамуса (aiohttp, в процессе бота).

Продамус подтверждает оплату POST-запросом (multipart/form-data) на наш эндпоинт
с подписью в заголовке `Sign`. Сервер живёт в том же процессе, что и бот-поллинг,
и делит с ним пул БД и объект Bot — чтобы уведомить пользователя сразу после
активации подписки. На проде за ним стоит nginx с TLS (домен dolbikfit.ru, этап 7).

Ответ 200 «success» → Продамус считает уведомление обработанным и прекращает ретраи;
любой другой код → повторит попытку позже.
"""
from __future__ import annotations

import asyncpg
from aiogram import Bot
from aiohttp import web

from .config import settings
from .logger import logger
from .services import payments


async def _handle_webhook(request: web.Request) -> web.Response:
    pool: asyncpg.Pool = request.app["pool"]
    bot: Bot = request.app["bot"]
    try:
        post = await request.post()
    except Exception as e:  # noqa: BLE001 — битое тело не должно ронять сервер
        logger.warning(f"Вебхук Продамуса: не разобрать тело запроса: {e}")
        return web.Response(status=400, text="bad request")

    # Плоские пары (включая bracket-ключи products[0][name]) — как ждёт parse_webhook_form.
    items = [(k, v) for k, v in post.items() if isinstance(v, str)]
    sign = request.headers.get("Sign")
    ok = await payments.handle_webhook(pool, bot, items, sign)
    if ok:
        return web.Response(text="success")
    return web.Response(status=400, text="signature verification failed")


async def _health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_app(pool: asyncpg.Pool, bot: Bot) -> web.Application:
    app = web.Application()
    app["pool"] = pool
    app["bot"] = bot
    app.router.add_post(settings.prodamus_webhook_path, _handle_webhook)
    app.router.add_get("/healthz", _health)
    return app


async def start_webhook_server(pool: asyncpg.Pool, bot: Bot) -> web.AppRunner:
    """Поднимает HTTP-сервер приёма вебхуков. Возвращает runner для остановки."""
    app = build_app(pool, bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.prodamus_webhook_port)
    await site.start()
    logger.info(
        f"✅ Приёмник вебхука Продамуса слушает :{settings.prodamus_webhook_port}"
        f"{settings.prodamus_webhook_path}"
    )
    return runner
