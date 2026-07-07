"""menu_buttons — переопределение подписи и видимости кнопок меню из веб-админки

Дефолтные подписи/состав живут в реестре `services.menu` (источник истины); эта
таблица хранит ТОЛЬКО переопределения: своя подпись (`label`, NULL = дефолт) и
видимость (`is_visible`). Бот при каждом рендере меню сливает реестр с этой таблицей.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_menu_buttons"
down_revision: Union[str, None] = "0002_screen_texts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "menu_buttons",
        # Ключ кнопки из реестра services.menu (join/mysub/about/…).
        sa.Column("key", sa.Text(), primary_key=True),
        # Своя подпись; NULL → дефолтная из реестра.
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("menu_buttons")
