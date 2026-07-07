"""payments + subscriptions — оплата Продамус (разовая) и активные подписки

Revision ID: 0006_payments_subscriptions
Revises: 0005_tariffs
Create Date: 2026-07-07

Этап 2. Две таблицы:
  · payments      — журнал платежей (pending → succeeded/canceled). order_num —
                    наш идентификатор заказа, его же Продамус возвращает в вебхуке;
                    по нему обеспечивается идемпотентность (повторный вебхук не
                    создаёт вторую подписку). Поля kind/raw/payment_type заложены
                    под будущие реккуренты (этап 8) и аудит.
  · subscriptions — активные подписки: старт/окончание, зафиксированная цена и тариф.

Схема maria_doll (search_path из migrations/env.py) — чужие таблицы не трогаем.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_payments_subscriptions"
down_revision: Union[str, None] = "0005_tariffs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # Наш идентификатор заказа: отдаём Продамусу как order_id, он возвращает его
        # в вебхуке как order_num. UNIQUE → идемпотентность приёма уведомлений.
        sa.Column("order_num", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "tg_id", sa.BigInteger(),
            sa.ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
        ),
        # Тариф мог быть удалён из админки — SET NULL, сумму/срок храним отдельно.
        sa.Column(
            "tariff_id", sa.Integer(),
            sa.ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False, server_default="month"),
        # Зафиксированная сумма к оплате, ₽ (сверяется с суммой из вебхука).
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        # purchase / renewal — под будущие продления и реккуренты (этап 8).
        sa.Column("kind", sa.Text(), nullable=False, server_default="purchase"),
        # pending → succeeded / canceled.
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("pay_url", sa.Text(), nullable=True),
        # Данные из вебхука Продамуса.
        sa.Column("prodamus_order_id", sa.Text(), nullable=True),
        sa.Column("payment_type", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_payments_tg_id", "payments", ["tg_id"])
    op.create_index("ix_payments_status", "payments", ["status"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tg_id", sa.BigInteger(),
            sa.ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "payment_id", sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "tariff_id", sa.Integer(),
            sa.ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True,
        ),
        # Зафиксированная цена подписки — по ней пойдут продления (история цены).
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False, server_default="month"),
        sa.Column(
            "start_date", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("end_date", sa.TIMESTAMP(timezone=True), nullable=False),
        # active → expired / canceled (кик из группы и пометка — этап 3).
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_subscriptions_tg_status", "subscriptions", ["tg_id", "status"])
    op.create_index("ix_subscriptions_end_date", "subscriptions", ["end_date"])


def downgrade() -> None:
    op.drop_index("ix_subscriptions_end_date", table_name="subscriptions")
    op.drop_index("ix_subscriptions_tg_status", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_tg_id", table_name="payments")
    op.drop_table("payments")
