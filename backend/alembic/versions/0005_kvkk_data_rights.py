"""KVKK: data deletion requests + consent logs

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    """Postgres'te JSONB, SQLite'da JSON — models.JSONType ile aynı strateji."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "data_deletion_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("email_snapshot", sa.String(length=255), nullable=False),
        sa.Column("name_snapshot", sa.String(length=255), nullable=True),
        sa.Column("token_hash", sa.String(length=128), nullable=True, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("result", _json_type(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ddr_customer_id", "data_deletion_requests", ["customer_id"])
    op.create_index("ix_ddr_email_snapshot", "data_deletion_requests", ["email_snapshot"])
    op.create_index("ix_ddr_token_hash", "data_deletion_requests", ["token_hash"])
    op.create_index("ix_ddr_status", "data_deletion_requests", ["status"])
    op.create_index("ix_ddr_created_at", "data_deletion_requests", ["created_at"])

    op.create_table(
        "consent_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("categories", _json_type(), nullable=False),
        sa.Column("policy_version", sa.String(length=16), server_default="1.0", nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_consent_logs_customer_id", "consent_logs", ["customer_id"])
    op.create_index("ix_consent_logs_session_id", "consent_logs", ["session_id"])
    op.create_index("ix_consent_logs_created_at", "consent_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_consent_logs_created_at", table_name="consent_logs")
    op.drop_index("ix_consent_logs_session_id", table_name="consent_logs")
    op.drop_index("ix_consent_logs_customer_id", table_name="consent_logs")
    op.drop_table("consent_logs")
    op.drop_index("ix_ddr_created_at", table_name="data_deletion_requests")
    op.drop_index("ix_ddr_status", table_name="data_deletion_requests")
    op.drop_index("ix_ddr_token_hash", table_name="data_deletion_requests")
    op.drop_index("ix_ddr_email_snapshot", table_name="data_deletion_requests")
    op.drop_index("ix_ddr_customer_id", table_name="data_deletion_requests")
    op.drop_table("data_deletion_requests")
