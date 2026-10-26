import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm import ModelResponseError, QwenCloudClient
from tests.test_agent import CATALOG, call


def test_dashscope_key_alias_and_secret_repr(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "  test-secret-dashscope  ")
    settings = Settings(_env_file=None)
    assert settings.qwen_api_key == "test-secret-dashscope"
    assert "test-secret-dashscope" not in repr(settings)
    monkeypatch.setenv("QWEN_API_KEY", "test-preferred-key")
    assert Settings(_env_file=None).qwen_api_key == "test-preferred-key"


@pytest.mark.parametrize(
    "url",
    [
        "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1/",
        "https://eu-central-1.maas.qwencloudapi.com/compatible-mode/v1",
        "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_cloud_endpoints_configurable(url):
    assert Settings(_env_file=None, qwen_base_url=url).qwen_base_url == url.rstrip("/")


@pytest.mark.parametrize(
    "overrides",
    [
        {"qwen_base_url": "http://example.test/compatible-mode/v1"},
        {"qwen_base_url": "https://secret@example.test/compatible-mode/v1"},
        {"qwen_base_url": "https://example.test/chat/completions"},
        {"qwen_base_url": "https://example.test/compatible-mode/v1?key=secret"},
        {"qwen_timeout_seconds": 0},
        {"qwen_model": "   "},
    ],
)
def test_invalid_cloud_configuration_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


async def test_nullable_tool_calls_and_thinking_request():
    settings = Settings(_env_file=None, qwen_api_key="test-key", qwen_enable_thinking=True)
    requests = []

    def handler(request):
        import json

        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Hello", "tool_calls": None}, "finish_reason": "stop"}]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await QwenCloudClient(settings, http).complete([], CATALOG)
    assert result.message.content == "Hello" and result.message.tool_calls == []
    assert requests[0]["enable_thinking"] and requests[0]["preserve_thinking"]


async def test_duplicate_call_ids_rejected():
    settings = Settings(_env_file=None, qwen_api_key="test-key")
    calls = [call("get_order", id="a").model_dump(), call("get_order", id="a").model_dump()]

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": calls}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ModelResponseError):
            await QwenCloudClient(settings, http).complete([], CATALOG)
