"""Shipment events + carrier columns on orders

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("orders") as b:
        b.add_column(sa.Column("carrier", sa.String(length=16), nullable=True))
        b.add_column(sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_tracking_sync_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "shipment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_no", sa.String(length=32), nullable=False),
        sa.Column("carrier", sa.String(length=16), nullable=False),
        sa.Column("tracking_no", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("raw_status", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="webhook"),
        sa.Column("raw_payload", _json_type(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("carrier", "tracking_no", "code", "occurred_at", name="uq_shipment_event_dedupe"),
    )
    op.create_index("ix_shipment_events_order_no", "shipment_events", ["order_no"])
    op.create_index("ix_shipment_events_carrier", "shipment_events", ["carrier"])
    op.create_index("ix_shipment_events_tracking_no", "shipment_events", ["tracking_no"])
    op.create_index("ix_shipment_events_code", "shipment_events", ["code"])
    op.create_index("ix_shipment_events_occurred_at", "shipment_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_shipment_events_occurred_at", table_name="shipment_events")
    op.drop_index("ix_shipment_events_code", table_name="shipment_events")
    op.drop_index("ix_shipment_events_tracking_no", table_name="shipment_events")
    op.drop_index("ix_shipment_events_carrier", table_name="shipment_events")
    op.drop_index("ix_shipment_events_order_no", table_name="shipment_events")
    op.drop_table("shipment_events")
    with op.batch_alter_table("orders") as b:
        b.drop_column("last_tracking_sync_at")
        b.drop_column("delivered_at")
        b.drop_column("shipped_at")
        b.drop_column("carrier")
