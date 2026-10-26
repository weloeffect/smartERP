import httpx

from app.gateway import Gateway
from tests.conftest import SESSION_A, SESSION_B

EXPECTED_TOOLS = {
    "search_customers",
    "get_customer_overview",
    "list_orders",
    "get_order",
    "list_stock",
    "list_invoices",
    "get_sales_summary",
    "prepare_invoice",
}


async def test_real_mcp_discovery_queries_and_session(live_stack):
    gateway = Gateway(live_stack)
    catalog = await gateway.catalog()
    assert {tool["name"] for tool in catalog} == EXPECTED_TOOLS
    schema = next(t["input_schema"] for t in catalog if t["name"] == "prepare_invoice")
    assert set(schema["properties"]) == {"order_id"}
    async with gateway.connect(SESSION_A) as client:
        found = await client.call_tool("search_customers", {"query": "Acme"})
        assert found.structuredContent["total"] == 2
        assert (
            await client.call_tool("get_customer_overview", {"customer_id": "CUS-001"})
        ).structuredContent["customer"]["id"] == "CUS-001"
        assert (await client.call_tool("list_orders", {"customer_id": "CUS-002"})).structuredContent[
            "total"
        ] == 3
        assert (await client.call_tool("list_stock", {"low_stock": True})).structuredContent["total"] == 6
        assert (await client.call_tool("list_invoices", {"overdue": True})).structuredContent["aggregates"][
            "outstanding_cents"
        ] > 0
        assert (
            await client.call_tool("get_sales_summary", {"date_from": "2026-01-01", "date_to": "2026-12-31"})
        ).structuredContent["order_count"] == 58
        assert (await client.call_tool("get_order", {"order_id": "ORD-9999"})).isError
        assert (await client.call_tool("list_stock", {"limit": 101})).isError
        assert (await client.call_tool("confirm_invoice", {})).isError
        prepared = await client.call_tool("prepare_invoice", {"order_id": "ORD-1042"})
        assert not prepared.isError
        action = prepared.structuredContent
    code, other = await gateway.request("GET", "/actions", SESSION_B)
    assert code == 200 and other["items"] == []
    code, own = await gateway.request("GET", "/actions", SESSION_A)
    assert own["items"][0]["id"] == action["id"]
    async with gateway.connect() as client:
        assert (await client.call_tool("prepare_invoice", {"order_id": "ORD-1043"})).isError


async def test_browser_api_security_and_approval(live_stack):
    origin = {"Origin": live_stack.web_origin}
    async with httpx.AsyncClient(base_url=live_stack.web_origin, trust_env=False) as browser:
        credentials = {"username": "tester", "password": "test passphrase for browser"}
        assert (await browser.get("/")).status_code == 303
        assert (await browser.post("/api/auth/setup", json=credentials, headers=origin)).status_code == 200
        page = await browser.get("/")
        assert page.status_code == 200 and "smartERP_session" in browser.cookies
        assert "HttpOnly" in page.headers["set-cookie"] and "SameSite=strict" in page.headers["set-cookie"]
        assert "script-src 'self'" in page.headers["content-security-policy"]
        assert (await browser.get("/health")).json()["mcp_tools"] == 8
        assert (await browser.get("/api/dashboard")).json()["customers"] == 20
        assert (await browser.get("/api/erp/orders?limit=101")).status_code == 422
        assert (await browser.get("/api/erp/orders?unknown=true")).status_code == 422
        assert (await browser.get("/api/erp/stock/SKU-001")).json()["available"] == 5
        assert (await browser.post("/api/actions/prepare", json={"order_id": "ORD-1042"})).status_code == 403
        assert (
            await browser.post(
                "/api/chat", json={"message": "yes"}, headers={"Origin": "https://attacker.invalid"}
            )
        ).status_code == 403
        chat = await browser.post("/api/chat", json={"message": "yes"}, headers=origin)
        assert chat.json()["error"] == "llm_not_configured"
        status = (await browser.get("/health")).json()
        assert status["llm_provider"] == "QwenCloud"
        assert status["llm_configured"] is False
        assert "claude_configured" not in status
        assert (await browser.post("/api/chat", json={"message": " "}, headers=origin)).status_code == 422
        action = (
            await browser.post("/api/actions/prepare", json={"order_id": "ORD-1042"}, headers=origin)
        ).json()
        assert action["state"] == "pending"
        # The model cannot select another browser session in the request body.
        assert (
            await browser.post(
                "/api/actions/prepare", json={"order_id": "ORD-1043", "session_id": SESSION_B}, headers=origin
            )
        ).status_code == 422
        async with httpx.AsyncClient(base_url=live_stack.web_origin, trust_env=False) as other:
            assert (await other.post("/api/auth/login", json=credentials, headers=origin)).status_code == 200
            stolen = await other.post(f"/api/actions/{action['id']}/confirm", headers=origin)
            assert stolen.status_code == 404
        before = (await browser.get("/api/erp/stock?limit=100")).json()
        first = (await browser.post(f"/api/actions/{action['id']}/confirm", headers=origin)).json()
        second = (await browser.post(f"/api/actions/{action['id']}/confirm", headers=origin)).json()
        assert first == second and first["invoice"]["status"] == "draft"
        assert (await browser.get("/api/erp/stock?limit=100")).json() == before
        cancelled = (
            await browser.post("/api/actions/prepare", json={"order_id": "ORD-1043"}, headers=origin)
        ).json()
        assert (await browser.post(f"/api/actions/{cancelled['id']}/cancel", headers=origin)).json()[
            "action"
        ]["state"] == "cancelled"
        assert (
            await browser.post(f"/api/actions/{cancelled['id']}/confirm", headers=origin)
        ).status_code == 409
        js = await browser.get("/static/app.js")
        assert live_stack.internal_token not in js.text
    async with httpx.AsyncClient(base_url=live_stack.erp_url, trust_env=False) as client:
        assert (await client.get("/records/customers")).status_code == 401
        assert (await client.post("/mcp", json={})).status_code == 401
