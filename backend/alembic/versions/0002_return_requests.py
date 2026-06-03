"""return_requests tablosu

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "return_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("order_no", sa.String(32), nullable=False, index=True),
        sa.Column("customer_email", sa.String(255), nullable=False, index=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("items", _json(), nullable=False),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="requested", index=True),
        sa.Column("admin_note", sa.Text, nullable=True),
        sa.Column("processed_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("return_requests")
