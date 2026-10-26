import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from app.agent import Agent
from app.config import Settings
from app.web import create_app
from tests.test_agent import response


class FakeGateway:
    async def catalog(self):
        return [{"name": "get_order", "description": "Read", "input_schema": {"type": "object"}}]

    async def request(self, *args, **kwargs):
        return 200, {"status": "ready"}

    @asynccontextmanager
    async def connect(self, session_id):
        yield SimpleNamespace()


async def test_conversations_are_isolated_and_retained_per_cookie(tmp_path):
    async def complete(messages, catalog):
        return response(f"Context messages: {len(messages) - 1}")

    settings = Settings(
        _env_file=None,
        internal_token="a" * 64,
        web_origin="http://testserver",
        auth_database_path=tmp_path / "auth.sqlite",
    )
    agent = Agent(settings, SimpleNamespace(complete=AsyncMock(side_effect=complete)))
    app = create_app(settings, FakeGateway(), agent)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as first,
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as second,
    ):
        credentials = {"username": "tester", "password": "test passphrase for sessions"}
        headers = {"Origin": "http://testserver"}
        assert (await first.post("/api/auth/setup", json=credentials, headers=headers)).status_code == 200
        assert (await second.post("/api/auth/login", json=credentials, headers=headers)).status_code == 200

        async def ask(client):
            return (
                await client.post(
                    "/api/chat", json={"message": "Hello"}, headers={"Origin": "http://testserver"}
                )
            ).json()["answer"]

        assert await ask(first) == "Context messages: 1"
        assert await ask(first) == "Context messages: 3"
        assert await ask(second) == "Context messages: 1"
        assert first.cookies["smartERP_session"] != second.cookies["smartERP_session"]
        assert len(app.state.conversations) == 2


async def test_health_probes_do_not_allocate_chat_sessions(tmp_path):
    settings = Settings(
        _env_file=None, internal_token="a" * 64, qwen_api_key="", auth_database_path=tmp_path / "auth.sqlite"
    )
    app = create_app(settings, FakeGateway(), Agent(settings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for _ in range(205):
            result = await client.get("/health")
            assert result.status_code == 200
            assert "smartERP_session" not in client.cookies
    assert app.state.conversations == {}


async def test_whole_question_timeout_has_clear_error(tmp_path):
    async def slow_complete(messages, catalog):
        await asyncio.sleep(0.05)
        return response("Too late")

    settings = Settings(
        _env_file=None,
        internal_token="a" * 64,
        web_origin="http://testserver",
        chat_timeout_seconds=0.001,
        auth_database_path=tmp_path / "auth.sqlite",
    )
    agent = Agent(settings, SimpleNamespace(complete=AsyncMock(side_effect=slow_complete)))
    app = create_app(settings, FakeGateway(), agent)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.post(
            "/api/auth/setup",
            json={"username": "tester", "password": "test passphrase for sessions"},
            headers={"Origin": "http://testserver"},
        )
        result = await client.post(
            "/api/chat", json={"message": "Hello"}, headers={"Origin": "http://testserver"}
        )
    assert result.json()["error"] == "chat_timeout"
    assert "ERP connection failed" not in result.json()["answer"]
