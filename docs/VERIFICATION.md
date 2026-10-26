# Verification record

Authentication update verified locally on 26 October 2026: **84 Python tests and eight Node answer-renderer tests passed**, together with Ruff lint, formatting, and JavaScript syntax checks. New checks cover password salting, local first-run setup, competing setup requests, persistence, protected reads and writes, incorrect credentials, origin checks, rate limiting, session rotation and expiry, logout revocation, password-reset revocation, and Secure cookies for HTTPS. Authentication tests use separate temporary account stores.

Verified locally on Windows with Python 3.12 on 24 October 2026.

- **63 automated tests passed.** The tests use fresh Alembic-migrated SQLite databases; network tests start actual ERP and web processes on temporary localhost ports and communicate through the official MCP Streamable HTTP client.
- Business checks cover seed integrity, balances, overdue calculations, stock availability, date filters, sales rankings, missing records, literal searches, and database constraints.
- Approval checks cover exact snapshots, unchanged stock, cross-session rejection, cancellation, expiry, changed source data, duplicate confirmation, four concurrent confirmations of one action, and competing previews for one order.
- Agent checks cover multi-round tool/result exchange, allowed tool dispatch, execution-tool rejection, preview propagation, MCP error responses, timeouts, the eight-round limit, and source extraction. Real HTTPX requests go through a local mock transport to check QwenCloud authentication, function schemas, tool-call/result IDs, nullable fields, thinking replay, invalid JSON, truncated tool requests, malformed response envelopes, cloud error sanitization, and bounded retries. Configuration tests cover DashScope aliases, region/plan endpoint overrides, and secret-free settings representations.
- Ruff lint and Python formatting checks passed. JavaScript syntax was checked with Node.
- Browser verification covered the rendered workspace, record search, order details, invoice preparation, explicit confirmation, and the resulting draft invoice record. Screenshots are saved in `docs/screenshots/`. The active local demo includes one confirmed test draft for ORD-1042; ORD-1043 remains available to demonstrate preparation, or deliberately reset the demo to restore the initial seed.

**Live QwenCloud acceptance: skipped.** No `DASHSCOPE_API_KEY` was configured locally during migration. Mocked HTTP tests verify the integration contract, not the model's live answers or instruction-following quality. Run `uv run python -m app.cli evaluate` after adding a key to execute the twelve live scenarios in a disposable ERP. The regional Base URL must match the key's QwenCloud region and access plan.

GitHub Actions is supplied for Windows and Ubuntu, but remote CI was not executed during this local implementation. Mobile CSS is included; browser verification used the Codex browser's available viewport rather than a device emulation suite.

