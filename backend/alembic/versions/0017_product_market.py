"""Ürünlere pazar (market) alanı: tr | intl | both

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("market", sa.String(length=8), nullable=False, server_default="both"),
    )
    # Mevcut dropship ürünlerini yurt dışı pazara taşı
    op.execute("UPDATE products SET market = 'intl' WHERE supplier IS NOT NULL")


def downgrade() -> None:
    op.drop_column("products", "market")
