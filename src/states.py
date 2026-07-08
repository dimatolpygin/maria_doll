"""FSM-состояния aiogram (хранятся в Redis). Пока только ввод промокода (этап 5)."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PromoStates(StatesGroup):
    # Ждём текст промокода. В data — tariff_id выбранного тарифа.
    waiting_code = State()
