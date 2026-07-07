"""Навигация по инфо-экранам бота (callback-кнопки).

Каждый переход фиксирует текущий экран пользователя в fsm_states, чтобы админ видел,
где человек находится/застрял.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
import asyncpg

from .. import keyboards as kb
from .. import repo
from ..config import settings
from ..logger import logger
from ..services import menu
from ..services import screens

router = Router()


async def _show(
    cb: CallbackQuery,
    pool: asyncpg.Pool,
    screen: str,
    key: str,
    markup: InlineKeyboardMarkup,
) -> None:
    """Показывает экран (заменяя текущее сообщение) и фиксирует экран в БД."""
    await repo.set_fsm_state(pool, cb.from_user.id, f"screen:{screen}")
    view = await screens.resolve(pool, key)
    await screens.render(
        cb.message, text=view["text"], markup=markup,
        photo_url=view["photo_url"], edit=True,
    )
    await cb.answer()
    logger.info(f"🤖 Бот → @{cb.from_user.username or '—'}: экран «{screen}»")


@router.callback_query(F.data == kb.NAV_START)
async def nav_start(cb: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    await state.clear()
    subscribed = await repo.get_active_subscription(pool, cb.from_user.id) is not None
    await _show(cb, pool, "start", "start", await menu.welcome_kb(pool, subscribed))


@router.callback_query(F.data == kb.NAV_MENU)
async def nav_menu(cb: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    await state.clear()
    subscribed = await repo.get_active_subscription(pool, cb.from_user.id) is not None
    await _show(cb, pool, "menu", "menu", await menu.main_menu_kb(pool, subscribed))


@router.callback_query(F.data == kb.NAV_ABOUTMENU)
async def nav_aboutmenu(cb: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    await state.clear()
    await _show(cb, pool, "aboutmenu", "aboutmenu", await menu.aboutmenu_kb(pool))


@router.callback_query(F.data == kb.NAV_ABOUT)
async def nav_about(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    await _show(cb, pool, "about", "about", kb.about_kb())


@router.callback_query(F.data == kb.NAV_RULES)
async def nav_rules(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    await _show(cb, pool, "rules", "rules", kb.rules_kb())


@router.callback_query(F.data == kb.NAV_SUPPORT)
async def nav_support(cb: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    await state.clear()
    # Ссылка поддержки — из .env (на этапе 6 переедет в редактируемые настройки).
    url = settings.support_url
    key = "support" if url else "support_no_link"
    await _show(cb, pool, "support", key, kb.support_kb(url or None))


# NAV_JOIN/NAV_TARIFF — роутер tariffs. NAV_MYSUB/NAV_RENEW — оплата (этапы 3–4).
# NAV_PROMO — промокоды (этап 5).
