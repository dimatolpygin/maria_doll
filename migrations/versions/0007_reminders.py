"""bot_settings + subscription_reminders — напоминания о продлении (этап 4)

Revision ID: 0007_reminders
Revises: 0006_payments_subscriptions
Create Date: 2026-07-08

Две таблицы:
  · bot_settings          — key/value рантайм-настройки, правятся из админки на лету
                            (этап 6) без рестарта: единица и пороги напоминаний.
                            Значения хранятся строками, типизация — на стороне
                            приложения; отсутствие ключа = дефолт из `.env`.
  · subscription_reminders — учёт отправленных напоминаний: одна строка на пару
                            (подписка, тип). PK (subscription_id, kind) +
                            INSERT ... ON CONFLICT DO NOTHING даёт атомарную
                            «заявку» на отправку — повторные прогоны планировщика
                            и гонка параллельных проходов не пошлют дважды.

Схема maria_doll (search_path из migrations/env.py) — чужие таблицы не трогаем.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_reminders"
down_revision: Union[str, None] = "0006_payments_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "subscription_reminders",
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        # Тип напоминания: early | soon | last (порог каждого задаётся настройкой).
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "sent_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("subscription_id", "kind"),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"
        ),
    )


def downgrade() -> None:
    op.drop_table("subscription_reminders")
    op.drop_table("bot_settings")
