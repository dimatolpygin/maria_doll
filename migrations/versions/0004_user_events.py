"""user_events — журнал действий пользователей для просмотра пути в админке

Append-only лог: каждое нажатие кнопки / команда / сообщение пишется строкой
(middleware). Нужен, чтобы в админке смотреть «путь» пользователя. Рост таблицы
ограничивается подрезкой хвоста на вставке (repo.add_event), отдельная чистка не нужна.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_user_events"
down_revision: Union[str, None] = "0003_menu_buttons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_user_events_tg_id_id", "user_events", ["tg_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_user_events_tg_id_id", table_name="user_events")
    op.drop_table("user_events")
