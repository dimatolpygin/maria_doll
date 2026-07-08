"""broadcasts.photos — фото в рассылках через S3 (этап 6)

Revision ID: 0010_broadcast_photos
Revises: 0009_admin_broadcasts
Create Date: 2026-07-08

Веб-админка заливает фото рассылки в S3 (services.storage) и хранит здесь только
публичные URL (JSON-массив объектов {"url": "..."}). Бот-джоб шлёт их по ссылке:
одно фото → send_photo, несколько → media_group. NULL/[] → рассылка только текстовая.
Картинки экранов уже поддержаны колонкой screen_texts.photo_url (миграция 0002).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_broadcast_photos"
down_revision: Union[str, None] = "0009_admin_broadcasts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "broadcasts",
        sa.Column(
            "photos", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
    )


def downgrade() -> None:
    op.drop_column("broadcasts", "photos")
