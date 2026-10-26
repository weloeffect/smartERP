import os
import socket
import subprocess
import sys
import time
from datetime import date

import httpx
import pytest
from alembic import command
from alembic.config import Config

from app.config import ROOT, Settings
from app.db import open_database
from app.seed import seed
from app.services import ERP

TOKEN = "test-internal-token-" + "a" * 48
SESSION_A = "a" * 64
SESSION_B = "b" * 64
AS_OF = date(2026, 10, 24)


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'erp.sqlite').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine, sessions = open_database(url)
    seed(sessions, AS_OF)
    monkeypatch.setattr("app.services.today", lambda: AS_OF)
    erp = ERP(sessions)
    yield erp, sessions, engine
    engine.dispose()


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_stack(database, tmp_path):
    _, _, engine = database
    erp_port, web_port = unused_port(), unused_port()
    erp_url, web_url = f"http://127.0.0.1:{erp_port}", f"http://127.0.0.1:{web_port}"
    env = {
        **os.environ,
        "DATABASE_URL": str(engine.url),
        "INTERNAL_TOKEN": TOKEN,
        "ERP_URL": erp_url,
        "WEB_ORIGIN": web_url,
        "AUTH_DATABASE_PATH": str(tmp_path / "auth.sqlite"),
        "QWEN_API_KEY": "",
        "DASHSCOPE_API_KEY": "",
    }
    children, logs = [], []
    try:
        for module, port, url in (("app.erp_server", erp_port, erp_url), ("app.web", web_port, web_url)):
            log = (tmp_path / f"{module}.log").open("w", encoding="utf-8")
            logs.append(log)
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    f"{module}:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            children.append(child)
            deadline = time.monotonic() + 25
            with httpx.Client(trust_env=False, timeout=1) as client:
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        log.flush()
                        pytest.fail((tmp_path / f"{module}.log").read_text())
                    try:
                        if client.get(url + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    log.flush()
                    pytest.fail((tmp_path / f"{module}.log").read_text())
        yield Settings(
            _env_file=None,
            internal_token=TOKEN,
            database_url=str(engine.url),
            erp_url=erp_url,
            web_origin=web_url,
            auth_database_path=tmp_path / "auth.sqlite",
            qwen_api_key="",
        )
    finally:
        for child in reversed(children):
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()
