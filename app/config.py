from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", populate_by_name=True)

    database_url: str = f"sqlite:///{(ROOT / 'data' / 'erp.sqlite').as_posix()}"
    internal_token: str = ""
    qwen_api_key: str = Field(
        default="", repr=False, validation_alias=AliasChoices("QWEN_API_KEY", "DASHSCOPE_API_KEY")
    )
    qwen_base_url: str = "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1"
    qwen_model: str = Field(default="qwen3.7-plus", min_length=1, max_length=150)
    qwen_timeout_seconds: float = Field(default=60, gt=0, le=300)
    qwen_max_completion_tokens: int = Field(default=4096, ge=256, le=32768)
    qwen_enable_thinking: bool = False
    chat_timeout_seconds: float = Field(default=180, gt=0, le=1200)
    erp_url: str = "http://127.0.0.1:8001"
    web_origin: str = "http://127.0.0.1:8000"
    auth_database_path: Path = ROOT / "data" / "auth.sqlite"

    @field_validator("qwen_base_url")
    @classmethod
    def validate_qwen_url(cls, value):
        from urllib.parse import urlsplit

        parts = urlsplit(value.strip())
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or not parts.path.rstrip("/").endswith("/compatible-mode/v1")
        ):
            raise ValueError("Use a QwenCloud HTTPS Base URL ending in /compatible-mode/v1.")
        return value.strip().rstrip("/")

    @field_validator("qwen_api_key", "qwen_model", mode="before")
    @classmethod
    def strip_qwen_value(cls, value):
        return value.strip()

    def validate_token(self):
        if len(self.internal_token) < 32 or self.internal_token.startswith("replace-"):
            raise RuntimeError("Run `uv run python -m app.cli configure` to configure the internal token.")


def now():
    return datetime.now(ZoneInfo("Europe/Paris"))


def today():
    return now().date()
