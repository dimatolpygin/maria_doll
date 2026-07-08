"""admin_users + broadcasts — фундамент веб-админки (этап 6)

Revision ID: 0009_admin_broadcasts
Revises: 0008_promocodes
Create Date: 2026-07-08

Две таблицы под веб-кабинет заказчицы:
  · admin_users — учётки веб-входа (логин + pbkdf2-хэш пароля). Это НЕ Telegram-админы
    (`admin_ids`) — отдельная сущность для входа в панель.
  · broadcasts — очередь рассылок из админки. Веб-процесс кладёт задачу
    (status='pending'), бот-джоб (services.broadcasts) забирает и рассылает по сегменту.
    В отличие от аналога — БЕЗ фото (у нас нет S3): рассылки только текстовые.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_admin_broadcasts"
down_revision: Union[str, None] = "0008_promocodes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ux_admin_users_login", "admin_users", ["login"], unique=True)

    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Сегмент аудитории: all / active / former / never (см. repo.AUDIENCE_*).
        sa.Column("audience", sa.Text(), nullable=False),
        # Текст рассылки (HTML).
        sa.Column("body", sa.Text(), nullable=False),
        # pending → sending → done (бот-джоб двигает статус).
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sent", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("blocked", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Логин админа веб-панели, создавшего рассылку.
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Быстрый отбор ожидающих рассылок ботом.
    op.create_index(
        "ix_broadcasts_pending", "broadcasts", ["id"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_broadcasts_pending", table_name="broadcasts")
    op.drop_table("broadcasts")
    op.drop_index("ux_admin_users_login", table_name="admin_users")
    op.drop_table("admin_users")
