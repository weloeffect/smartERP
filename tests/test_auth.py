import asyncio

import httpx
import pytest

from app.agent import Agent
from app.auth import AuthStore, hash_password, verify_password
from app.config import Settings
from app.web import create_app
from tests.test_web_sessions import FakeGateway

ORIGIN = "http://testserver"
HEADERS = {"Origin": ORIGIN}
CREDENTIALS = {"username": "workspace.admin", "password": "a private local test passphrase"}


@pytest.fixture
def auth_app(tmp_path):
    settings = Settings(
        _env_file=None,
        internal_token="a" * 64,
        qwen_api_key="",
        web_origin=ORIGIN,
        auth_database_path=tmp_path / "auth.sqlite",
    )
    return create_app(settings, FakeGateway(), Agent(settings)), settings


def browser(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def setup(client):
    return await client.post("/api/auth/setup", json=CREDENTIALS, headers=HEADERS)


def test_passwords_are_salted_and_verified():
    first = hash_password(CREDENTIALS["password"])
    second = hash_password(CREDENTIALS["password"])
    assert first != second and CREDENTIALS["password"] not in first
    assert verify_password(CREDENTIALS["password"], first)
    assert not verify_password("incorrect password", first)


@pytest.mark.parametrize(
    "path", ["/api/session", "/api/dashboard", "/api/erp/customers", "/api/erp/orders/ORD-1042"]
)
async def test_records_require_login(auth_app, path):
    app, _ = auth_app
    async with browser(app) as client:
        result = await client.get(path)
        assert result.status_code == 401
        assert result.json()["error"]["code"] == "login_required"
    assert not app.state.conversations


@pytest.mark.parametrize(
    "path",
    [
        "/api/chat",
        "/api/actions/prepare",
        "/api/actions/" + "a" * 32 + "/confirm",
        "/api/actions/" + "a" * 32 + "/cancel",
    ],
)
async def test_mutations_require_login(auth_app, path):
    app, _ = auth_app
    async with browser(app) as client:
        assert (await client.post(path, json={}, headers=HEADERS)).status_code == 401


async def test_first_run_setup_persists_without_plaintext_and_cannot_be_repeated(auth_app):
    app, settings = auth_app
    async with browser(app) as client:
        assert (await client.get("/")).headers["location"] == "/login"
        assert (await client.get("/static/index.html")).status_code == 303
        assert (await client.get("/static//index.html")).status_code == 303
        for path in ("/login", "/static/auth.js", "/static/auth.css", "/static/theme.css", "/health"):
            assert (await client.get(path)).status_code == 200
        assert not client.cookies.get("smartERP_session")
        assert (await client.get("/api/auth/status")).json()["setup_required"] is True
        result = await setup(client)
        assert result.status_code == 200
        assert (
            "HttpOnly" in result.headers["set-cookie"] and "SameSite=strict" in result.headers["set-cookie"]
        )
        assert result.headers["cache-control"] == "no-store"
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/login")).status_code == 303
        assert (await client.get("/api/session")).json()["username"] == CREDENTIALS["username"]
        assert (await setup(client)).status_code == 409
    assert CREDENTIALS["password"].encode() not in settings.auth_database_path.read_bytes()
    restarted = create_app(settings, FakeGateway(), Agent(settings))
    async with browser(restarted) as client:
        assert (await client.get("/api/auth/status")).json()["setup_required"] is False
        assert (await client.post("/api/auth/login", json=CREDENTIALS, headers=HEADERS)).status_code == 200


async def test_invalid_credentials_do_not_create_sessions(auth_app):
    app, _ = auth_app
    async with browser(app) as owner:
        await setup(owner)
    async with browser(app) as client:
        for bad in ({**CREDENTIALS, "username": "another.user"}, {**CREDENTIALS, "password": "wrong"}):
            result = await client.post("/api/auth/login", json=bad, headers=HEADERS)
            assert result.status_code == 401
            assert result.json()["error"]["message"] == "Incorrect username or password."
            assert not client.cookies.get("smartERP_session")
        assert len(app.state.conversations) == 1


async def test_login_rotates_session_and_logout_revokes_cookie(auth_app):
    app, _ = auth_app
    async with browser(app) as client:
        await setup(client)
        old = client.cookies["smartERP_session"]
        await client.post("/api/auth/login", json=CREDENTIALS, headers=HEADERS)
        fresh = client.cookies["smartERP_session"]
        assert old != fresh and old not in app.state.conversations
        assert (await client.post("/api/auth/logout", headers=HEADERS)).status_code == 200
        assert not client.cookies.get("smartERP_session")
        assert fresh not in app.state.conversations
        client.cookies.set("smartERP_session", fresh)
        assert (await client.get("/api/dashboard")).status_code == 401
    assert not app.state.conversations


async def test_expiry_and_password_reset_revoke_sessions(auth_app):
    app, settings = auth_app
    async with browser(app) as client:
        await setup(client)
        cookie = client.cookies["smartERP_session"]
        app.state.conversations[cookie].accessed -= 3601
        assert (await client.get("/api/session")).status_code == 401
        await client.post("/api/auth/login", json=CREDENTIALS, headers=HEADERS)
        AuthStore(settings.auth_database_path).create(
            "new.admin", "a different local passphrase", replace=True
        )
        assert (await client.get("/api/session")).status_code == 401
        assert (await client.post("/api/auth/login", json=CREDENTIALS, headers=HEADERS)).status_code == 401
        assert (
            await client.post(
                "/api/auth/login",
                json={"username": "new.admin", "password": "a different local passphrase"},
                headers=HEADERS,
            )
        ).status_code == 200


async def test_auth_endpoints_reject_cross_origin_requests(auth_app):
    app, _ = auth_app
    async with browser(app) as client:
        assert (await client.post("/api/auth/setup", json=CREDENTIALS)).status_code == 403
        await setup(client)
        for path in ("login", "setup", "logout"):
            result = await client.post(
                f"/api/auth/{path}", json=CREDENTIALS, headers={"Origin": "https://attacker.invalid"}
            )
            assert result.status_code == 403
        assert (await client.get("/api/session")).status_code == 200


async def test_setup_validation_does_not_echo_password(auth_app):
    app, _ = auth_app
    async with browser(app) as client:
        result = await client.post(
            "/api/auth/setup", json={**CREDENTIALS, "password": "secret7"}, headers=HEADERS
        )
        assert result.status_code == 422 and "secret7" not in result.text
        assert (await client.get("/api/auth/status")).json()["setup_required"] is True


async def test_setup_is_only_available_from_local_computer(auth_app):
    app, _ = auth_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 1234)), base_url=ORIGIN
    ) as client:
        assert (await setup(client)).status_code == 403


async def test_account_creation_accepts_eight_character_password(auth_app):
    app, _ = auth_app
    credentials = {**CREDENTIALS, "password": "eight123"}
    async with browser(app) as client:
        result = await client.post("/api/auth/setup", json=credentials, headers=HEADERS)
        assert result.status_code == 200
        await client.post("/api/auth/logout", headers=HEADERS)
        assert (await client.post("/api/auth/login", json=credentials, headers=HEADERS)).status_code == 200


async def test_concurrent_setup_creates_only_one_account(auth_app):
    app, _ = auth_app
    async with browser(app) as first, browser(app) as second:
        results = await asyncio.gather(setup(first), setup(second))
        assert sorted(result.status_code for result in results) == [200, 409]
    assert len(app.state.conversations) == 1


async def test_failed_login_is_rate_limited(auth_app):
    app, _ = auth_app
    async with browser(app) as client:
        await setup(client)
        await client.post("/api/auth/logout", headers=HEADERS)
        for _ in range(10):
            assert (
                await client.post(
                    "/api/auth/login", json={**CREDENTIALS, "password": "wrong"}, headers=HEADERS
                )
            ).status_code == 401
        limited = await client.post("/api/auth/login", json=CREDENTIALS, headers=HEADERS)
        assert limited.status_code == 429 and limited.headers["retry-after"] == "300"


async def test_https_session_cookie_is_secure(tmp_path):
    settings = Settings(
        _env_file=None,
        internal_token="a" * 64,
        qwen_api_key="",
        web_origin="https://testserver",
        auth_database_path=tmp_path / "auth.sqlite",
    )
    app = create_app(settings, FakeGateway(), Agent(settings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=settings.web_origin
    ) as client:
        result = await client.post(
            "/api/auth/setup", json=CREDENTIALS, headers={"Origin": settings.web_origin}
        )
        assert result.status_code == 200
        assert "; Secure" in result.headers["set-cookie"]
