"""A4: инструменты ИИ не пишут в БД напрямую — только через AgentAction.

Каждый инструмент помощника админки и агента вызывается под перехватчиком
SQL: разрешены записи лишь в таблицы предложений (``AgentAction``) и
вопросов владельцу (``AgentQuestion``).
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from django.db import connection

from core.models import AgentAction, AgentPolicy, AgentQuestion, Car, Client, Container, Task
from core.services import admin_ai_tools
from core.services.agent import agent_tools

pytestmark = pytest.mark.django_db

_WRITE_RE = re.compile(r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?(\w+)"?', re.IGNORECASE)
_ALLOWED_TABLES = {AgentAction._meta.db_table, AgentQuestion._meta.db_table}


@pytest.fixture
def objects():
    client = Client.objects.create(name="A4 Client", email="a4@test.com")
    container = Container.objects.create(number="AFOR1234567", status="FLOATING")
    car = Car.objects.create(
        year=2022, brand="Toyota", vin="A4NOWRITE00000001", status="FLOATING", client=client, container=container
    )
    return {"client": client, "container": container, "car": car}


def _universal_args(objects):
    return {
        "query": "A4",
        "title": "Позвонить клиенту",
        "description": "Проверка",
        "content": "Факт",
        "question": "Вопрос?",
        "body": "Текст",
        "model_name": "car",
        "object_id": objects["car"].pk,
        "car_id": objects["car"].pk,
        "container_id": objects["container"].pk,
        "client_id": objects["client"].pk,
        "vin": objects["car"].vin,
        "number": objects["container"].number,
        "name": objects["client"].name,
    }


def _run_capturing_writes(call):
    writes = []

    def wrapper(execute, sql, params, many, context):
        match = _WRITE_RE.match(sql)
        if match:
            writes.append(match.group(1))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(wrapper):
        try:
            call()
        except Exception:
            pass
    return writes


def test_admin_assistant_tools_write_only_agent_actions(objects):
    args = _universal_args(objects)
    seen = set()
    with patch("core.services.ai_rag.build_rag_snippets", return_value=[]):
        for name in admin_ai_tools._TOOLS:
            writes = _run_capturing_writes(lambda name=name: admin_ai_tools.execute_admin_tool(name, dict(args)))
            assert set(writes) <= _ALLOWED_TABLES, f"{name} пишет в {set(writes) - _ALLOWED_TABLES}"
            seen.update(writes)
    # Перехватчик действительно видит записи (create_task → предложение).
    assert AgentAction._meta.db_table in seen


def test_agent_tools_write_only_agent_actions(objects):
    args = _universal_args(objects)
    tools = {**agent_tools._READ_TOOLS, **agent_tools._WRITE_TOOLS}
    for name in tools:
        writes = _run_capturing_writes(lambda name=name: agent_tools.execute_tool(name, dict(args), context={}))
        assert set(writes) <= _ALLOWED_TABLES, f"{name} пишет в {set(writes) - _ALLOWED_TABLES}"


def test_admin_create_task_is_proposed_not_created(objects):
    result = admin_ai_tools.execute_admin_tool(
        "create_task", {"title": "Позвонить клиенту", "car_id": objects["car"].pk, "container_id": 999999}
    )

    assert result["status"] == "proposed"
    assert not Task.objects.exists()
    action = AgentAction.objects.get(pk=result["action_id"])
    assert action.action_type == AgentAction.TYPE_CREATE_TASK
    assert action.status == AgentAction.STATUS_PROPOSED
    assert action.payload["car_id"] == objects["car"].pk
    assert action.payload["container_id"] is None


def test_admin_create_task_auto_policy_creates_task(objects):
    AgentPolicy.objects.create(action_type="CREATE_TASK", mode=AgentPolicy.MODE_AUTO)

    result = admin_ai_tools.execute_admin_tool("create_task", {"title": "Сразу", "car_id": objects["car"].pk})

    assert result["status"] == "created"
    task = Task.objects.get(pk=result["task_id"])
    assert task.origin == Task.ORIGIN_AI
    assert task.car_id == objects["car"].pk


def test_admin_create_task_disabled_policy(objects):
    AgentPolicy.objects.create(action_type="CREATE_TASK", mode=AgentPolicy.MODE_DISABLED)

    result = admin_ai_tools.execute_admin_tool("create_task", {"title": "Нельзя"})

    assert "error" in result
    assert not AgentAction.objects.exists()
