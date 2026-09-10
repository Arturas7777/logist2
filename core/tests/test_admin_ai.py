"""Тесты Grok-помощника админки и клиентского чата.

LLM мокается — в сеть тесты не ходят.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from core.models import Car, Client, Container, Task
from core.services.admin_ai_agent import generate_admin_ai_response
from core.services.admin_ai_tools import execute_admin_tool, parse_tool_arguments
from core.services.ai_chat_service import generate_ai_response
from core.services.ai_provider import ChatResult, ToolCall, openai_tools_from_specs

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_row():
    return Client.objects.create(name="Петров Логистик", email="petrov@test.com", country="BY")


@pytest.fixture
def container_row():
    return Container.objects.create(number="MSKU1234567", status="FLOATING")


@pytest.fixture
def car_row(client_row, container_row):
    return Car.objects.create(
        year=2021,
        brand="Toyota Camry",
        vin="1HGBH41JXMN109186",
        status="FLOATING",
        client=client_row,
        container=container_row,
    )


def test_openai_tools_from_specs():
    tools = openai_tools_from_specs(
        [{"name": "search_cars", "description": "Поиск", "input_schema": {"type": "object", "properties": {}}}]
    )
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "search_cars"


def test_parse_tool_arguments_invalid():
    assert parse_tool_arguments("not-json") == {}
    assert parse_tool_arguments('{"query": "VIN"}') == {"query": "VIN"}


def test_search_clients_returns_balance(client_row):
    rows = execute_admin_tool("search_clients", {"query": "Петров"})
    assert len(rows) == 1
    assert rows[0]["name"] == "Петров Логистик"
    assert "total_balance" in rows[0]


def test_search_cars_and_get_car(car_row):
    found = execute_admin_tool("search_cars", {"query": "1HGBH41JXMN109186"})
    assert found[0]["vin"] == "1HGBH41JXMN109186"
    detail = execute_admin_tool("get_car", {"vin": "1HGBH41JXMN109186"})
    assert "1HGBH41JXMN109186" in detail["summary"]
    assert "/admin/core/car/" in detail["admin_url"]


def test_get_container(container_row, car_row):
    detail = execute_admin_tool("get_container", {"number": "MSKU1234567"})
    assert detail["cars"][0]["vin"] == car_row.vin
    assert "/admin/core/container/" in detail["admin_url"]


def test_create_task_from_admin_tool(car_row):
    result = execute_admin_tool(
        "create_task",
        {"title": "Позвонить клиенту", "description": "Уточнить выдачу", "car_id": car_row.pk},
    )
    assert result["status"] == "created"
    task = Task.objects.get(pk=result["task_id"])
    assert task.origin == Task.ORIGIN_AI
    assert task.car_id == car_row.pk


def test_unknown_tool():
    assert "error" in execute_admin_tool("wipe_database", {})


def test_admin_ai_uses_tools_then_answers(car_row, settings):
    settings.AI_CHAT_ENABLED = True
    settings.AI_API_KEY = "test-key"
    settings.AI_ADMIN_TOOL_ROUNDS = 4
    user = User.objects.create_user("boss", "boss@test.com", "pw", is_staff=True)

    calls = {"n": 0}

    def fake_completion(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return ChatResult(
                content="",
                tool_calls=[ToolCall(id="call_1", name="search_cars", arguments='{"query": "1HGBH41JXMN109186"}')],
                raw_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search_cars", "arguments": '{"query": "1HGBH41JXMN109186"}'},
                        }
                    ],
                },
            )
        return ChatResult(content="Авто Toyota Camry, статус в пути.", tool_calls=[], raw_message={})

    with patch("core.services.admin_ai_agent.chat_completion", side_effect=fake_completion):
        result = generate_admin_ai_response("Где машина 1HGBH41JXMN109186?", user=user)

    assert result["used_fallback"] is False
    assert "Toyota" in result["response"]
    assert calls["n"] == 2


def test_client_chat_forbids_finance_in_prompt(settings):
    settings.AI_CHAT_ENABLED = True
    settings.AI_API_KEY = "test-key"
    captured = {}

    def fake_completion(messages, **kwargs):
        captured["messages"] = messages
        return ChatResult(content="Уточните VIN.", tool_calls=[])

    with patch("core.services.ai_chat_service.chat_completion", side_effect=fake_completion):
        text = generate_ai_response("Привет")

    assert text == "Уточните VIN."
    joined = " ".join(m["content"] for m in captured["messages"] if m.get("role") == "system")
    assert "финансов" in joined.lower() or "инвойс" in joined.lower()


def test_embeddings_use_openai_not_xai(settings, monkeypatch):
    settings.AI_EMBEDDINGS_API_KEY = "openai-test"
    settings.AI_EMBEDDINGS_BASE_URL = "https://api.openai.com/v1"
    settings.AI_API_BASE_URL = "https://api.x.ai/v1"
    settings.AI_API_KEY = "xai-test"

    seen = {}

    class DummyResponse:
        ok = True

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2]}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["auth"] = kwargs["headers"]["Authorization"]
        return DummyResponse()

    class DummySession:
        trust_env = False

        def post(self, url, **kwargs):
            return fake_post(url, **kwargs)

    monkeypatch.setattr("core.services.ai_rag.requests.Session", DummySession)
    from core.services.ai_rag import _call_embeddings_api

    vector = _call_embeddings_api("hello", use_cache=False)
    assert vector == [0.1, 0.2]
    assert seen["url"].startswith("https://api.openai.com/v1/embeddings")
    assert "openai-test" in seen["auth"]
