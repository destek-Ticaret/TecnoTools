"""customer auth: password + refresh + reset

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # customers tablosuna üyelik alanları
    op.add_column("customers", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "customers",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "customers",
        sa.Column("verification_token_hash", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_customers_verification_token_hash",
        "customers",
        ["verification_token_hash"],
    )
    op.add_column(
        "customers",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "customers",
        sa.Column("marketing_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "customers",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "customer_refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_customer_refresh_tokens_token_hash",
        "customer_refresh_tokens",
        ["token_hash"],
    )

    op.create_table(
        "customer_password_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_customer_password_reset_tokens_token_hash",
        "customer_password_reset_tokens",
        ["token_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_customer_password_reset_tokens_token_hash", table_name="customer_password_reset_tokens")
    op.drop_table("customer_password_reset_tokens")
    op.drop_index("ix_customer_refresh_tokens_token_hash", table_name="customer_refresh_tokens")
    op.drop_table("customer_refresh_tokens")
    op.drop_column("customers", "last_login_at")
    op.drop_column("customers", "marketing_opt_in")
    op.drop_column("customers", "is_active")
    op.drop_index("ix_customers_verification_token_hash", table_name="customers")
    op.drop_column("customers", "verification_token_hash")
    op.drop_column("customers", "is_verified")
    op.drop_column("customers", "password_hash")
