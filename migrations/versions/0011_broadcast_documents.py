"""broadcasts.documents — файлы (pdf/csv/xlsx/…) в рассылках через S3

Revision ID: 0011_broadcast_documents
Revises: 0010_broadcast_photos
Create Date: 2026-07-13

Веб-админка заливает прикреплённые к рассылке файлы (pdf, csv, xlsx, xls, docx, doc,
html, txt) в S3 (services.storage) и хранит здесь только публичные URL с исходным
именем: JSON-массив объектов {"url": "...", "name": "план_питания.pdf"}. Бот-джоб
шлёт каждый файл через send_document (URLInputFile с именем, чтобы у получателя было
читаемое имя файла, а не uuid). NULL/[] → в рассылке файлов нет.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_broadcast_documents"
down_revision: Union[str, None] = "0010_broadcast_photos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "broadcasts",
        sa.Column(
            "documents", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
    )


def downgrade() -> None:
    op.drop_column("broadcasts", "documents")
