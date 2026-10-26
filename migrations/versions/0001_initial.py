"""Initial ERP schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("contact", sa.String, nullable=False),
        sa.Column("email", sa.String, nullable=False),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("price_cents", sa.Integer, nullable=False),
        sa.Column("on_hand", sa.Integer, nullable=False),
        sa.Column("reserved", sa.Integer, nullable=False),
        sa.Column("reorder_threshold", sa.Integer, nullable=False),
        sa.CheckConstraint("price_cents >= 0 AND on_hand >= 0 AND reserved >= 0 AND reserved <= on_hand"),
        sa.CheckConstraint("reorder_threshold >= 0"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("customer_id", sa.String, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.CheckConstraint("status IN ('confirmed','fulfilled','cancelled')"),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_table(
        "order_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.String, sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("product_id", sa.String, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_price_cents", sa.Integer, nullable=False),
        sa.CheckConstraint("quantity > 0 AND unit_price_cents >= 0"),
    )
    op.create_index("ix_order_lines_order_id", "order_lines", ["order_id"])
    op.create_table(
        "invoices",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("order_id", sa.String, sa.ForeignKey("orders.id"), nullable=False, unique=True),
        sa.Column("customer_id", sa.String, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("customer_snapshot", sa.JSON, nullable=False),
        sa.Column("lines", sa.JSON, nullable=False),
        sa.Column("issue_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("total_cents", sa.Integer, nullable=False),
        sa.CheckConstraint("status IN ('draft','issued','paid')"),
        sa.CheckConstraint("total_cents >= 0"),
    )
    op.create_index("ix_invoices_customer_id", "invoices", ["customer_id"])
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("session_id", sa.String, nullable=False),
        sa.Column("order_id", sa.String, sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("preview", sa.JSON, nullable=False),
        sa.Column("fingerprint", sa.String, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("invoice_id", sa.String, sa.ForeignKey("invoices.id")),
        sa.CheckConstraint("state IN ('pending','confirmed','cancelled','expired','stale')"),
    )
    op.create_index("ix_pending_actions_session_id", "pending_actions", ["session_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("action_id", sa.String, sa.ForeignKey("pending_actions.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("invoice_id", sa.String, sa.ForeignKey("invoices.id")),
    )
    op.create_index("ix_audit_events_action_id", "audit_events", ["action_id"])


def downgrade():
    for table in (
        "audit_events",
        "pending_actions",
        "invoices",
        "order_lines",
        "orders",
        "products",
        "customers",
    ):
        op.drop_table(table)
