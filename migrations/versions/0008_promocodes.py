"""promo_codes + promo_redemptions + payments.promo_id — промокоды (этап 5)

Revision ID: 0008_promocodes
Revises: 0007_reminders
Create Date: 2026-07-08

promo_codes — сами коды: тип ('percent' — скидка % от цены тарифа, 'fixed' —
спец-цена в ₽ за период), значение, лимит активаций (NULL = безлимит) и счётчик
использованных, срок действия (NULL = бессрочно), активность. promo_redemptions —
факт применения кода (для соблюдения лимита и истории), уникум по (промокод,
пользователь): один код — одна активация на человека. payments.promo_id связывает
оплату с применённым кодом — при успешной активации создаётся запись redemption и
инкрементится used_count (в транзакции activate_payment, идемпотентно).

Модель проще аналога: тарифы фиксированные (цена за период), поэтому нет ставки
₽/мес, матрицы цен и фиксации цены за подпиской (fixes_price). Схема maria_doll —
чужие таблицы не трогаем.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_promocodes"
down_revision: Union[str, None] = "0007_reminders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Нормализованный код (верхний регистр, без пробелов) — уникален.
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        # Тип: percent (скидка % от цены тарифа) | fixed (спец-цена в ₽ за период).
        sa.Column("kind", sa.Text(), nullable=False),
        # Значение: для percent — процент скидки; для fixed — итоговая цена в ₽.
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        # Лимит активаций (NULL = безлимит) и сколько уже использовано.
        sa.Column("max_activations", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        # Срок действия (NULL = бессрочно).
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "promo_redemptions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("promo_id", sa.Integer(), nullable=False),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column(
            "redeemed_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["promo_id"], ["promo_codes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        # Один промокод — одна активация на пользователя.
        sa.UniqueConstraint("promo_id", "tg_id", name="uq_promo_redemption_user"),
    )

    # Связь платежа с применённым промокодом (NULL = оплата без промокода).
    op.add_column("payments", sa.Column("promo_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "payments_promo_id_fkey", "payments", "promo_codes",
        ["promo_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("payments_promo_id_fkey", "payments", type_="foreignkey")
    op.drop_column("payments", "promo_id")
    op.drop_table("promo_redemptions")
    op.drop_table("promo_codes")
