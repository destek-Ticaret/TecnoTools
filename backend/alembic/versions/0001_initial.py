"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-14

Tüm modelleri tek seferde oluşturur. Üretim deploy'da:
    alembic upgrade head
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    """Postgres'te JSONB, diğer DB'lerde JSON."""
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # ── users ──
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("email", sa.String(255), nullable=True, unique=True, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="admin"),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("recovery_code_hash", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── categories ──
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )

    # ── products ──
    op.create_table(
        "products",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sub", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon", sa.String(8), nullable=True),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("old_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("stock", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rating", sa.Numeric(3, 2), nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("badge", _json(), nullable=True),
        sa.Column("features", _json(), nullable=True),
        sa.Column("images", _json(), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── customers ──
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("city", sa.String(64), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── orders ──
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_no", sa.String(32), nullable=False, unique=True, index=True),
        sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("customer_email", sa.String(255), nullable=False),
        sa.Column("customer_phone", sa.String(32), nullable=False),
        sa.Column("customer_city", sa.String(64), nullable=True),
        sa.Column("customer_address", sa.Text, nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("coupon_code", sa.String(64), nullable=True),
        sa.Column("tax", sa.Numeric(12, 2), nullable=False),
        sa.Column("shipping", sa.Numeric(12, 2), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True),
        sa.Column("payment_status", sa.String(16), nullable=False, server_default="initiated", index=True),
        sa.Column("payment_method", sa.String(32), nullable=True),
        sa.Column("payment_brand", sa.String(16), nullable=True),
        sa.Column("payment_last4", sa.String(4), nullable=True),
        sa.Column("tracking_no", sa.String(64), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("admin_notes", _json(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── order_items ──
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("icon", sa.String(8), nullable=True),
        sa.Column("image", sa.Text, nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
    )

    # ── coupons ──
    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("min_order", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("max_uses", sa.Integer, nullable=True),
        sa.Column("used_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── stock_movements ──
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("delta", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("order_no", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
    )

    # ── reservations ──
    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False, index=True),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.UniqueConstraint("session_id", "product_id", name="uq_reservation_session_product"),
    )

    # ── audit_logs ──
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("actor", sa.String(64), nullable=False, server_default="system"),
        sa.Column("action", sa.String(64), nullable=False, index=True),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("meta", _json(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
    )

    # ── newsletter_subscribers ──
    op.create_table(
        "newsletter_subscribers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── analytics_events ──
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event", sa.String(64), nullable=False, index=True),
        sa.Column("path", sa.String(255), nullable=True),
        sa.Column("referrer", sa.String(255), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True, index=True),
        sa.Column("meta", _json(), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
    )

    # ── newsletter_campaigns ──
    op.create_table(
        "newsletter_campaigns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("html_body", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("total_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── refresh_tokens ──
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── password_reset_tokens ──
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── order_counters ──
    op.create_table(
        "order_counters",
        sa.Column("year", sa.BigInteger, primary_key=True),
        sa.Column("seq", sa.BigInteger, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("order_counters")
    op.drop_table("password_reset_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("newsletter_campaigns")
    op.drop_table("analytics_events")
    op.drop_table("newsletter_subscribers")
    op.drop_table("audit_logs")
    op.drop_table("reservations")
    op.drop_table("stock_movements")
    op.drop_table("coupons")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("customers")
    op.drop_table("products")
    op.drop_table("categories")
    op.drop_table("users")
