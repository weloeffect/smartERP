import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.agent import Agent
from app.auth import AuthStore, LoginInput, SetupInput
from app.config import ROOT, Settings
from app.gateway import ERPUnavailable, Gateway
from app.schemas import ChatInput, PrepareInput, Resource


@dataclass
class Conversation:
    username: str = ""
    account_version: str = ""
    history: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    accessed: float = field(default_factory=time.monotonic)


def create_app(settings=None, gateway=None, agent=None):
    settings = settings or Settings()
    settings.validate_token()
    auth = AuthStore(settings.auth_database_path)
    gateway, agent = gateway or Gateway(settings), agent or Agent(settings)
    conversations = {}
    catalog = []
    login_attempts = []
    auth_lock = asyncio.Lock()

    async def discover():
        nonlocal catalog
        if not catalog:
            catalog = await gateway.catalog()
        return catalog

    @asynccontextmanager
    async def lifespan(app):
        try:
            await discover()
        except Exception:
            pass  # The interface explains ERP unavailability and can reconnect without restarting.
        yield
        if agent.client and hasattr(agent.client, "close"):
            await agent.client.close()
        conversations.clear()

    app = FastAPI(
        title="smartERP Assistant", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.conversations, app.state.gateway = conversations, gateway

    def secure_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    def set_session_cookie(response, session_id):
        response.set_cookie(
            "smartERP_session",
            session_id,
            httponly=True,
            samesite="strict",
            max_age=3600,
            secure=settings.web_origin.startswith("https://"),
        )

    @app.middleware("http")
    async def session_and_origin(request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get("origin") != settings.web_origin:
                return secure_response(
                    JSONResponse(
                        {
                            "error": {
                                "code": "origin_rejected",
                                "message": "Use the configured local app address.",
                            }
                        },
                        403,
                    )
                )
        current = time.monotonic()
        expired = [
            key
            for key, value in conversations.items()
            if current - value.accessed > 3600 and not value.lock.locked()
        ]
        for key in expired:
            conversations.pop(key, None)
        session_id = request.cookies.get("smartERP_session")
        conversation = conversations.get(session_id)
        if conversation:
            account = auth.account()
            if not account or conversation.account_version != account["version"]:
                conversations.pop(session_id, None)
                conversation = None
        path = request.url.path
        public = path in {"/login", "/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup"}
        public = public or path in {
            "/static/style.css",
            "/static/theme.css",
            "/static/auth.css",
            "/static/auth.js",
            "/static/app.js",
            "/static/answer-format.js",
            "/static/favicon.svg",
        }
        if not public and not conversation:
            if path.startswith("/api/"):
                response = JSONResponse(
                    {"error": {"code": "login_required", "message": "Please sign in to continue."}}, 401
                )
            else:
                response = RedirectResponse("/login", status_code=303)
            response.delete_cookie("smartERP_session")
            return secure_response(response)
        if conversation:
            conversation.accessed = current
        request.state.session_id, request.state.conversation = session_id, conversation
        response = await call_next(request)
        if (
            conversation
            and session_id in conversations
            and path not in {"/api/auth/login", "/api/auth/setup"}
        ):
            set_session_cookie(response, session_id)
        return secure_response(response)

    def signed_in(request, account):
        if len(conversations) >= 200:
            return JSONResponse(
                {"error": {"code": "busy", "message": "Too many active sessions. Try again later."}}, 503
            )
        # Never promote a caller-supplied or anonymous cookie to an authenticated session.
        conversations.pop(request.cookies.get("smartERP_session"), None)
        session_id = secrets.token_hex(32)
        conversations[session_id] = Conversation(
            username=account["username"], account_version=account["version"]
        )
        login_attempts.clear()
        response = JSONResponse({"username": account["username"]})
        set_session_cookie(response, session_id)
        return response

    def throttle_login():
        current = time.monotonic()
        login_attempts[:] = [attempt for attempt in login_attempts if current - attempt < 300]
        if len(login_attempts) >= 10:
            return JSONResponse(
                {
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many sign-in attempts. Try again in 5 minutes.",
                    }
                },
                429,
                headers={"Retry-After": "300"},
            )
        login_attempts.append(current)
        return None

    @app.get("/login")
    def login_page(request: Request):
        if request.state.conversation:
            return RedirectResponse("/", status_code=303)
        return FileResponse(ROOT / "static" / "login.html")

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        conversation = request.state.conversation
        return {
            "setup_required": auth.account() is None,
            "authenticated": bool(conversation),
            "username": conversation.username if conversation else None,
        }

    @app.post("/api/auth/setup")
    async def setup_account(body: SetupInput, request: Request):
        if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse(
                {"error": {"code": "local_setup_only", "message": "Set up the account on this computer."}},
                403,
            )
        if auth.account():
            return JSONResponse(
                {
                    "error": {
                        "code": "already_configured",
                        "message": "An account already exists. Please sign in.",
                    }
                },
                409,
            )
        if limited := throttle_login():
            return limited
        async with auth_lock:
            account = await asyncio.to_thread(auth.create, body.username, body.password)
        if not account:
            return JSONResponse(
                {
                    "error": {
                        "code": "already_configured",
                        "message": "An account already exists. Please sign in.",
                    }
                },
                409,
            )
        return signed_in(request, account)

    @app.post("/api/auth/login")
    async def login(body: LoginInput, request: Request):
        if limited := throttle_login():
            return limited
        async with auth_lock:
            account = await asyncio.to_thread(auth.verify, body.username, body.password)
        if not account:
            return JSONResponse(
                {"error": {"code": "invalid_credentials", "message": "Incorrect username or password."}}, 401
            )
        return signed_in(request, account)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        conversations.pop(request.state.session_id, None)
        response = JSONResponse({"signed_out": True})
        response.delete_cookie("smartERP_session")
        return response

    @app.exception_handler(ERPUnavailable)
    async def unavailable(request, exc):
        return JSONResponse({"error": {"code": "erp_unavailable", "message": str(exc)}}, 503)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        if request.url.path.startswith("/api/auth/"):
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_input",
                        "message": "Use a username of 3–40 letters, numbers, dots, underscores or hyphens, and a password of 8–128 characters when creating an account.",
                    }
                },
                422,
            )
        return await request_validation_exception_handler(request, exc)

    @app.get("/")
    def home():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/health")
    async def health():
        try:
            code, body = await gateway.request("GET", "/health")
            await discover()
            ready = code == 200 and body.get("status") == "ready"
            return JSONResponse(
                {
                    "status": "ready" if ready else "unavailable",
                    "mcp_tools": len(catalog),
                    "llm_configured": bool(agent.client),
                    "llm_provider": "QwenCloud",
                    "llm_model": settings.qwen_model,
                },
                200 if ready else 503,
            )
        except Exception:
            return JSONResponse(
                {
                    "status": "unavailable",
                    "mcp_tools": 0,
                    "llm_configured": bool(agent.client),
                    "llm_provider": "QwenCloud",
                    "llm_model": settings.qwen_model,
                },
                503,
            )

    @app.get("/api/session")
    async def session(request: Request):
        _, actions = await gateway.request("GET", "/actions", request.state.session_id)
        return {
            "username": request.state.conversation.username,
            "llm_configured": bool(agent.client),
            "llm_provider": "QwenCloud",
            "llm_model": settings.qwen_model,
            "actions": actions.get("items", []),
            "conversation_ephemeral": True,
        }

    @app.get("/api/dashboard")
    async def dashboard():
        code, body = await gateway.request("GET", "/dashboard")
        return JSONResponse(body, code)

    @app.get("/api/erp/{resource}")
    async def browse(resource: Resource, request: Request):
        code, body = await gateway.request("GET", f"/records/{resource}", params=dict(request.query_params))
        return JSONResponse(body, code)

    @app.get("/api/erp/{resource}/{key}")
    async def detail(resource: Resource, key: str):
        from urllib.parse import quote

        code, body = await gateway.request("GET", f"/records/{resource}/{quote(key, safe='')}")
        return JSONResponse(body, code)

    @app.post("/api/chat")
    async def chat(body: ChatInput, request: Request):
        conversation = request.state.conversation
        if not body.message.strip():
            return JSONResponse(
                {"error": {"code": "empty_message", "message": "Enter a question first."}}, 422
            )
        if conversation.lock.locked():
            return JSONResponse({"error": {"code": "busy", "message": "Wait for the previous answer."}}, 409)
        async with conversation.lock:
            if not agent.client:
                return await agent.turn(body.message, conversation.history, None, [])
            reset = len(json.dumps(conversation.history)) > 120000
            if reset:
                conversation.history.clear()
            try:
                tools = await discover()
                async with asyncio.timeout(settings.chat_timeout_seconds):
                    async with gateway.connect(request.state.session_id) as mcp:
                        result = await agent.turn(body.message, conversation.history, mcp, tools)
                result["history_reset"] = reset
                return result
            except TimeoutError:
                return {
                    "answer": "This question reached its time limit. Try a narrower question and check pending invoice previews before preparing again.",
                    "error": "chat_timeout",
                    "references": [],
                    "tool_activity": [],
                    "pending_actions": [],
                }
            except Exception:
                return {
                    "answer": "The ERP connection failed or timed out. Please retry. Check pending actions before preparing again.",
                    "error": "erp_unavailable",
                    "references": [],
                    "tool_activity": [],
                    "pending_actions": [],
                }

    @app.post("/api/actions/prepare")
    async def prepare(body: PrepareInput, request: Request):
        try:
            async with gateway.connect(request.state.session_id) as mcp:
                result = await mcp.call_tool("prepare_invoice", {"order_id": body.order_id})
                if result.isError:
                    message = " ".join(block.text for block in result.content if block.type == "text")
                    return JSONResponse({"error": {"code": "ineligible_order", "message": message}}, 409)
                return result.structuredContent
        except Exception:
            raise ERPUnavailable(
                "Could not prepare the invoice. Check pending actions and the ERP connection."
            ) from None

    @app.post("/api/actions/{action_id}/{operation}")
    async def resolve(action_id: str, operation: str, request: Request):
        if (
            operation not in ("confirm", "cancel")
            or not all(c in "0123456789abcdef" for c in action_id)
            or len(action_id) != 32
        ):
            return JSONResponse({"error": {"code": "not_found", "message": "Unknown action."}}, 404)
        code, body = await gateway.request(
            "POST", f"/actions/{action_id}/{operation}", request.state.session_id
        )
        return JSONResponse(body, code)

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app
