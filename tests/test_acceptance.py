import json
from datetime import timedelta

from app.config import ROOT
from app.evaluate import has_amount
from app.schemas import Query
from tests.conftest import AS_OF


def test_fixed_acceptance_fixture_values(database):
    """Independently lock the twelve demo scenarios to the seeded ERP's numeric ground truth."""
    erp, _, _ = database
    cases = json.loads((ROOT / "docs" / "acceptance.json").read_text())
    assert len(cases) == 12 and len({case["id"] for case in cases}) == 12
    assert erp.customer_overview("CUS-001")["outstanding_cents"] == 2000
    assert erp.dashboard()["outstanding_cents"] == 709900
    assert erp.browse("invoices", Query(overdue=True))["aggregates"]["outstanding_cents"] == 110650
    end = AS_OF.replace(day=1) - timedelta(days=1)
    report = erp.sales_summary(end.replace(day=1), end)
    assert report["total_cents"] == 1278000
    assert [(c["customer_id"], c["total_cents"]) for c in report["customers"]] == [
        ("CUS-009", 151750),
        ("CUS-019", 151750),
        ("CUS-010", 111350),
        ("CUS-020", 111350),
        ("CUS-008", 104450),
    ]
    assert erp.detail("orders", "ORD-1042")["total_cents"] == 8100


def test_amount_matching():
    assert has_amount("EUR 1,106.50", 1106.5)
    assert has_amount("EUR 12,780", 12780)
    assert has_amount("81.00 euros", 81)
    assert not has_amount("810.00 euros", 81)
    assert not has_amount("1106.51 euros", 1106.5)
