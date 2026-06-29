"""Ürünlere sipariş başına maksimum adet (max_per_order)

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("max_per_order", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "max_per_order")
