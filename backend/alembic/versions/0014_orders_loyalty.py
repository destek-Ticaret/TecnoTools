"""orders tablosuna sadakat puanı harcama alanları

loyalty_points_used: bu siparişte harcanan puan (bakiye = kazanılan - harcanan)
loyalty_discount:    puanın TRY karşılığı indirim (kupon indiriminden ayrı, denetlenebilir)

Revision ID: 0014
Revises: 0013
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS loyalty_points_used INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS loyalty_discount NUMERIC(12, 2) NOT NULL DEFAULT 0
    """)


def downgrade() -> None:
    op.drop_column("orders", "loyalty_discount")
    op.drop_column("orders", "loyalty_points_used")
