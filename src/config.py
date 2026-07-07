"""Конфигурация проекта. Все значения берутся из переменных окружения (.env)."""
from __future__ import annotations

import re

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Telegram ─────────────────────────────────────────────────────────────
    bot_token: str
    admin_ids: str = ""

    # Закрытая группа клуба (этап 3). На этапе 0 не обязательно.
    club_chat_id: str = ""
    club_chat_invite_url: str = ""

    # Ссылка на аккаунт поддержки (кнопка «Перейти» в разделе «Поддержка»).
    # Обычно задаётся из админки (bot_settings) — это .env-fallback.
    support_url: str = ""

    # ── База данных ──────────────────────────────────────────────────────────
    # DSN для asyncpg (runtime). Пример: postgresql://user:pass@host:5432/db
    database_url: str
    # Отдельная схема под этот бот — база не пустая, чужие таблицы НЕ трогаем.
    db_schema: str = "maria_doll"

    # ── Redis (FSM + кеш) ────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Продамус (этап 2) ────────────────────────────────────────────────────
    # Адрес платёжной страницы payform (без завершающего слэша обяз. не важно).
    #   Пример: https://dolbikfit.payform.ru
    prodamus_form_url: str = ""
    # Секретный ключ платёжной страницы (из ЛК Продамуса, раздел «Настройки»):
    # им подписываем ссылку и проверяем подпись входящих вебхуков.
    prodamus_secret_key: str = ""
    # Куда Продамус вернёт пользователя после оплаты. Если пусто — на платёжной
    # странице вернётся стандартная страница успеха/возврата Продамуса.
    prodamus_return_url: str = ""
    # Демо-режим payform: в ссылку добавляется demo_mode=1 — платёж проходит без
    # реального списания (для тестов). На подпись вебхука не влияет, если демо
    # включён мерчантом в настройках страницы (боевой ключ остаётся рабочим).
    prodamus_demo_mode: bool = False
    # Заготовка под будущие реккуренты (этап 8): включение автосписаний.
    prodamus_recurrent_enabled: bool = False
    # Приёмник вебхука Продамуса (подтверждение оплаты — решение заказчицы).
    # Порт слушает встроенный aiohttp-сервер; путь настраивается в ЛК Продамуса
    # как «URL для уведомлений» (на проде за nginx: https://dolbikfit.ru<path>).
    prodamus_webhook_port: int = 8080
    prodamus_webhook_path: str = "/prodamus/webhook"
    # Как часто проверять окончания подписок (минуты) — кик из группы + уведомление.
    expiry_check_interval_min: int = 30

    # ── Напоминания о продлении (этап 4) ─────────────────────────────────────
    reminder_check_interval_min: int = 60
    # Единица порогов напоминаний: minute/hour/day/month. Для теста — minute.
    reminder_unit: str = "day"
    # Пороги (в reminder_unit): сколько целых единиц осталось до конца. early ≥ soon ≥ last.
    reminder_early_offset: int = 3
    reminder_soon_offset: int = 1
    reminder_last_offset: int = 0

    # Чек 54-ФЗ: ВРЕМЕННАЯ заглушка email покупателя (fallback, пока сбор email выключен).
    receipt_email_placeholder: str = "receipt@example.com"

    # ── Веб-админка (этап 6) ─────────────────────────────────────────────────
    # Секрет для подписи cookie-сессии админки. В ПРОДЕ задать длинный случайный.
    admin_web_secret: str = "dev-insecure-change-me"
    # Сид первого администратора веб-панели (создаётся при первом старте, если пусто).
    admin_web_login: str = "admin"
    admin_web_password: str = ""

    # ── Прочее ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @field_validator("db_schema")
    @classmethod
    def _validate_schema(cls, v: str) -> str:
        # Защита от инъекции: имя схемы попадает в DDL/search_path напрямую.
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", v):
            raise ValueError(f"Недопустимое имя схемы: {v}")
        return v

    @property
    def admin_id_list(self) -> list[int]:
        return [int(x) for x in self.admin_ids.replace(" ", "").split(",") if x]

    @property
    def club_chat_id_int(self) -> int | None:
        """Числовой id группы клуба (для сравнения с chat_join_request). None — не задан."""
        raw = self.club_chat_id.strip()
        if not raw or not raw.lstrip("-").isdigit():
            return None
        return int(raw)

    @property
    def sqlalchemy_url(self) -> str:
        """URL для SQLAlchemy/Alembic — тот же Postgres, но через драйвер asyncpg."""
        url = self.database_url
        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
