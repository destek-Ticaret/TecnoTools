"""Admin panel: granüler yetki, banner, vitrin düzeni, blog, CMS sayfa, fiyat kuralı

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Postgres'te JSONB, SQLite'da JSON
JSONType = JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # Granüler yetki override'ı
    op.add_column("users", sa.Column("permissions", JSONType, nullable=True))

    # Ürün alış maliyeti — kâr raporu + marj-tabanlı fiyatlandırma
    op.add_column("products", sa.Column("cost", sa.Numeric(10, 2), nullable=True))

    op.create_table(
        "banners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("subtitle", sa.String(length=255), nullable=True),
        sa.Column("image_url", sa.String(length=512), nullable=False),
        sa.Column("mobile_image_url", sa.String(length=512), nullable=True),
        sa.Column("link_url", sa.String(length=512), nullable=True),
        sa.Column("cta_text", sa.String(length=64), nullable=True),
        sa.Column("position", sa.String(length=16), nullable=False, server_default="hero"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_banners_position", "banners", ["position"])
    op.create_index("ix_banners_is_active", "banners", ["is_active"])

    op.create_table(
        "homepage_sections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("config", JSONType, nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_homepage_sections_kind", "homepage_sections", ["kind"])
    op.create_index("ix_homepage_sections_sort_order", "homepage_sections", ["sort_order"])
    op.create_index("ix_homepage_sections_is_active", "homepage_sections", ["is_active"])

    op.create_table(
        "blog_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cover_image", sa.String(length=512), nullable=True),
        sa.Column("tags", JSONType, nullable=True),
        sa.Column("author", sa.String(length=64), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meta_title", sa.String(length=255), nullable=True),
        sa.Column("meta_description", sa.String(length=320), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_blog_posts_slug", "blog_posts", ["slug"], unique=True)
    op.create_index("ix_blog_posts_is_published", "blog_posts", ["is_published"])
    op.create_index("ix_blog_posts_published_at", "blog_posts", ["published_at"])

    op.create_table(
        "cms_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_in_footer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meta_title", sa.String(length=255), nullable=True),
        sa.Column("meta_description", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cms_pages_slug", "cms_pages", ["slug"], unique=True)
    op.create_index("ix_cms_pages_is_published", "cms_pages", ["is_published"])

    op.create_table(
        "pricing_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False, server_default="all"),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(length=16), nullable=False, server_default="percent"),
        sa.Column("value", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("min_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("max_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("only_in_stock", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_affected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pricing_rules_priority", "pricing_rules", ["priority"])
    op.create_index("ix_pricing_rules_is_active", "pricing_rules", ["is_active"])


def downgrade() -> None:
    op.drop_table("pricing_rules")
    op.drop_table("cms_pages")
    op.drop_table("blog_posts")
    op.drop_table("homepage_sections")
    op.drop_table("banners")
    op.drop_column("products", "cost")
    op.drop_column("users", "permissions")
