# Five-minute demonstration

## Prepare

Start both services and open `http://127.0.0.1:8000`. A fresh seed has:

| Check | Expected value |
| --- | --- |
| Customers / products / orders / invoices | 20 / 30 / 60 / 40 |
| Outstanding issued invoices | €7,099.00 |
| Overdue issued invoices on the seed date | 9 invoices, €1,106.50 |
| Low-stock products | SKU-001, 006, 011, 016, 021, 026; each has 5 available |
| Acme Office outstanding balance | €20.00, INV-2021 |
| Last month's confirmed/fulfilled order sales | €12,780.00 across 40 orders |
| ORD-1042 | Acme Studio, €81.00, eligible before confirmation |

Dates are relative to the day the database is seeded; overdue classifications change as time passes. For a fresh repeatable demo, stop services and deliberately run `uv run python -m app.cli reset --yes`, then restart. This deletes the configured demo data. The live evaluator instead creates its own disposable database automatically.

## 1 — Explore the ERP

Show the four overview cards. Switch among Orders, Invoices, Inventory, and Customers. Search Acme and open a customer record. Select Inventory → Low stock. Open SKU-001 to show on-hand stock, reservations, and available stock.

## 2 — Resolve an ambiguous question

With QwenCloud configured, ask: “Show Acme's recent orders and unpaid invoices.” The assistant should ask which Acme company you mean. Reply “Acme Office, CUS-001.” Show its three order references and €20.00 outstanding balance. Open a source record and expand tool activity.

## 3 — Ask across records

Ask about overdue invoices: expect nine and €1,106.50 on the seed date. Ask for last month's five biggest customers. Ties are resolved by customer ID:

| Customer | Net order value |
| --- | --- |
| CUS-009 · Meridian Labs | €1,517.50 |
| CUS-019 · Urban Collective | €1,517.50 |
| CUS-010 · Juniper Works | €1,113.50 |
| CUS-020 · Willow Education | €1,113.50 |
| CUS-008 · Oak & Ink | €1,044.50 |

Explain that these totals come from the ERP's Python calculations, not the language model adding a displayed page.

## 4 — Prepare and approve an invoice

Ask to prepare ORD-1042, or use Orders → search ORD-1042 → open record → Prepare draft invoice. The preview contains two desk lamps at €14.25 and three chairs at €17.50, totalling €81.00, due 30 days after preparation.

Type “yes, confirm it” in chat. No invoice should be created. Click **Confirm draft invoice** on the preview card. Open the returned invoice link: it is a draft with the same lines and amount. Inventory and outstanding issued-invoice balance are unchanged. Repeating confirmation returns the same invoice.

For cancellation, prepare another eligible order, such as ORD-1043, and choose Cancel. Expired or stale previews require preparing a new one.

## 5 — Explain the boundaries

The eight MCP tools expose specific business operations. QwenCloud cannot run SQL, change stock, send invoices, or approve its own preview. The data is fictional, services listen on localhost, API credentials stay on the server, and audit events record approval outcomes.

The full 12-prompt acceptance set is in `acceptance.json`. The live runner also inserts hostile text into a fictional customer name in its separate temporary database and checks that the assistant treats it as data.

