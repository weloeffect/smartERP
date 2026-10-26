"""A single local account, with salted password hashes and no default credentials."""

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ITERATIONS = 600_000


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128, repr=False)


class SetupInput(LoginInput):
    password: str = Field(min_length=8, max_length=128, repr=False)


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    algorithm, iterations, salt, expected = encoded.split("$")
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    return secrets.compare_digest(actual, bytes.fromhex(expected))


class AuthStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS account ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL, "
                "password_hash TEXT NOT NULL, version TEXT NOT NULL)"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def account(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM account WHERE id = 1").fetchone()
            return dict(row) if row else None

    def create(self, username, password, *, replace=False):
        password_hash, version = hash_password(password), secrets.token_hex(16)
        with self.connect() as db:
            if replace:
                db.execute(
                    "INSERT INTO account VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "username=excluded.username, password_hash=excluded.password_hash, version=excluded.version",
                    (username, password_hash, version),
                )
            else:
                try:
                    db.execute("INSERT INTO account VALUES (1, ?, ?, ?)", (username, password_hash, version))
                except sqlite3.IntegrityError:
                    return None  # Another request already completed first-run setup.
        return {"username": username, "version": version}

    def verify(self, username, password):
        account = self.account()
        if not account:
            return None
        valid_password = verify_password(password, account["password_hash"])
        valid_username = secrets.compare_digest(
            username.lower().encode(), account["username"].lower().encode()
        )
        return account if valid_password and valid_username else None
