"""tariffs — фиксированные тарифы подписки (правятся из веб-админки)

В ОТЛИЧИЕ от аналога (скрытые авто-ступени по числу мест) здесь простые
фиксированные тарифы: длительность + цена. Заказчица правит цены/состав из админки,
бот читает актуальные значения при каждом показе — правка применяется без рестарта.

Сид — тарифы из переписки: 1 мес 990 · 3 мес 2370 · 6 мес 3540 · 12 мес 5990 ₽.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_tariffs"
down_revision: Union[str, None] = "0004_user_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # Длительность подписки и её единица (month по умолчанию; day/hour/minute — для теста).
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False, server_default="month"),
        # Цена за весь период, ₽.
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        # Необязательная подпись тарифа (если пусто — генерится из длительности).
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Сид фиксированных тарифов (переписка с заказчицей).
    op.execute(
        """
        INSERT INTO tariffs (months, unit, price, sort_order) VALUES
            (1,  'month',  990.00, 1),
            (3,  'month', 2370.00, 2),
            (6,  'month', 3540.00, 3),
            (12, 'month', 5990.00, 4)
        """
    )


def downgrade() -> None:
    op.drop_table("tariffs")
