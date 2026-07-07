"""Сбор всех роутеров."""
from aiogram import Router

from . import join, navigation, start, subscription, tariffs


def get_main_router() -> Router:
    router = Router()
    router.include_router(start.router)
    router.include_router(tariffs.router)
    router.include_router(subscription.router)
    router.include_router(navigation.router)
    router.include_router(join.router)
    return router
