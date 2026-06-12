"""Celery-задачи AI-агента (планировщик дел).

Регистрация в beat_schedule — в logist2/celery.py (+ autodiscover).
Все задачи no-op, пока AGENT_ENABLED=False — агент включается через env.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Лок, чтобы два beat-тика не разбирали одни письма параллельно.
_ANALYZE_LOCK_KEY = "agent_email_analysis_lock"
_ANALYZE_LOCK_TIMEOUT_SEC = 15 * 60


@shared_task(bind=True, max_retries=0, time_limit=900, soft_time_limit=840)
def analyze_new_emails_task(self) -> dict:
    """Разбор новых входящих писем агентом (каждые 10 минут)."""
    from core.services.agent.llm_client import AgentBudgetExceeded, agent_is_enabled

    if not agent_is_enabled():
        return {"status": "disabled"}

    if not cache.add(_ANALYZE_LOCK_KEY, "1", _ANALYZE_LOCK_TIMEOUT_SEC):
        logger.info("[analyze_new_emails_task] Уже идёт анализ — пропуск.")
        return {"status": "locked"}

    try:
        from core.services.agent.email_analyzer import analyze_new_emails

        report = analyze_new_emails()
        if report["processed"]:
            logger.info("[analyze_new_emails_task] %s", report)
        return {"status": "ok", **report}
    except AgentBudgetExceeded as exc:
        logger.warning("[analyze_new_emails_task] %s", exc)
        return {"status": "budget_exceeded", "detail": str(exc)}
    except Exception as exc:
        logger.exception("[analyze_new_emails_task] Unhandled error")
        return {"status": "error", "error": str(exc)[:500]}
    finally:
        cache.delete(_ANALYZE_LOCK_KEY)


@shared_task(bind=True, max_retries=0, time_limit=300, soft_time_limit=240)
def morning_digest_task(self) -> dict:
    """Утренний дайджест-план дня (будни, 07:00)."""
    from core.services.agent.llm_client import AgentBudgetExceeded, agent_is_enabled

    if not agent_is_enabled():
        return {"status": "disabled"}
    try:
        from core.services.agent.task_planner import build_morning_digest

        return {"status": "ok", **build_morning_digest()}
    except AgentBudgetExceeded as exc:
        return {"status": "budget_exceeded", "detail": str(exc)}
    except Exception as exc:
        logger.exception("[morning_digest_task] Unhandled error")
        return {"status": "error", "error": str(exc)[:500]}


@shared_task(bind=True, max_retries=0, time_limit=600, soft_time_limit=540)
def execute_task_by_agent_task(self, task_id: int) -> dict:
    """«Поручить ИИ»: агент выполняет дело tool-use циклом (фаза 4)."""
    from core.services.agent.llm_client import AgentBudgetExceeded, agent_is_enabled

    if not agent_is_enabled():
        return {"status": "disabled"}
    try:
        from core.models import Task
        from core.services.agent.agent_executor import run_task_by_agent

        task = Task.objects.filter(pk=task_id).first()
        if task is None:
            return {"status": "error", "error": f"Дело {task_id} не найдено"}
        run = run_task_by_agent(task)
        return {"status": "ok", "run_id": run.pk, "run_status": run.status}
    except AgentBudgetExceeded as exc:
        return {"status": "budget_exceeded", "detail": str(exc)}
    except Exception as exc:
        logger.exception("[execute_task_by_agent_task] Unhandled error")
        return {"status": "error", "error": str(exc)[:500]}


@shared_task(bind=True, max_retries=0, time_limit=300, soft_time_limit=240)
def distill_question_task(self, question_id: int) -> dict:
    """Дистилляция ответа владельца в память (вызывается после ответа)."""
    try:
        from core.models import AgentQuestion
        from core.services.agent.memory import distill_answer_to_memory

        question = AgentQuestion.objects.filter(pk=question_id).first()
        if question is None or not question.answer:
            return {"status": "skipped"}
        memory = distill_answer_to_memory(question)
        if memory is not None:
            question.memory = memory
            question.save(update_fields=["memory"])
        return {"status": "ok", "memory_id": memory.pk if memory else None}
    except Exception as exc:
        logger.exception("[distill_question_task] Unhandled error")
        return {"status": "error", "error": str(exc)[:500]}
