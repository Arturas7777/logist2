"""Тесты AI-агента (планировщик дел) — docs/AI_AGENT_PLAN.md.

Покрытие:
- статусная машина AgentAction + политика автономии (AgentPolicy);
- propose_action: ASK / AUTO / DISABLED, HIGH-риск не авто-выполняется;
- исполнители: CREATE_TASK (origin=AI), COMPLETE_TASK;
- email_analyzer с замоканным LLM: CREATE_TASK / ASK_QUESTION / NOTHING,
  отметка agent_analyzed_at, выборка analyze_new_emails;
- память: save/retrieve (keyword-fallback), правило из отклонения;
- llm_client: extract_json, calc_cost_usd, дневной бюджет;
- views доски: рендер, approve/reject, ответ на вопрос.

LLM-вызовы везде замоканы — тесты не ходят в Anthropic/OpenAI.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AgentAction,
    AgentMemory,
    AgentPolicy,
    AgentQuestion,
    AgentRun,
    ContainerEmail,
    Task,
)
from core.services.agent.agent_executor import execute_action, propose_action
from core.services.agent.llm_client import (
    AgentBudgetExceeded,
    calc_cost_usd,
    check_budget,
    extract_json,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def make_email(**kwargs) -> ContainerEmail:
    defaults = {
        "message_id": f"<test-{timezone.now().timestamp()}-{kwargs.get('subject', '')[:10]}@test>",
        "thread_id": "thread-1",
        "direction": ContainerEmail.DIRECTION_INCOMING,
        "from_addr": "client@example.com",
        "subject": "Container MSKU1234567 arrival",
        "body_text": "Container arrives tomorrow, please prepare unloading.",
        "received_at": timezone.now(),
    }
    defaults.update(kwargs)
    return ContainerEmail.objects.create(**defaults)


def make_staff_client(client):
    user = User.objects.create_user("boss", "boss@test.com", "pw", is_staff=True)
    client.force_login(user)
    return user


# ---------------------------------------------------------------------------
# llm_client: utils
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_markdown_block():
    text = 'Вот ответ:\n```json\n{"action": "NOTHING"}\n```\nготово'
    assert extract_json(text) == {"action": "NOTHING"}


def test_extract_json_embedded():
    assert extract_json('бла-бла {"x": [1, 2]} бла') == {"x": [1, 2]}


def test_extract_json_garbage():
    assert extract_json("ничего похожего на json") is None


def test_calc_cost_usd():
    # 1M input = $3, 1M output = $15
    cost = calc_cost_usd(1_000_000, 1_000_000)
    assert float(cost) == pytest.approx(18.0)


def test_budget_exceeded(settings):
    settings.AGENT_DAILY_BUDGET_USD = 0.0
    with pytest.raises(AgentBudgetExceeded):
        check_budget()


# ---------------------------------------------------------------------------
# AgentAction: статусная машина
# ---------------------------------------------------------------------------


def test_action_approve_reject_flow():
    action = AgentAction.objects.create(action_type=AgentAction.TYPE_CREATE_TASK, title="t", payload={})
    assert action.status == AgentAction.STATUS_PROPOSED
    action.approve(by="boss")
    assert action.status == AgentAction.STATUS_APPROVED
    assert action.decided_by == "boss"

    action2 = AgentAction.objects.create(action_type=AgentAction.TYPE_CREATE_TASK, title="t2", payload={})
    action2.reject(by="boss", reason="не надо")
    assert action2.status == AgentAction.STATUS_REJECTED
    assert action2.reject_reason == "не надо"


def test_policy_default_is_ask():
    assert AgentPolicy.mode_for("CREATE_TASK") == AgentPolicy.MODE_ASK


def test_propose_action_disabled_returns_none():
    AgentPolicy.objects.create(action_type="CREATE_TASK", mode=AgentPolicy.MODE_DISABLED)
    action = propose_action(action_type="CREATE_TASK", title="x", payload={})
    assert action is None
    assert AgentAction.objects.count() == 0


def test_propose_action_ask_stays_proposed():
    action = propose_action(action_type="CREATE_TASK", title="x", payload={"title": "Дело"})
    assert action.status == AgentAction.STATUS_PROPOSED
    assert Task.objects.count() == 0


def test_propose_action_auto_executes():
    AgentPolicy.objects.create(action_type="CREATE_TASK", mode=AgentPolicy.MODE_AUTO)
    action = propose_action(
        action_type="CREATE_TASK",
        title="Авто-дело",
        payload={"title": "Авто-дело", "priority": "HIGH"},
    )
    action.refresh_from_db()
    assert action.status == AgentAction.STATUS_AUTO_EXECUTED
    task = Task.objects.get()
    assert task.origin == Task.ORIGIN_AI
    assert task.priority == "HIGH"


def test_propose_action_high_risk_never_auto():
    AgentPolicy.objects.create(action_type="SEND_EMAIL", mode=AgentPolicy.MODE_AUTO)
    action = propose_action(action_type="SEND_EMAIL", title="x", payload={}, risk_level=AgentAction.RISK_HIGH)
    assert action.status == AgentAction.STATUS_PROPOSED


# ---------------------------------------------------------------------------
# Исполнители действий
# ---------------------------------------------------------------------------


def test_execute_create_task_full_payload():
    email = make_email()
    action = AgentAction.objects.create(
        action_type=AgentAction.TYPE_CREATE_TASK,
        title="Подготовить разгрузку",
        payload={
            "title": "Подготовить разгрузку",
            "description": "Прибытие завтра",
            "priority": "MEDIUM",
            "deadline_days": 1,
        },
        source_email=email,
        reasoning="Нотис о прибытии",
    )
    result = execute_action(action, by="boss")
    task = Task.objects.get(pk=result["task_id"])
    assert task.origin == Task.ORIGIN_AI
    assert task.source_email_id == email.pk
    assert task.ai_summary == "Нотис о прибытии"
    assert task.deadline is not None
    action.refresh_from_db()
    assert action.status == AgentAction.STATUS_EXECUTED
    assert action.task_id == task.pk


def test_execute_complete_task():
    task = Task.objects.create(title="Старое дело")
    action = AgentAction.objects.create(
        action_type=AgentAction.TYPE_COMPLETE_TASK,
        title="Закрыть",
        payload={"task_id": task.pk, "comment": "Сделано агентом"},
    )
    execute_action(action, by="boss")
    task.refresh_from_db()
    assert task.is_completed
    assert "[ИИ] Сделано агентом" in task.description


def test_execute_unknown_action_type_raises():
    action = AgentAction.objects.create(action_type="OTHER", title="x", payload={})
    with pytest.raises(ValueError):
        execute_action(action)


# ---------------------------------------------------------------------------
# email_analyzer (LLM замокан)
# ---------------------------------------------------------------------------


def test_analyze_email_creates_task_proposal():
    from core.services.agent import email_analyzer

    email = make_email()
    response = {
        "sender_role": "линия",
        "intent": "Нотис о прибытии",
        "related": "MSKU1234567",
        "action": "CREATE_TASK",
        "task": {
            "title": "Подготовить разгрузку MSKU1234567",
            "description": "Контейнер прибывает завтра",
            "priority": "HIGH",
            "deadline_days": 1,
        },
        "confidence": 0.9,
        "reasoning": "Прибытие требует подготовки",
    }
    with patch("core.services.agent.llm_client.AgentLLMClient") as mock_cls:
        mock_cls.return_value.complete_json.return_value = response
        outcome = email_analyzer.analyze_email(email)

    assert outcome["action"] == "CREATE_TASK"
    action = AgentAction.objects.get()
    assert action.status == AgentAction.STATUS_PROPOSED
    assert action.source_email_id == email.pk
    assert action.payload["priority"] == "HIGH"
    email.refresh_from_db()
    assert email.agent_analyzed_at is not None
    run = AgentRun.objects.get(kind=AgentRun.KIND_EMAIL_ANALYSIS)
    assert run.status == AgentRun.STATUS_SUCCESS


def test_analyze_email_creates_question():
    from core.services.agent import email_analyzer

    email = make_email(subject="Strange request")
    response = {
        "sender_role": "другое",
        "intent": "Непонятный запрос",
        "related": "",
        "action": "ASK_QUESTION",
        "question": "Как обрабатывать письма о страховке?",
        "confidence": 0.4,
        "reasoning": "Нет правила в памяти",
    }
    with patch("core.services.agent.llm_client.AgentLLMClient") as mock_cls:
        mock_cls.return_value.complete_json.return_value = response
        outcome = email_analyzer.analyze_email(email)

    assert outcome["action"] == "ASK_QUESTION"
    question = AgentQuestion.objects.get()
    assert question.status == AgentQuestion.STATUS_OPEN
    assert question.source_email_id == email.pk
    assert "страховке" in question.question


def test_analyze_email_nothing():
    from core.services.agent import email_analyzer

    email = make_email(subject="Newsletter")
    response = {
        "sender_role": "спам_рассылка",
        "intent": "Рассылка",
        "related": "",
        "action": "NOTHING",
        "confidence": 0.95,
        "reasoning": "Не требует действий",
    }
    with patch("core.services.agent.llm_client.AgentLLMClient") as mock_cls:
        mock_cls.return_value.complete_json.return_value = response
        outcome = email_analyzer.analyze_email(email)

    assert outcome["action"] == "NOTHING"
    assert AgentAction.objects.count() == 0
    assert AgentQuestion.objects.count() == 0


def test_analyze_email_llm_error_marks_analyzed():
    """Ошибка LLM не зацикливает очередь: письмо помечается разобранным."""
    from core.services.agent import email_analyzer

    email = make_email()
    with patch("core.services.agent.llm_client.AgentLLMClient") as mock_cls:
        mock_cls.return_value.complete_json.return_value = None
        outcome = email_analyzer.analyze_email(email)

    assert "error" in outcome
    email.refresh_from_db()
    assert email.agent_analyzed_at is not None
    run = AgentRun.objects.get()
    assert run.status == AgentRun.STATUS_ERROR


def test_analyze_new_emails_selection():
    """Берёт только INCOMING без agent_analyzed_at и не старше 14 дней."""
    from core.services.agent import email_analyzer

    fresh = make_email(subject="fresh")
    make_email(
        subject="old",
        message_id="<old@test>",
        received_at=timezone.now() - timezone.timedelta(days=30),
    )
    make_email(
        subject="outgoing",
        message_id="<out@test>",
        direction=ContainerEmail.DIRECTION_OUTGOING,
    )
    analyzed = make_email(subject="done", message_id="<done@test>")
    analyzed.agent_analyzed_at = timezone.now()
    analyzed.save(update_fields=["agent_analyzed_at"])

    seen: list[int] = []

    def fake_analyze(email):
        seen.append(email.pk)
        return {"action": "NOTHING"}

    with patch.object(email_analyzer, "analyze_email", side_effect=fake_analyze):
        report = email_analyzer.analyze_new_emails()

    assert seen == [fresh.pk]
    assert report["processed"] == 1


# ---------------------------------------------------------------------------
# Память
# ---------------------------------------------------------------------------


def test_save_and_retrieve_memory_keyword_fallback():
    from core.services.agent.memory import retrieve_memories, save_memory

    with patch("core.services.agent.memory.embed_text", return_value=None):
        save_memory(content="Письма про страховку пересылать брокеру", kind="RULE")
        save_memory(content="Склад в Клайпеде работает до 18:00", kind="FACT")
        results = retrieve_memories("как обработать письмо про страховку?")

    assert results
    assert "страховку" in results[0].content
    results[0].refresh_from_db()
    assert results[0].times_used == 1


def test_rejection_memory():
    from core.services.agent.memory import add_rejection_memory

    action = AgentAction.objects.create(action_type="CREATE_TASK", title="Дело про рассылку", payload={})
    with patch("core.services.agent.memory.embed_text", return_value=None):
        memory = add_rejection_memory(action, "это рассылка, дел не создавать", by="boss")

    assert memory.source == AgentMemory.SOURCE_REJECTION
    assert "рассылка" in memory.content


def test_rejection_memory_empty_reason_skipped():
    from core.services.agent.memory import add_rejection_memory

    action = AgentAction.objects.create(action_type="CREATE_TASK", title="x", payload={})
    assert add_rejection_memory(action, "  ") is None


def test_format_memories_block():
    from core.services.agent.memory import format_memories_block

    assert format_memories_block([]) == ""
    memory = AgentMemory(kind="RULE", content="правило")
    block = format_memories_block([memory])
    assert "правило" in block
    assert "ПАМЯТЬ АГЕНТА" in block


# ---------------------------------------------------------------------------
# Views доски
# ---------------------------------------------------------------------------


def test_board_page_renders(client):
    make_staff_client(client)
    Task.objects.create(title="Открытое дело", priority="HIGH")
    AgentAction.objects.create(
        action_type="CREATE_TASK",
        title="Предложение",
        payload={"description": "тест"},
    )
    AgentQuestion.objects.create(question="Как поступать с X?")

    response = client.get(reverse("tasks_board"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Открытое дело" in content
    assert "Предложение" in content
    assert "Как поступать с X?" in content


def test_board_requires_staff(client):
    response = client.get(reverse("tasks_board"))
    assert response.status_code == 302  # redirect to admin login


def test_board_approve_creates_task(client):
    make_staff_client(client)
    action = AgentAction.objects.create(
        action_type="CREATE_TASK",
        title="Создать дело",
        payload={"title": "Из письма", "priority": "MEDIUM"},
    )
    response = client.post(reverse("agent_action_approve", args=[action.pk]))
    assert response.status_code == 302
    action.refresh_from_db()
    assert action.status == AgentAction.STATUS_EXECUTED
    assert Task.objects.filter(title="Из письма", origin=Task.ORIGIN_AI).exists()


def test_board_reject_with_reason_saves_memory(client):
    make_staff_client(client)
    action = AgentAction.objects.create(action_type="CREATE_TASK", title="Лишнее дело", payload={})
    with patch("core.services.agent.memory.embed_text", return_value=None):
        response = client.post(
            reverse("agent_action_reject", args=[action.pk]),
            {"reason": "такие письма игнорируем"},
        )
    assert response.status_code == 302
    action.refresh_from_db()
    assert action.status == AgentAction.STATUS_REJECTED
    assert AgentMemory.objects.filter(source=AgentMemory.SOURCE_REJECTION).count() == 1


def test_board_answer_question(client):
    """Ответ сохраняется; дистилляция без LLM-ключа падает в fallback-память."""
    make_staff_client(client)
    question = AgentQuestion.objects.create(
        question="Что делать с письмами о страховке?",
        context_json={"summary": "письмо от страховой"},
    )
    with patch("core.services.agent.memory.embed_text", return_value=None):
        response = client.post(
            reverse("agent_question_answer", args=[question.pk]),
            {"answer": "Пересылать брокеру Ивану"},
        )
    assert response.status_code == 302
    question.refresh_from_db()
    assert question.status == AgentQuestion.STATUS_ANSWERED
    assert question.answer == "Пересылать брокеру Ивану"
    # Fallback-память создана (LLM недоступен в тестах).
    assert AgentMemory.objects.filter(source=AgentMemory.SOURCE_QUESTION).exists()


def test_board_complete_task(client):
    user = make_staff_client(client)
    task = Task.objects.create(title="Сделать руками")
    response = client.post(reverse("task_complete_from_board", args=[task.pk]))
    assert response.status_code == 302
    task.refresh_from_db()
    assert task.is_completed
    assert task.completed_by == user.username


def test_board_delegate_disabled_agent(client):
    """При выключенном агенте «Поручить ИИ» даёт понятную ошибку."""
    make_staff_client(client)
    task = Task.objects.create(title="Дело для ИИ")
    response = client.post(reverse("task_delegate_to_agent", args=[task.pk]))
    assert response.status_code == 302
    task.refresh_from_db()
    assert not task.is_completed  # ничего не произошло


# ---------------------------------------------------------------------------
# Планировщик (LLM замокан)
# ---------------------------------------------------------------------------


def test_morning_digest(client):
    from core.services.agent import task_planner

    Task.objects.create(title="Просроченное", deadline=timezone.now() - timezone.timedelta(days=1))
    response = {
        "digest": "1. Закрыть просроченное дело",
        "top_task_ids": [Task.objects.get().pk],
        "warnings": "",
    }
    with patch("core.services.agent.llm_client.AgentLLMClient") as mock_cls:
        mock_cls.return_value.complete_json.return_value = response
        result = task_planner.build_morning_digest()

    assert "Закрыть просроченное" in result["digest"]
    latest = task_planner.get_latest_digest()
    assert latest is not None
    assert latest["digest"] == result["digest"]


def test_signal_auto_task_gets_origin():
    """Сигнал Car.is_important проставляет origin=AUTO_CAR (фаза 0)."""
    from core.models import Car

    car = Car.objects.create(
        year=2023,
        brand="BMW",
        vin="AGENTSIGNAL000001",
        status="FLOATING",
        is_important=True,
    )
    task = Task.objects.get(car=car, auto_created=True)
    assert task.origin == Task.ORIGIN_AUTO_CAR
