"""HTTP-клиент для чата (xAI Grok) в формате OpenAI Chat Completions.

Клиентский портал и помощник админки используют этот модуль.
Embeddings живут отдельно в ``ai_rag.py`` и ходят в OpenAI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Ошибка вызова LLM (ключ, сеть, ответ провайдера)."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def openai_tools_from_specs(specs: list[dict]) -> list[dict]:
    """Anthropic-подобные ``{name, description, input_schema}`` → OpenAI tools."""
    tools = []
    for spec in specs:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec.get("description") or "",
                    "parameters": spec.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return tools


def chat_completion(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> ChatResult:
    """Один запрос ``/chat/completions``. Не крутит tool-loop сам."""
    if not settings.AI_CHAT_ENABLED:
        raise AIServiceError("AI chat is disabled")

    api_key = (settings.AI_API_KEY or "").strip()
    if not api_key:
        raise AIServiceError("AI API key is missing")

    base_url = settings.AI_API_BASE_URL.rstrip("/")
    url = f"{base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model or settings.AI_MODEL,
        "messages": messages,
        "temperature": settings.AI_TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens if max_tokens is not None else settings.AI_MAX_TOKENS,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    try:
        session = requests.Session()
        session.trust_env = False
        response = session.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings.AI_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception("AI API request failed")
        raise AIServiceError(f"AI API request failed: {exc.__class__.__name__}: {exc}") from exc

    if not response.ok:
        error_text = response.text[:500] if response.text else ""
        logger.error("AI API error: %s - %s", response.status_code, error_text)
        raise AIServiceError(f"AI API returned error ({response.status_code}): {error_text}")

    data = response.json()
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("AI API response parsing error: %s", data)
        raise AIServiceError("AI API response parsing error") from exc

    content = (message.get("content") or "").strip()
    tool_calls = []
    for item in message.get("tool_calls") or []:
        function = item.get("function") or {}
        tool_calls.append(
            ToolCall(
                id=str(item.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=function.get("arguments") or "{}",
            )
        )

    return ChatResult(content=content, tool_calls=tool_calls, raw_message=message)
