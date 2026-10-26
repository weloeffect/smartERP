from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import AuditEvent, Customer, Invoice, OrderLine, PendingAction, Product
from app.schemas import Query
from app.seed import seed
from app.services import DomainError
from tests.conftest import AS_OF, SESSION_A, SESSION_B


def test_seed_migration_and_queries(database):
    erp, sessions, engine = database
    assert seed(sessions, AS_OF) is False
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
        assert [
            c.scalar(text(f"SELECT COUNT(*) FROM {table}"))
            for table in ("customers", "products", "orders", "invoices")
        ] == [20, 30, 60, 40]
    found = erp.browse("customers", Query(q="acme"))
    assert [c["id"] for c in found["items"]] == ["CUS-001", "CUS-002"]
    assert erp.browse("customers", Query(q="' OR 1=1 --"))["total"] == 0
    assert erp.browse("customers", Query(q="nonexistent"))["total"] == 0
    assert erp.browse("orders", Query(offset=999))["items"] == []
    assert erp.detail("orders", "ORD-1042")["invoice_eligible"] is True


def test_stock_and_balances(database):
    erp, sessions, _ = database
    stock = erp.browse("stock", Query(low_stock=True))
    assert stock["total"] == 6
    assert {x["available"] for x in stock["items"]} == {5}
    invoices = erp.browse("invoices", Query(overdue=True, limit=1))
    with sessions() as s:
        overdue = s.scalars(select(Invoice).where(Invoice.status == "issued", Invoice.due_date < AS_OF)).all()
        expected = sum(i.total_cents for i in overdue)
        assert invoices["total"] == len(overdue)
        assert invoices["aggregates"] == {"total_cents": expected, "outstanding_cents": expected}
        customer_expected = (
            s.scalar(
                select(func.sum(Invoice.total_cents)).where(
                    Invoice.customer_id == "CUS-001", Invoice.status == "issued"
                )
            )
            or 0
        )
    assert erp.customer_overview("CUS-001")["outstanding_cents"] == customer_expected


def test_dashboard_charts_reconcile_with_erp(database):
    erp, _, _ = database
    data = erp.dashboard()
    charts = data["charts"]
    assert [point["month"] for point in charts["sales"]] == [
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    assert all(point["total_cents"] == 0 for point in charts["sales"][:4])
    report = erp.sales_summary(AS_OF.replace(month=5, day=1), AS_OF)
    assert sum(point["total_cents"] for point in charts["sales"]) == report["total_cents"]
    assert charts["sales"][-1]["total_cents"] == erp.sales_summary(AS_OF.replace(day=1), AS_OF)["total_cents"]
    assert [(c["id"], c["total_cents"]) for c in charts["top_customers"]] == [
        (c["customer_id"], c["total_cents"]) for c in report["customers"]
    ]
    balances = charts["invoice_balances"]
    assert balances["overdue"] == data["overdue_cents"]
    assert balances["issued"] + balances["overdue"] == data["outstanding_cents"]
    assert sum(balances.values()) == erp.browse("invoices", Query(limit=100))["aggregates"]["total_cents"]
    assert len(charts["stock"]) == 6
    assert all(p["low_stock"] and p["available"] == 5 for p in charts["stock"])


def test_dashboard_charts_without_records(database):
    erp, sessions, _ = database
    with sessions.begin() as s:
        for model in (Invoice, OrderLine):
            for item in s.scalars(select(model)):
                s.delete(item)
        s.flush()
        from app.models import Order

        for model in (Order, Product, Customer):
            for item in s.scalars(select(model)):
                s.delete(item)
            s.flush()
    data = erp.dashboard()
    assert data["orders"] == data["customers"] == data["stock_alerts"] == 0
    assert all(point["total_cents"] == 0 for point in data["charts"]["sales"])
    assert not any(data["charts"]["invoice_balances"].values())
    assert data["charts"]["top_customers"] == data["charts"]["stock"] == []


def test_sales_summary_and_filters(database):
    erp, _, engine = database
    report = erp.sales_summary(AS_OF.replace(day=1), AS_OF, 5)
    with engine.connect() as c:
        expected = c.execute(
            text("""SELECT o.customer_id, SUM(l.quantity*l.unit_price_cents) AS total
          FROM orders o JOIN order_lines l ON l.order_id=o.id
          WHERE o.status != 'cancelled' AND o.date BETWEEN '2026-10-01' AND '2026-10-24'
          GROUP BY o.customer_id ORDER BY total DESC, o.customer_id""")
        ).all()
    assert report["total_cents"] == sum(row.total for row in expected)
    assert [(x["customer_id"], x["total_cents"]) for x in report["customers"]] == list(expected[:5])
    assert report["order_count"] == 18
    assert erp.sales_summary(AS_OF + timedelta(days=1), AS_OF + timedelta(days=5))["total_cents"] == 0
    assert erp.browse("invoices", Query(status="issued", due_before=AS_OF, limit=100))["total"] > 0
    with pytest.raises(DomainError):
        erp.browse("stock", Query(status="paid"))
    with pytest.raises(ValidationError):
        Query(date_from="2026-10-24", date_to="2026-10-01")
    with pytest.raises(ValidationError):
        Query(limit=101)
    with pytest.raises(ValidationError):
        Query(unsupported="x")
    with pytest.raises(DomainError):
        erp.detail("orders", "ORD-9999")


def test_preview_confirm_idempotency_and_stock(database):
    erp, sessions, _ = database
    before = erp.browse("stock", Query(limit=100))
    action = erp.prepare("ORD-1042", SESSION_A)
    assert action["preview"]["due_date"] == "2026-11-23"
    assert action["preview"]["total_cents"] == erp.detail("orders", "ORD-1042")["total_cents"]
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Invoice)) == 40
    first = erp.resolve(action["id"], SESSION_A, "confirm")
    second = erp.resolve(action["id"], SESSION_A, "confirm")
    assert first == second
    assert first["invoice"]["status"] == "draft"
    assert first["invoice"]["lines"] == action["preview"]["lines"]
    assert erp.browse("stock", Query(limit=100)) == before
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Invoice)) == 41
        assert s.scalar(select(func.count()).select_from(AuditEvent)) == 1
    with pytest.raises(DomainError):
        erp.prepare("ORD-1042", SESSION_A)


@pytest.mark.parametrize("order_id", ["ORD-1001", "ORD-1046", "ORD-9999"])
def test_ineligible_order(database, order_id):
    erp, _, _ = database
    with pytest.raises(DomainError):
        erp.prepare(order_id, SESSION_A)


def test_cancel_and_session_isolation(database):
    erp, sessions, _ = database
    action = erp.prepare("ORD-1042", SESSION_A)
    assert erp.actions(SESSION_B)["items"] == []
    with pytest.raises(DomainError) as exc:
        erp.resolve(action["id"], SESSION_B, "confirm")
    assert exc.value.status == 404
    assert erp.resolve(action["id"], SESSION_A, "cancel")["action"]["state"] == "cancelled"
    erp.resolve(action["id"], SESSION_A, "cancel")
    with pytest.raises(DomainError):
        erp.resolve(action["id"], SESSION_A, "confirm")
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Invoice)) == 40
        assert s.scalar(select(func.count()).select_from(AuditEvent)) == 1


@pytest.mark.parametrize("change", ["expiry", "price", "customer", "status"])
def test_expired_and_stale_previews(database, change):
    erp, sessions, _ = database
    action = erp.prepare("ORD-1042", SESSION_A)
    with sessions.begin() as s:
        if change == "expiry":
            s.get(PendingAction, action["id"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "price":
            s.scalar(select(OrderLine).where(OrderLine.order_id == "ORD-1042")).unit_price_cents += 1
        elif change == "customer":
            s.get(Customer, "CUS-002").email = "updated@example.test"
        else:
            from app.models import Order

            s.get(Order, "ORD-1042").status = "cancelled"
    with pytest.raises(DomainError) as exc:
        erp.resolve(action["id"], SESSION_A, "confirm")
    assert exc.value.code == ("expired" if change == "expiry" else "stale")
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Invoice)) == 40
        assert s.get(PendingAction, action["id"]).state == exc.value.code
        assert s.scalar(select(AuditEvent.outcome)) == exc.value.code


def test_concurrent_duplicate_confirmation(database):
    erp, sessions, _ = database
    action = erp.prepare("ORD-1042", SESSION_A)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: erp.resolve(action["id"], SESSION_A, "confirm"), range(4)))
    assert len({r["invoice"]["id"] for r in results}) == 1
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_competing_previews_create_one_invoice(database):
    erp, sessions, _ = database
    actions = [erp.prepare("ORD-1042", SESSION_A), erp.prepare("ORD-1042", SESSION_B)]

    def resolve(pair):
        action, session = pair
        try:
            return erp.resolve(action["id"], session, "confirm")["action"]["state"]
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resolve, zip(actions, [SESSION_A, SESSION_B])))
    assert sorted(results) == ["confirmed", "stale"]
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Invoice)) == 41


def test_database_constraints(database):
    _, sessions, _ = database
    with pytest.raises(IntegrityError), sessions.begin() as s:
        s.get(Product, "SKU-001").reserved = 99999
