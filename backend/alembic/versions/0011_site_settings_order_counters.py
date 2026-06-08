"""site_settings ve order_counters tabloları

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS site_settings (
            key VARCHAR(64) NOT NULL PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS order_counters (
            year BIGINT NOT NULL PRIMARY KEY,
            seq BIGINT NOT NULL DEFAULT 0
        )
    """)


def downgrade() -> None:
    op.drop_table("order_counters")
    op.drop_table("site_settings")
