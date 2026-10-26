"""QwenCloud Chat Completions adapter. No ERP access or invoice execution lives here."""

import asyncio
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator


class ModelResponseError(Exception):
    pass


class FunctionCall(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    arguments: str


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["function"] = "function"
    function: FunctionCall


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=20)
    reasoning_content: str | None = Field(default=None, repr=False)

    @field_validator("tool_calls", mode="before")
    @classmethod
    def normalize_no_tools(cls, value):
        return [] if value is None else value

    def for_history(self, thinking=False):
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [call.model_dump() for call in self.tool_calls]
        if thinking and self.reasoning_content:
            message["reasoning_content"] = self.reasoning_content
        return message


class Choice(BaseModel):
    message: AssistantMessage
    finish_reason: str | None = None


class Completion(BaseModel):
    choices: list[Choice] = Field(min_length=1)


def function_tools(catalog):
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in catalog
    ]


class QwenCloudClient:
    def __init__(self, settings, http_client=None):
        self.settings = settings
        self.http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.qwen_timeout_seconds, connect=10),
            trust_env=False,
            follow_redirects=False,
        )
        self.owns_http = http_client is None

    async def complete(self, messages, catalog):
        payload = {
            "model": self.settings.qwen_model,
            "messages": messages,
            "tools": function_tools(catalog),
            "tool_choice": "auto",
            "stream": False,
            "max_completion_tokens": self.settings.qwen_max_completion_tokens,
            "enable_thinking": self.settings.qwen_enable_thinking,
        }
        if self.settings.qwen_enable_thinking:
            payload["preserve_thinking"] = True
        for attempt in range(2):
            response = await self.http.post(
                self.settings.qwen_base_url + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + self.settings.qwen_api_key},
            )
            if attempt == 0 and (response.status_code == 429 or response.status_code >= 500):
                await asyncio.sleep(0.5)
                continue
            response.raise_for_status()
            try:
                parsed = Completion.model_validate(response.json()).choices[0]
            except (ValueError, ValidationError):
                raise ModelResponseError("QwenCloud returned an invalid response.") from None
            calls = parsed.message.tool_calls
            if len({call.id for call in calls}) != len(calls):
                raise ModelResponseError("QwenCloud returned duplicate tool-call IDs.")
            if not calls and not parsed.message.content and parsed.finish_reason != "content_filter":
                raise ModelResponseError("QwenCloud returned no answer or tool request.")
            return parsed

    async def close(self):
        if self.owns_http:
            await self.http.aclose()
