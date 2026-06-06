"""Ürün varyantları (basit liste) + order_items.variant_id/variant_name

Revision ID: 0010
Revises: 0009
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONType = JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "product_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("options", JSONType, nullable=True),
        sa.Column("sku", sa.String(length=64), nullable=True),
        sa.Column("barcode", sa.String(length=64), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_product_variants_product_id", "product_variants", ["product_id"])

    op.add_column("order_items", sa.Column("variant_id", sa.Integer(), nullable=True))
    op.add_column("order_items", sa.Column("variant_name", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        "fk_order_items_variant_id",
        "order_items",
        "product_variants",
        ["variant_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_order_items_variant_id", "order_items", type_="foreignkey")
    op.drop_column("order_items", "variant_name")
    op.drop_column("order_items", "variant_id")
    op.drop_table("product_variants")
