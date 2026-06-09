"""Eksik tablolar: product_reviews, stock_notifications, invoices,
invoice_counters, chat_sessions, chat_messages

Modellerde tanımlı olup migration'ı yazılmamış tablolar. Hepsi IF NOT EXISTS
ile idempotent oluşturulur.

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── product_reviews ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_reviews (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            customer_name VARCHAR(255) NOT NULL,
            customer_email VARCHAR(255),
            rating INTEGER NOT NULL,
            title VARCHAR(255),
            body TEXT NOT NULL,
            is_approved BOOLEAN NOT NULL DEFAULT false,
            order_no VARCHAR(32),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_reviews_product_id ON product_reviews (product_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_reviews_is_approved ON product_reviews (is_approved)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_reviews_created_at ON product_reviews (created_at)")

    # ── stock_notifications ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS stock_notifications (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            email VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            notified_at TIMESTAMPTZ,
            CONSTRAINT uq_stock_notif UNIQUE (product_id, email)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_stock_notifications_product_id ON stock_notifications (product_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_stock_notifications_email ON stock_notifications (email)")

    # ── invoices ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            order_no VARCHAR(32) NOT NULL,
            invoice_no VARCHAR(32) NOT NULL UNIQUE,
            ettn VARCHAR(64) UNIQUE,
            uuid VARCHAR(64),
            kind VARCHAR(16) NOT NULL DEFAULT 'earsiv',
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            customer_name VARCHAR(255) NOT NULL,
            customer_email VARCHAR(255) NOT NULL,
            customer_phone VARCHAR(32) NOT NULL,
            customer_address TEXT NOT NULL,
            tax_no VARCHAR(16),
            tax_office VARCHAR(128),
            company_title VARCHAR(255),
            subtotal NUMERIC(12, 2) NOT NULL,
            tax_rate NUMERIC(5, 2) NOT NULL DEFAULT 20,
            tax_amount NUMERIC(12, 2) NOT NULL,
            total NUMERIC(12, 2) NOT NULL,
            items JSONB NOT NULL,
            provider VARCHAR(32),
            provider_response JSONB,
            pdf_url VARCHAR(512),
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_invoices_order_id ON invoices (order_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_invoices_order_no ON invoices (order_no)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_invoices_status ON invoices (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_invoices_created_at ON invoices (created_at)")

    # ── invoice_counters ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS invoice_counters (
            year BIGINT PRIMARY KEY,
            seq BIGINT NOT NULL DEFAULT 0
        )
    """)

    # ── chat_sessions ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id SERIAL PRIMARY KEY,
            session_id VARCHAR(64) NOT NULL UNIQUE,
            customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
            customer_name VARCHAR(255),
            customer_email VARCHAR(255),
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            unread_admin INTEGER NOT NULL DEFAULT 0,
            unread_customer INTEGER NOT NULL DEFAULT 0,
            last_message_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_status ON chat_sessions (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_last_message_at ON chat_sessions (last_message_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_created_at ON chat_sessions (created_at)")

    # ── chat_messages ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            session_pk INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            sender VARCHAR(16) NOT NULL,
            sender_name VARCHAR(255),
            body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_messages_session_pk ON chat_messages (session_pk)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_messages_created_at ON chat_messages (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_messages")
    op.execute("DROP TABLE IF EXISTS chat_sessions")
    op.execute("DROP TABLE IF EXISTS invoice_counters")
    op.execute("DROP TABLE IF EXISTS invoices")
    op.execute("DROP TABLE IF EXISTS stock_notifications")
    op.execute("DROP TABLE IF EXISTS product_reviews")
