"""orders tablosuna e-arşiv fatura alanları (tax_no, tax_office, company_title)

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS tax_no VARCHAR(16),
            ADD COLUMN IF NOT EXISTS tax_office VARCHAR(128),
            ADD COLUMN IF NOT EXISTS company_title VARCHAR(255)
    """)


def downgrade() -> None:
    op.drop_column("orders", "company_title")
    op.drop_column("orders", "tax_office")
    op.drop_column("orders", "tax_no")
