import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, text

from app.config import today
from app.models import AuditEvent, Customer, Invoice, Order, OrderLine, PendingAction, Product
from app.schemas import Query


class DomainError(Exception):
    def __init__(self, message: str, status: int = 409, code: str = "conflict"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def required(session, model, key):
    row = session.get(model, key)
    if row is None:
        raise DomainError(f"Record {key} was not found.", 404, "not_found")
    return row


def customer_dict(c):
    return {"id": c.id, "name": c.name, "contact": c.contact, "email": c.email}


def stock_dict(p):
    available = p.on_hand - p.reserved
    return {
        "id": p.id,
        "name": p.name,
        "price_cents": p.price_cents,
        "on_hand": p.on_hand,
        "reserved": p.reserved,
        "available": available,
        "reorder_threshold": p.reorder_threshold,
        "low_stock": available <= p.reorder_threshold,
    }


def order_dict(s, o, detail=False):
    lines = []
    for line, product in s.execute(
        select(OrderLine, Product).join(Product).where(OrderLine.order_id == o.id).order_by(OrderLine.id)
    ):
        lines.append(
            {
                "product_id": product.id,
                "name": product.name,
                "quantity": line.quantity,
                "unit_price_cents": line.unit_price_cents,
                "total_cents": line.quantity * line.unit_price_cents,
            }
        )
    customer = required(s, Customer, o.customer_id)
    invoice = s.scalar(select(Invoice).where(Invoice.order_id == o.id))
    result = {
        "id": o.id,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "date": o.date.isoformat(),
        "status": o.status,
        "total_cents": sum(x["total_cents"] for x in lines),
        "invoice_id": invoice.id if invoice else None,
        "invoice_eligible": o.status in ("confirmed", "fulfilled") and invoice is None,
    }
    if detail:
        result.update(customer=customer_dict(customer), lines=lines)
    return result


def invoice_dict(i, detail=False):
    result = {
        "id": i.id,
        "order_id": i.order_id,
        "customer_id": i.customer_id,
        "customer_name": i.customer_snapshot["name"],
        "issue_date": i.issue_date.isoformat(),
        "due_date": i.due_date.isoformat(),
        "status": i.status,
        "total_cents": i.total_cents,
        "overdue": i.status == "issued" and i.due_date < today(),
    }
    if detail:
        result.update(customer=i.customer_snapshot, lines=i.lines)
    return result


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def action_dict(a):
    expires = a.expires_at.replace(tzinfo=UTC)
    state = "expired" if a.state == "pending" and expires <= datetime.now(UTC) else a.state
    return {
        "id": a.id,
        "state": state,
        "expires_at": expires.isoformat(),
        "invoice_id": a.invoice_id,
        "preview": a.preview,
    }


class ERP:
    def __init__(self, sessions):
        self.sessions = sessions

    def browse(self, resource: str, query: Query):
        models = {"customers": Customer, "stock": Product, "orders": Order, "invoices": Invoice}
        if resource not in models:
            raise DomainError("Unknown ERP resource.", 404, "not_found")
        allowed = {
            "customers": set(),
            "stock": {"low_stock"},
            "orders": {"customer_id", "status", "date_from", "date_to"},
            "invoices": {"customer_id", "status", "date_from", "date_to", "due_before", "overdue"},
        }
        active = query.model_dump(exclude_defaults=True, exclude_none=True)
        if set(active) - allowed[resource] - {"q", "limit", "offset"}:
            raise DomainError("This filter does not apply to the selected resource.", 422, "invalid_filter")
        statuses = {
            "orders": {"confirmed", "fulfilled", "cancelled"},
            "invoices": {"draft", "issued", "paid"},
        }
        if query.status and query.status not in statuses.get(resource, set()):
            raise DomainError("Invalid status filter.", 422, "invalid_filter")
        with self.sessions() as s:
            rows = s.scalars(select(models[resource]).order_by(models[resource].id)).all()
            serialize = {
                "customers": customer_dict,
                "stock": stock_dict,
                "orders": lambda row: order_dict(s, row),
                "invoices": invoice_dict,
            }[resource]
            items = [serialize(row) for row in rows]

            def matches(item):
                searchable = " ".join(str(item.get(k, "")) for k in ("id", "name", "email", "customer_name"))
                dt = item.get("date", item.get("issue_date", ""))
                return (
                    query.q.casefold() in searchable.casefold()
                    and (not query.customer_id or item.get("customer_id") == query.customer_id)
                    and (not query.status or item.get("status") == query.status)
                    and (not query.date_from or dt >= query.date_from.isoformat())
                    and (not query.date_to or dt <= query.date_to.isoformat())
                    and (not query.due_before or item.get("due_date", "") <= query.due_before.isoformat())
                    and (not query.overdue or item.get("overdue"))
                    and (not query.low_stock or item.get("low_stock"))
                )

            items = [item for item in items if matches(item)]
            if resource in ("orders", "invoices"):
                items.sort(key=lambda row: (row.get("date", row.get("issue_date")), row["id"]), reverse=True)
            totals = {}
            if resource == "invoices":
                totals = {
                    "total_cents": sum(x["total_cents"] for x in items),
                    "outstanding_cents": sum(x["total_cents"] for x in items if x["status"] == "issued"),
                }
            return {
                "items": items[query.offset : query.offset + query.limit],
                "total": len(items),
                "limit": query.limit,
                "offset": query.offset,
                "filters": query.model_dump(mode="json", exclude={"limit", "offset"}),
                "aggregates": totals,
                "as_of": today().isoformat(),
                "currency": "EUR",
            }

    def detail(self, resource, key):
        with self.sessions() as s:
            if resource == "customers":
                return customer_dict(required(s, Customer, key))
            if resource == "stock":
                return stock_dict(required(s, Product, key))
            if resource == "orders":
                return order_dict(s, required(s, Order, key), detail=True)
            if resource == "invoices":
                return invoice_dict(required(s, Invoice, key), detail=True)
            raise DomainError("Unknown ERP resource.", 404, "not_found")

    def customer_overview(self, customer_id):
        customer = self.detail("customers", customer_id)
        orders = self.browse("orders", Query(customer_id=customer_id, limit=5))
        invoices = self.browse("invoices", Query(customer_id=customer_id, status="issued", limit=100))
        return {
            "customer": customer,
            "recent_orders": orders,
            "unpaid_invoices": invoices,
            "outstanding_cents": invoices["aggregates"]["outstanding_cents"],
            "currency": "EUR",
        }

    def sales_summary(self, date_from: date, date_to: date, limit=5):
        if date_from > date_to or not 1 <= limit <= 20:
            raise DomainError("Use an ordered date range and a limit from 1 to 20.", 422, "invalid_filter")
        with self.sessions() as s:
            orders = s.scalars(
                select(Order).where(
                    Order.date >= date_from, Order.date <= date_to, Order.status != "cancelled"
                )
            ).all()
            groups = defaultdict(lambda: {"total_cents": 0, "order_ids": []})
            for order in orders:
                item = order_dict(s, order)
                groups[order.customer_id]["total_cents"] += item["total_cents"]
                groups[order.customer_id]["order_ids"].append(order.id)
            ranked = sorted(groups.items(), key=lambda entry: (-entry[1]["total_cents"], entry[0]))
            return {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "total_cents": sum(g["total_cents"] for g in groups.values()),
                "order_count": len(orders),
                "customers": [
                    {"customer_id": key, "customer_name": required(s, Customer, key).name, **value}
                    for key, value in ranked[:limit]
                ],
                "currency": "EUR",
                "definition": "Confirmed and fulfilled orders by order date; cancelled orders excluded.",
            }

    def _snapshot(self, s, order_id):
        result = order_dict(s, required(s, Order, order_id), detail=True)
        if not result["invoice_eligible"]:
            raise DomainError("This order is cancelled or already has an invoice.", code="ineligible_order")
        if not result["lines"]:
            raise DomainError("An empty order cannot be invoiced.", code="empty_order")
        return result

    def prepare(self, order_id, session_id):
        if not session_id:
            raise DomainError("A browser session is required.", 403, "session_required")
        with self.sessions() as s:
            s.execute(text("BEGIN IMMEDIATE"))
            snapshot = self._snapshot(s, order_id)
            preview = {
                "order_id": order_id,
                "customer": snapshot["customer"],
                "lines": snapshot["lines"],
                "total_cents": snapshot["total_cents"],
                "currency": "EUR",
                "issue_date": today().isoformat(),
                "due_date": (today() + timedelta(days=30)).isoformat(),
            }
            action = PendingAction(
                id=uuid4().hex,
                session_id=session_id,
                order_id=order_id,
                preview=preview,
                fingerprint=fingerprint(snapshot),
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                state="pending",
            )
            s.add(action)
            s.commit()
            return action_dict(action)

    def actions(self, session_id):
        with self.sessions() as s:
            actions = s.scalars(
                select(PendingAction)
                .where(PendingAction.session_id == session_id)
                .order_by(PendingAction.expires_at.desc())
                .limit(20)
            ).all()
            return {"items": [action_dict(a) for a in actions]}

    def resolve(self, action_id, session_id, operation):
        if operation not in ("confirm", "cancel"):
            raise DomainError("Unknown operation.", 404, "not_found")
        with self.sessions() as s:
            # Serialize writers before the initial read. This also handles duplicate requests across processes.
            s.execute(text("BEGIN IMMEDIATE"))
            action = required(s, PendingAction, action_id)
            if action.session_id != session_id:
                raise DomainError("Action not found for this session.", 404, "not_found")
            if action.state == "confirmed":
                if operation == "cancel":
                    raise DomainError("This action is already confirmed.")
                return {
                    "action": action_dict(action),
                    "invoice": invoice_dict(required(s, Invoice, action.invoice_id), True),
                }
            if action.state == "cancelled" and operation == "cancel":
                return {"action": action_dict(action)}
            if action.state != "pending":
                raise DomainError(
                    f"This preview is {action.state}. Prepare a new preview.", code=action.state
                )
            if action.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
                self._reject(s, action, "expired", "This preview expired. Prepare a new preview.")
            if operation == "cancel":
                action.state = "cancelled"
                s.add(AuditEvent(action_id=action.id, timestamp=datetime.now(UTC), outcome="cancelled"))
                s.commit()
                return {"action": action_dict(action)}
            try:
                snapshot = self._snapshot(s, action.order_id)
            except DomainError:
                self._reject(s, action, "stale", "The order changed or was invoiced. Prepare a new preview.")
            if (
                fingerprint(snapshot) != action.fingerprint
                or action.preview["issue_date"] != today().isoformat()
            ):
                self._reject(
                    s, action, "stale", "The source data or business date changed. Prepare a new preview."
                )
            preview = action.preview
            invoice = Invoice(
                id="INV-" + uuid4().hex[:12].upper(),
                order_id=action.order_id,
                customer_id=preview["customer"]["id"],
                customer_snapshot=preview["customer"],
                lines=preview["lines"],
                issue_date=date.fromisoformat(preview["issue_date"]),
                due_date=date.fromisoformat(preview["due_date"]),
                status="draft",
                total_cents=preview["total_cents"],
            )
            s.add(invoice)
            s.flush()
            action.state, action.invoice_id = "confirmed", invoice.id
            s.add(
                AuditEvent(
                    action_id=action.id,
                    timestamp=datetime.now(UTC),
                    outcome="confirmed",
                    invoice_id=invoice.id,
                )
            )
            s.commit()
            return {"action": action_dict(action), "invoice": invoice_dict(invoice, True)}

    @staticmethod
    def _reject(s, action, state, message):
        action.state = state
        s.add(AuditEvent(action_id=action.id, timestamp=datetime.now(UTC), outcome=state))
        s.commit()
        raise DomainError(message, code=state)

    def dashboard(self):
        as_of = today()
        month = as_of.replace(day=1)
        months = [month]
        for _ in range(5):
            month = (month - timedelta(days=1)).replace(day=1)
            months.insert(0, month)
        with self.sessions() as s:
            invoices = s.scalars(select(Invoice)).all()
            products = [stock_dict(p) for p in s.scalars(select(Product))]
            sales = {month.isoformat()[:7]: 0 for month in months}
            customers = defaultdict(int)
            for order, total in s.execute(
                select(Order, func.sum(OrderLine.quantity * OrderLine.unit_price_cents))
                .join(OrderLine)
                .where(Order.status != "cancelled", Order.date >= months[0], Order.date <= as_of)
                .group_by(Order.id)
            ):
                sales[order.date.isoformat()[:7]] += total
                customers[order.customer_id] += total
            invoice_balances = dict.fromkeys(("paid", "issued", "overdue", "draft"), 0)
            for invoice in invoices:
                status = (
                    "overdue" if invoice.status == "issued" and invoice.due_date < as_of else invoice.status
                )
                invoice_balances[status] += invoice.total_cents
            return {
                "customers": len(s.scalars(select(Customer)).all()),
                "orders": len(s.scalars(select(Order)).all()),
                "stock_alerts": sum(p["low_stock"] for p in products),
                "outstanding_cents": sum(i.total_cents for i in invoices if i.status == "issued"),
                "overdue_cents": sum(
                    i.total_cents for i in invoices if i.status == "issued" and i.due_date < today()
                ),
                "currency": "EUR",
                "as_of": as_of.isoformat(),
                "charts": {
                    "sales": [{"month": month, "total_cents": total} for month, total in sales.items()],
                    "invoice_balances": invoice_balances,
                    "top_customers": [
                        {"id": key, "name": required(s, Customer, key).name, "total_cents": total}
                        for key, total in sorted(customers.items(), key=lambda item: (-item[1], item[0]))[:5]
                    ],
                    "stock": sorted(
                        products, key=lambda p: (p["available"] - p["reorder_threshold"], p["id"])
                    )[:6],
                },
            }
