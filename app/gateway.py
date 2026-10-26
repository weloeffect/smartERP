from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class ERPUnavailable(Exception):
    pass


class Gateway:
    def __init__(self, settings):
        self.settings = settings

    def headers(self, session_id=None):
        headers = {"Authorization": f"Bearer {self.settings.internal_token}"}
        if session_id:
            headers["X-Session-ID"] = session_id
        return headers

    @asynccontextmanager
    async def connect(self, session_id=None):
        async with httpx.AsyncClient(
            headers=self.headers(session_id), timeout=httpx.Timeout(35, connect=10), trust_env=False
        ) as http:
            async with streamable_http_client(self.settings.erp_url + "/mcp", http_client=http) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as client:
                    await client.initialize()
                    yield client

    async def catalog(self):
        async with self.connect() as client:
            result = await client.list_tools()
            return [
                {"name": tool.name, "description": tool.description or "", "input_schema": tool.inputSchema}
                for tool in result.tools
            ]

    async def request(self, method, path, session_id=None, params=None):
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                response = await client.request(
                    method, self.settings.erp_url + path, headers=self.headers(session_id), params=params
                )
                if response.status_code >= 500:
                    raise ERPUnavailable(
                        "The ERP service is unavailable. Check that it is running and initialized."
                    )
                return response.status_code, response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ERPUnavailable("Cannot reach the ERP service. Start it and try again.") from exc
