"""Единый Anthropic-клиент AI-агента.

Обязанности:

* один способ дёргать Anthropic Messages API для всех модулей агента;
* учёт токенов и стоимости в :class:`core.models.AgentRun`;
* дневной бюджет (``AGENT_DAILY_BUDGET_USD``) — при превышении агент
  останавливается до следующего дня, вместо того чтобы сжечь счёт;
* tool-use цикл (:meth:`AgentLLMClient.run_tool_loop`) для фаз 4-5;
* извлечение JSON из ответа модели (:func:`extract_json`).

Ключ — ``ANTHROPIC_API_KEY`` (общий с invoice audit).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from decimal import Decimal
from typing import Any, Callable

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Цены claude-sonnet-4 за 1M токенов, $. Если модель сменится — обновить.
_PRICE_PER_MTOK_INPUT = Decimal("3.00")
_PRICE_PER_MTOK_OUTPUT = Decimal("15.00")

_BUDGET_CACHE_PREFIX = "agent:spent_usd:"


class AgentDisabled(Exception):
    """Агент выключен (AGENT_ENABLED=False) или нет API-ключа."""


class AgentBudgetExceeded(Exception):
    """Дневной бюджет LLM-расходов исчерпан."""


def _budget_cache_key() -> str:
    return f"{_BUDGET_CACHE_PREFIX}{timezone.localdate().isoformat()}"


def get_today_spent_usd() -> float:
    """Потрачено на LLM сегодня (по кэш-счётчику; БД — источник истины)."""
    return float(cache.get(_budget_cache_key()) or 0.0)


def _register_spent(cost_usd: Decimal) -> None:
    key = _budget_cache_key()
    spent = Decimal(str(cache.get(key) or "0"))
    # TTL 2 суток: ключ доживает до конца дня в любой таймзоне.
    cache.set(key, str(spent + cost_usd), 2 * 24 * 3600)


def check_budget() -> None:
    """Бросает :class:`AgentBudgetExceeded`, если дневной лимит исчерпан."""
    limit = float(getattr(settings, "AGENT_DAILY_BUDGET_USD", 5.0))
    spent = get_today_spent_usd()
    if spent >= limit:
        raise AgentBudgetExceeded(f"Дневной бюджет агента исчерпан: ${spent:.2f} из ${limit:.2f}")


def calc_cost_usd(input_tokens: int, output_tokens: int) -> Decimal:
    return Decimal(input_tokens) * _PRICE_PER_MTOK_INPUT / Decimal(1_000_000) + Decimal(
        output_tokens
    ) * _PRICE_PER_MTOK_OUTPUT / Decimal(1_000_000)


def extract_json(text: str) -> dict | None:
    """Достаёт JSON-объект из ответа модели (с ```json-обёрткой или без)."""
    if not text:
        return None
    # Сначала пробуем как есть.
    try:
        return json.loads(text)
    except ValueError:
        pass
    # Потом — из markdown-блока или первого {...}.
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        match = re.search(r"(\{.*\})", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


class AgentLLMClient:
    """Обёртка Anthropic Messages API с учётом расходов в AgentRun.

    Использование::

        run = AgentRun.objects.create(kind=AgentRun.KIND_EMAIL_ANALYSIS, input_ref="email:1")
        client = AgentLLMClient(run=run)
        text = client.complete(system="...", messages=[{"role": "user", "content": "..."}])
    """

    def __init__(self, run=None, model: str | None = None):
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise AgentDisabled("ANTHROPIC_API_KEY не задан")
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model or getattr(settings, "AGENT_MODEL", "claude-sonnet-4-20250514")
        self.run = run
        if run is not None and not run.model:
            run.model = self.model

    # ------------------------------------------------------------------
    def _track_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost = calc_cost_usd(input_tokens, output_tokens)
        _register_spent(cost)
        if self.run is not None:
            self.run.input_tokens += input_tokens
            self.run.output_tokens += output_tokens
            self.run.cost_usd = Decimal(self.run.cost_usd) + cost

    def _create_message(self, **kwargs):
        """messages.create с retry на перегрузку/сетевые ошибки."""
        import anthropic

        check_budget()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=int(getattr(settings, "AGENT_MAX_TOKENS", 2000)),
                    timeout=int(getattr(settings, "AGENT_REQUEST_TIMEOUT", 60)),
                    **kwargs,
                )
                self._track_usage(response)
                return response
            except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as exc:
                last_exc = exc
                wait = 2 ** (attempt + 1)
                logger.warning("Anthropic retry %d/3 после %s (ждём %ds)", attempt + 1, type(exc).__name__, wait)
                time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    def complete(self, *, system: str, messages: list[dict]) -> str:
        """Один запрос без инструментов → текст ответа."""
        response = self._create_message(system=system, messages=messages)
        parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
        return "\n".join(parts).strip()

    def complete_json(self, *, system: str, messages: list[dict]) -> dict | None:
        """Один запрос → распарсенный JSON-объект (или None)."""
        return extract_json(self.complete(system=system, messages=messages))

    # ------------------------------------------------------------------
    def run_tool_loop(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Any],
        max_iterations: int = 10,
    ) -> str:
        """Tool-use цикл: модель вызывает инструменты, пока не даст финальный текст.

        ``tools`` — спецификации в формате Anthropic
        (``{"name", "description", "input_schema"}``).
        ``tool_executor(name, args) -> Any`` — выполняет инструмент и
        возвращает JSON-сериализуемый результат.
        """
        conversation = list(messages)
        for _ in range(max_iterations):
            response = self._create_message(system=system, messages=conversation, tools=tools)

            tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
            text_parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]

            if response.stop_reason != "tool_use" or not tool_uses:
                return "\n".join(text_parts).strip()

            # Ассистентский ход с tool_use — добавляем как есть.
            conversation.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_use in tool_uses:
                try:
                    result = tool_executor(tool_use.name, dict(tool_use.input or {}))
                    content = json.dumps(result, ensure_ascii=False, default=str)[:20000]
                    tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": content})
                except Exception as exc:
                    logger.exception("Инструмент %s упал", tool_use.name)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": f"ERROR: {exc}",
                            "is_error": True,
                        }
                    )
            conversation.append({"role": "user", "content": tool_results})

        logger.warning("Tool-loop достиг лимита %d итераций", max_iterations)
        return "Достигнут лимит итераций tool-loop; задача не завершена."


def agent_is_enabled() -> bool:
    """Агент включён и сконфигурирован (флаг + ключ)."""
    return bool(getattr(settings, "AGENT_ENABLED", False)) and bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
