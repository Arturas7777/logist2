"""Страница «Дела» с AI-агентом — /admin/tasks-board/.

Зоны страницы:
  * утренний дайджест агента (последний AgentRun kind=PLANNER);
  * вопросы агента (ответить инлайн → дистилляция в память);
  * предложения агента (AgentAction PROPOSED: принять/отклонить);
  * активные дела (с кнопкой «Поручить ИИ»).

POST-экшены принимают форму и возвращают redirect на страницу
(классическая server-rendered страница без SPA-магии).
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.models import AgentAction, AgentPolicy, AgentQuestion, AgentRun, Task

logger = logging.getLogger(__name__)


def _username(request: HttpRequest) -> str:
    return request.user.username or request.user.get_full_name() or ""


@staff_member_required
@require_GET
def tasks_board_page(request: HttpRequest):
    """Главная страница «Дела + ИИ»."""
    from django.conf import settings

    from core.services.agent.llm_client import agent_is_enabled, get_today_spent_usd
    from core.services.agent.task_planner import get_latest_digest

    open_tasks = (
        Task.objects.filter(is_completed=False)
        .select_related("car", "container", "source_email")
        .order_by("deadline", "-created_at")[:100]
    )
    proposals = (
        AgentAction.objects.filter(status=AgentAction.STATUS_PROPOSED)
        .select_related("source_email", "task")
        .order_by("-created_at")[:50]
    )
    questions = (
        AgentQuestion.objects.filter(status=AgentQuestion.STATUS_OPEN)
        .select_related("source_email")
        .order_by("-created_at")[:50]
    )
    recent_actions = (
        AgentAction.objects.exclude(status=AgentAction.STATUS_PROPOSED)
        .select_related("task")
        .order_by("-created_at")[:15]
    )

    # Сводка статистики агента за сегодня.
    today = timezone.localdate()
    runs_today = AgentRun.objects.filter(started_at__date=today)
    agent_stats = {
        "enabled": agent_is_enabled(),
        "runs_today": runs_today.count(),
        "errors_today": runs_today.filter(status=AgentRun.STATUS_ERROR).count(),
        "spent_today": get_today_spent_usd(),
        "budget": float(getattr(settings, "AGENT_DAILY_BUDGET_USD", 5.0)),
    }

    policies = {p.action_type: p.get_mode_display() for p in AgentPolicy.objects.all()}

    from logist2.admin_site import admin_site

    context = {
        **admin_site.each_context(request),
        "title": "Дела",
        "open_tasks": open_tasks,
        "proposals": proposals,
        "questions": questions,
        "recent_actions": recent_actions,
        "digest": get_latest_digest(),
        "agent_stats": agent_stats,
        "policies": policies,
    }
    return render(request, "admin/tasks_board.html", context)


@staff_member_required
@require_POST
def agent_action_approve(request: HttpRequest, action_id: int):
    """Принять предложение агента → исполнение действия."""
    from core.services.agent.agent_executor import execute_action

    action = get_object_or_404(AgentAction, pk=action_id, status=AgentAction.STATUS_PROPOSED)
    action.approve(by=_username(request))
    try:
        result = execute_action(action, by=_username(request))
        if action.action_type == AgentAction.TYPE_CREATE_TASK:
            messages.success(request, f"Дело создано: «{result.get('title', '')}»")
        else:
            messages.success(request, f"Действие выполнено: {action.title}")
    except Exception as exc:
        logger.exception("Исполнение действия #%s упало", action.pk)
        messages.error(request, f"Действие одобрено, но исполнение упало: {exc}")
    return redirect("tasks_board")


@staff_member_required
@require_POST
def agent_action_reject(request: HttpRequest, action_id: int):
    """Отклонить предложение агента. Причина (если есть) уходит в память."""
    from core.services.agent.memory import add_rejection_memory

    action = get_object_or_404(AgentAction, pk=action_id, status=AgentAction.STATUS_PROPOSED)
    reason = (request.POST.get("reason") or "").strip()
    action.reject(by=_username(request), reason=reason)
    if reason:
        try:
            add_rejection_memory(action, reason, by=_username(request))
            messages.success(request, "Предложение отклонено, причина сохранена в память агента.")
        except Exception:
            logger.exception("Память из отклонения #%s не сохранилась", action.pk)
            messages.warning(request, "Предложение отклонено, но память не сохранилась (см. логи).")
    else:
        messages.info(request, "Предложение отклонено.")
    return redirect("tasks_board")


@staff_member_required
@require_POST
def agent_question_answer(request: HttpRequest, question_id: int):
    """Ответ владельца на вопрос агента → дистилляция в память (фоном)."""
    question = get_object_or_404(AgentQuestion, pk=question_id, status=AgentQuestion.STATUS_OPEN)
    answer = (request.POST.get("answer") or "").strip()
    if not answer:
        messages.error(request, "Пустой ответ не сохранён.")
        return redirect("tasks_board")

    question.answer = answer
    question.answered_by = _username(request)
    question.answered_at = timezone.now()
    question.status = AgentQuestion.STATUS_ANSWERED
    question.save(update_fields=["answer", "answered_by", "answered_at", "status"])

    try:
        from core.tasks_agent import distill_question_task

        distill_question_task.delay(question.pk)
    except Exception:
        # Celery недоступен (локальная разработка) — дистиллируем синхронно.
        logger.exception("distill_question_task.delay не сработал — пробуем синхронно")
        from core.tasks_agent import distill_question_task as sync_task

        sync_task(question.pk)

    messages.success(request, "Ответ сохранён — агент запомнит это правило.")
    return redirect("tasks_board")


@staff_member_required
@require_POST
def agent_question_dismiss(request: HttpRequest, question_id: int):
    question = get_object_or_404(AgentQuestion, pk=question_id, status=AgentQuestion.STATUS_OPEN)
    question.status = AgentQuestion.STATUS_DISMISSED
    question.answered_by = _username(request)
    question.answered_at = timezone.now()
    question.save(update_fields=["status", "answered_by", "answered_at"])
    messages.info(request, "Вопрос закрыт без ответа.")
    return redirect("tasks_board")


@staff_member_required
@require_POST
def task_delegate_to_agent(request: HttpRequest, task_id: int):
    """«Поручить ИИ»: запускает агент-исполнитель по делу (фаза 4)."""
    from core.services.agent.llm_client import agent_is_enabled

    task = get_object_or_404(Task, pk=task_id, is_completed=False)
    if not agent_is_enabled():
        messages.error(request, "Агент выключен (AGENT_ENABLED=False или нет ANTHROPIC_API_KEY).")
        return redirect("tasks_board")

    try:
        from core.tasks_agent import execute_task_by_agent_task

        execute_task_by_agent_task.delay(task.pk)
        messages.success(
            request,
            f"Дело «{task.title[:60]}» поручено ИИ. Результат появится в предложениях через 1-2 минуты.",
        )
    except Exception as exc:
        logger.exception("Не удалось поставить execute_task_by_agent_task")
        messages.error(request, f"Не удалось поручить дело агенту: {exc}")
    return redirect("tasks_board")


@staff_member_required
@require_POST
def task_complete_from_board(request: HttpRequest, task_id: int):
    """Закрыть дело прямо с доски."""
    task = get_object_or_404(Task, pk=task_id, is_completed=False)
    task.mark_completed(by=_username(request))
    messages.success(request, f"Дело «{task.title[:60]}» закрыто.")
    return redirect("tasks_board")
