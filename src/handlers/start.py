"""Хендлеры /start, /menu, /id. Точка входа в навигацию бота."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
import asyncpg

from .. import repo, texts
from ..logger import logger
from ..services import menu
from ..services import screens
from ..utils import fmt_price

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool, state: FSMContext) -> None:
    await state.clear()
    u = message.from_user
    await repo.upsert_user(pool, u.id, u.username, u.first_name)
    await repo.set_fsm_state(pool, u.id, "screen:start")
    subscribed = await repo.get_active_subscription(pool, u.id) is not None
    view = await screens.resolve(pool, "start")
    await screens.render(
        message, text=view["text"], markup=await menu.welcome_kb(pool, subscribed),
        photo_url=view["photo_url"], edit=False,
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: приветствие /start")


@router.message(Command("menu"))
async def cmd_menu(message: Message, pool: asyncpg.Pool, state: FSMContext) -> None:
    await state.clear()
    u = message.from_user
    await repo.upsert_user(pool, u.id, u.username, u.first_name)
    await repo.set_fsm_state(pool, u.id, "screen:menu")
    subscribed = await repo.get_active_subscription(pool, u.id) is not None
    view = await screens.resolve(pool, "menu")
    await screens.render(
        message, text=view["text"], markup=await menu.main_menu_kb(pool, subscribed),
        photo_url=view["photo_url"], edit=False,
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: главное меню /menu")


@router.message(Command("oferta"))
async def cmd_oferta(message: Message, pool: asyncpg.Pool) -> None:
    """Оферта и реквизиты (требование Продамуса — доступны в боте)."""
    tariffs = await repo.get_active_tariffs(pool)
    block = "\n".join(
        f"· {texts.period_phrase(t['months'], t['unit'])} — {fmt_price(t['price'])} ₽"
        for t in tariffs
    ) or "—"
    await message.answer(
        texts.oferta_text(block), disable_web_page_preview=True
    )
    u = message.from_user
    logger.info(f"🤖 Бот → @{u.username or '—'}: /oferta (оферта и реквизиты)")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    u = message.from_user
    lines = [f"Ваш Telegram ID: <code>{u.id}</code>"]
    if message.chat.type != "private":
        lines.append(f"ID этого чата: <code>{message.chat.id}</code>")
    await message.answer("\n".join(lines))
    logger.info(f"🤖 Бот → @{u.username or '—'}: /id ({u.id})")
