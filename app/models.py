from datetime import date, datetime

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    contact: Mapped[str]
    email: Mapped[str]


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_cents >= 0 AND on_hand >= 0 AND reserved >= 0 AND reserved <= on_hand"),
        CheckConstraint("reorder_threshold >= 0"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    price_cents: Mapped[int]
    on_hand: Mapped[int]
    reserved: Mapped[int]
    reorder_threshold: Mapped[int]


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (CheckConstraint("status IN ('confirmed','fulfilled','cancelled')"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    status: Mapped[str]


class OrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (CheckConstraint("quantity > 0 AND unit_price_cents >= 0"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    unit_price_cents: Mapped[int]


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("order_id"),
        CheckConstraint("status IN ('draft','issued','paid')"),
        CheckConstraint("total_cents >= 0"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    customer_snapshot: Mapped[dict] = mapped_column(JSON)
    lines: Mapped[list] = mapped_column(JSON)
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str]
    total_cents: Mapped[int]


class PendingAction(Base):
    __tablename__ = "pending_actions"
    __table_args__ = (CheckConstraint("state IN ('pending','confirmed','cancelled','expired','stale')"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    preview: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str]
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("pending_actions.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str]
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
