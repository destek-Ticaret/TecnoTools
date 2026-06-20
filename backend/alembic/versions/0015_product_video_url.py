"""Ürünlere video_url alanı (YouTube/Vimeo gömülü oynatıcı)

Revision ID: 0015
Revises: 0014
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("video_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "video_url")
