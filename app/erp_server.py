import hmac
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, ValidationError
from sqlalchemy import text

from app.config import Settings
from app.db import open_database
from app.schemas import InvoiceStatus, OrderStatus, Query, Resource
from app.services import ERP, DomainError

SESSION_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def browser_session(headers):
    value = headers.get("x-session-id", "")
    if not SESSION_PATTERN.fullmatch(value):
        raise DomainError("A valid browser session is required.", 403, "session_required")
    return value


class InternalAuth:
    """Protect both REST and the mounted MCP app, including its discovery endpoint."""

    def __init__(self, app, token):
        self.app, self.expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            actual = dict(scope.get("headers", [])).get(b"authorization", b"")
            if not hmac.compare_digest(actual, self.expected):
                response = JSONResponse(
                    {"error": {"code": "unauthorized", "message": "Internal authentication required."}}, 401
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings=None):
    settings = settings or Settings()
    settings.validate_token()
    engine, sessions = open_database(settings.database_url)
    erp = ERP(sessions)
    mcp = FastMCP(
        "smartERP ERP",
        json_response=True,
        stateless_http=True,
        instructions="Fictional ERP data. Prepare invoice previews only; confirmation is outside MCP.",
    )

    def run(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except DomainError as exc:
            raise ValueError(f"{exc.code}: {exc.message}") from None

    Limit = Annotated[int, Field(ge=1, le=100)]
    Offset = Annotated[int, Field(ge=0, le=100000)]

    @mcp.tool(structured_output=True)
    def search_customers(query: str = "", limit: Limit = 20, offset: Offset = 0) -> dict[str, Any]:
        """Find customers by literal company name or email. Return IDs, names, contacts, and pagination.
        If multiple customers match a name, ask the employee to choose before customer-specific queries or actions.
        An empty query lists all customers. Never infer a customer ID from its name.
        """
        return run(erp.browse, "customers", Query(q=query, limit=limit, offset=offset))

    @mcp.tool(structured_output=True)
    def get_customer_overview(customer_id: str) -> dict[str, Any]:
        """Get a resolved customer's details, five recent orders, and unpaid issued invoices.
        Outstanding balance is computed over all issued invoices, excluding paid and draft invoices.
        Use search_customers first if the employee supplied a name rather than a stable ID.
        """
        return run(erp.customer_overview, customer_id)

    @mcp.tool(structured_output=True)
    def list_orders(
        customer_id: str | None = None,
        status: OrderStatus | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        query: str = "",
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """List orders by customer ID, status, inclusive order-date range, or literal order/name search.
        Return net EUR totals in integer cents, invoice IDs, eligibility, and pagination.
        Results are newest first. For totals and rankings across orders use get_sales_summary.
        """
        return run(
            erp.browse,
            "orders",
            Query(
                customer_id=customer_id,
                status=status,
                date_from=date_from,
                date_to=date_to,
                q=query,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool(structured_output=True)
    def get_order(order_id: str) -> dict[str, Any]:
        """Get one order by its exact ID including customer, lines, net totals, and invoice eligibility.
        Prices are order snapshots; quantities and totals are computed by the ERP.
        Cancelled or already invoiced orders are ineligible for draft invoice preparation.
        """
        return run(erp.detail, "orders", order_id)

    @mcp.tool(structured_output=True)
    def list_stock(
        query: str = "", low_stock: bool = False, limit: Limit = 20, offset: Offset = 0
    ) -> dict[str, Any]:
        """Search inventory by SKU or product name, optionally selecting low available stock.
        Available equals on_hand minus reserved; low means available at or below the reorder threshold.
        This tool is read-only and cannot reserve, adjust, or replenish inventory.
        """
        return run(erp.browse, "stock", Query(q=query, low_stock=low_stock, limit=limit, offset=offset))

    @mcp.tool(structured_output=True)
    def list_invoices(
        customer_id: str | None = None,
        status: InvoiceStatus | None = None,
        overdue: bool = False,
        due_before: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        query: str = "",
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> dict[str, Any]:
        """List invoices by customer, status, inclusive issue-date range, or inclusive due-before date.
        Overdue selects issued invoices due before today's Europe/Paris business date.
        Aggregates cover ALL matching invoices, independent of pagination; outstanding excludes paid and draft.
        """
        return run(
            erp.browse,
            "invoices",
            Query(
                customer_id=customer_id,
                status=status,
                overdue=overdue,
                due_before=due_before,
                date_from=date_from,
                date_to=date_to,
                q=query,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool(structured_output=True)
    def get_sales_summary(
        date_from: date, date_to: date, limit: Annotated[int, Field(ge=1, le=20)] = 5
    ) -> dict[str, Any]:
        """Compute sales totals and top customers over an inclusive order-date range.
        Sales means confirmed and fulfilled orders, excluding cancelled orders; values are net EUR cents.
        Return customer and order IDs as sources. Use this for rankings rather than calculating from a page.
        """
        return run(erp.sales_summary, date_from, date_to, limit)

    @mcp.tool(structured_output=True)
    def prepare_invoice(order_id: str, ctx: Context) -> dict[str, Any]:
        """Prepare a 15-minute draft-invoice preview for an exact eligible order ID.
        This creates no invoice and does not change inventory. The preview is bound to the current browser session.
        The employee must click Confirm in the interface; chat approval cannot execute an invoice.
        """
        request = ctx.request_context.request
        return run(erp.prepare, order_id, browser_session(request.headers if request else {}))

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        engine.dispose()

    app = FastAPI(title="smartERP ERP", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(InternalAuth, token=settings.internal_token)
    app.state.erp, app.state.engine, app.state.mcp = erp, engine, mcp

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status)

    @app.get("/health")
    def health():
        try:
            with engine.connect() as connection:
                version = connection.scalar(text("SELECT version_num FROM alembic_version"))
                connection.execute(text("SELECT id FROM customers LIMIT 1"))
            return {"status": "ready" if version == "0001" else "unavailable"}
        except Exception:
            return JSONResponse(
                {"status": "unavailable", "message": "Initialize the database before starting."}, 503
            )

    @app.get("/dashboard")
    def dashboard():
        return erp.dashboard()

    @app.get("/records/{resource}")
    def browse(resource: Resource, request: Request):
        try:
            query = Query.model_validate(dict(request.query_params))
        except ValidationError:
            raise DomainError("Invalid query parameters or date range.", 422, "invalid_query") from None
        return erp.browse(resource, query)

    @app.get("/records/{resource}/{key}")
    def detail(resource: Resource, key: str):
        return erp.detail(resource, key)

    @app.get("/actions")
    def actions(request: Request):
        return erp.actions(browser_session(request.headers))

    @app.post("/actions/{action_id}/{operation}")
    def resolve(action_id: str, operation: str, request: Request):
        return erp.resolve(action_id, browser_session(request.headers), operation)

    app.mount("/", mcp.streamable_http_app())
    return app
