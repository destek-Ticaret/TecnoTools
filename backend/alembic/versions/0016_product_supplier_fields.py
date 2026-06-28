"""Ürünlere dropshipping tedarikçi alanları

Revision ID: 0016
Revises: 0015
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("supplier", sa.String(length=32), nullable=True))
    op.add_column("products", sa.Column("supplier_url", sa.String(length=1024), nullable=True))
    op.add_column(
        "products", sa.Column("supplier_product_id", sa.String(length=128), nullable=True)
    )
    op.add_column("products", sa.Column("supplier_price", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "products",
        sa.Column("supplier_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_products_supplier_product_id", "products", ["supplier_product_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_products_supplier_product_id", table_name="products")
    op.drop_column("products", "supplier_synced_at")
    op.drop_column("products", "supplier_price")
    op.drop_column("products", "supplier_product_id")
    op.drop_column("products", "supplier_url")
    op.drop_column("products", "supplier")
