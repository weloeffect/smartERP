import argparse
import asyncio
import os
import secrets
import signal
import subprocess
import sys
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import delete

from app.config import ROOT, Settings
from app.db import open_database
from app.models import AuditEvent, Customer, Invoice, Order, OrderLine, PendingAction, Product
from app.seed import seed


def migrate():
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def configure():
    path = ROOT / ".env"
    if path.exists():
        print(".env already exists; left unchanged.")
        return
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    content = content.replace("replace-with-a-random-token-of-at-least-32-characters", secrets.token_hex(32))
    path.write_text(content, encoding="utf-8")
    print("Created .env with a random internal token. Add DASHSCOPE_API_KEY to enable QwenCloud chat.")


def initialize(reset=False):
    migrate()
    engine, sessions = open_database(Settings().database_url)
    try:
        if reset:
            with sessions.begin() as s:
                for model in (AuditEvent, PendingAction, Invoice, OrderLine, Order, Product, Customer):
                    s.execute(delete(model))
        created = seed(sessions)
        print(
            "Demo data seeded: 20 customers, 30 products, 60 orders, 40 invoices."
            if created
            else "Demo data already exists; left unchanged."
        )
    finally:
        engine.dispose()


def set_login():
    from getpass import getpass

    from pydantic import ValidationError

    from app.auth import AuthStore, SetupInput

    username = input("Username: ").strip()
    password = getpass("Password (at least 8 characters): ")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords do not match. Account unchanged.")
    try:
        credentials = SetupInput(username=username, password=password)
    except ValidationError:
        raise SystemExit(
            "Use a username of 3–40 letters, numbers, dots, underscores or hyphens and a password of 8–128 characters. Account unchanged."
        ) from None
    AuthStore(Settings().auth_database_path).create(credentials.username, credentials.password, replace=True)
    print("Account saved. Existing sessions are revoked; sign in with the new credentials.")


def serve():
    import httpx

    settings = Settings()
    settings.validate_token()
    children = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        # Never start on top of another service, avoiding a web/ERP pairing from different projects.
        import socket

        for port in (8000, 8001):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    raise SystemExit(f"Port {port} is already in use. Stop that service first.")
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.erp_server:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8001",
                    "--no-access-log",
                ],
                cwd=ROOT,
            )
        )
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if children[0].poll() is not None:
                    raise SystemExit("ERP startup failed.")
                try:
                    if client.get(settings.erp_url + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise SystemExit("ERP did not become ready. Run the init command first.")
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.web:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                    "--no-access-log",
                ],
                cwd=ROOT,
            )
        )
        print(
            f"\nsmartERP is opening at {settings.web_origin}. Press Ctrl+C to stop both services.\n",
            flush=True,
        )
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(0.25)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    parser = argparse.ArgumentParser(description="Manage the local smartERP demo")
    parser.add_argument(
        "command", choices=["configure", "init", "seed", "reset", "serve", "evaluate", "set-login"]
    )
    parser.add_argument("--yes", action="store_true", help="Acknowledge deletion of the configured demo data")
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.command == "configure":
        configure()
    elif args.command in ("init", "seed", "reset"):
        if args.command == "reset" and not args.yes:
            parser.error("reset deletes demo records, previews, and audit history; pass --yes to acknowledge")
        initialize(reset=args.command == "reset")
    elif args.command == "serve":
        serve()
    elif args.command == "set-login":
        set_login()
    else:
        from app.evaluate import evaluate

        asyncio.run(evaluate())


if __name__ == "__main__":
    main()
