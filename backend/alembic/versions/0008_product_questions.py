"""Product questions (ürün soru-cevap)

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("customer_name", sa.String(length=255), nullable=False),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.String(length=64), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_product_questions_product_id", "product_questions", ["product_id"])
    op.create_index("ix_product_questions_is_published", "product_questions", ["is_published"])
    op.create_index("ix_product_questions_created_at", "product_questions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_product_questions_created_at", table_name="product_questions")
    op.drop_index("ix_product_questions_is_published", table_name="product_questions")
    op.drop_index("ix_product_questions_product_id", table_name="product_questions")
    op.drop_table("product_questions")
