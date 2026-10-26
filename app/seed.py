from datetime import timedelta

from sqlalchemy import select

from app.config import today
from app.models import Customer, Invoice, Order, OrderLine, Product
from app.services import customer_dict, order_dict

COMPANIES = [
    "Acme Office",
    "Acme Studio",
    "Northstar Design",
    "Atelier Blanc",
    "Forma Architecture",
    "Paper & Co.",
    "Brightside Consulting",
    "Oak & Ink",
    "Meridian Labs",
    "Juniper Works",
    "Foundry Creative",
    "Bluebird Agency",
    "Cedar Finance",
    "Lumen Digital",
    "Pioneer Legal",
    "Riverbank Health",
    "Summit Partners",
    "Terra Engineering",
    "Urban Collective",
    "Willow Education",
]
PRODUCTS = [
    "A4 recycled paper · 500 sheets",
    "A5 dotted notebook",
    "Black gel pens · box of 12",
    "Adjustable desk lamp",
    "Ergonomic office chair",
    "Monitor stand",
    "USB-C docking station",
    "Wireless keyboard",
    "Wireless mouse",
    "27-inch monitor",
    "Desk organiser",
    "Document tray",
    "Sticky notes · pack of 6",
    "Whiteboard markers",
    "Magnetic whiteboard",
    "Ring binders · pack of 5",
    "Archive boxes · pack of 10",
    "Shipping labels",
    "Packing tape · pack of 6",
    "Desktop stapler",
    "Heavy-duty staples",
    "Scissors",
    "Filing cabinet",
    "Standing desk",
    "Desk mat",
    "Cable management kit",
    "Laptop sleeve",
    "Printer toner",
    "Envelopes · box of 100",
    "Meeting planner",
]


def seed(sessions, as_of=None):
    as_of = as_of or today()
    previous_month = (as_of.replace(day=1) - timedelta(days=1)).replace(day=1)
    days_previous = (as_of.replace(day=1) - previous_month).days
    with sessions() as s:
        if s.scalar(select(Customer.id).limit(1)):
            return False
        for i, company in enumerate(COMPANIES):
            s.add(
                Customer(
                    id=f"CUS-{i + 1:03}",
                    name=company,
                    contact=f"{['Alex', 'Sam', 'Morgan', 'Taylor'][i % 4]} Martin",
                    email=f"office{i + 1}@example.test",
                )
            )
        for i, name in enumerate(PRODUCTS):
            s.add(
                Product(
                    id=f"SKU-{i + 1:03}",
                    name=name,
                    price_cents=450 + i * 325,
                    on_hand=8 if i % 5 == 0 else 60 + i * 3,
                    reserved=3 if i % 5 == 0 else 5,
                    reorder_threshold=10,
                )
            )
        s.flush()
        for i in range(60):
            order = Order(
                id=f"ORD-{1001 + i}",
                customer_id=f"CUS-{(i % 20) + 1:03}",
                date=previous_month + timedelta(days=(i * 7) % days_previous)
                if i < 40
                else as_of - timedelta(days=min(as_of.day - 1, i % 7)),
                status="cancelled" if i in (45, 55) else ("fulfilled" if i < 30 else "confirmed"),
            )
            s.add(order)
            s.flush()
            for j in range(2):
                product = s.get(Product, f"SKU-{((i * 3 + j) % 30) + 1:03}")
                s.add(
                    OrderLine(
                        order_id=order.id,
                        product_id=product.id,
                        quantity=1 + (i + j) % 5,
                        unit_price_cents=product.price_cents,
                    )
                )
            s.flush()
            if i < 40:
                snapshot = order_dict(s, order, True)
                s.add(
                    Invoice(
                        id=f"INV-{2001 + i}",
                        order_id=order.id,
                        customer_id=order.customer_id,
                        customer_snapshot=customer_dict(s.get(Customer, order.customer_id)),
                        lines=snapshot["lines"],
                        issue_date=order.date,
                        due_date=as_of + timedelta(days=(i % 5) * 8 - 16),
                        status=("draft" if i % 8 == 0 else "paid" if i % 3 == 0 else "issued"),
                        total_cents=snapshot["total_cents"],
                    )
                )
        s.commit()
    return True
