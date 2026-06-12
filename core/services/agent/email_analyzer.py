"""Агент-аналитик почты (фаза 1).

Для каждого нового входящего письма агент определяет:

* кто пишет (роль отправителя, связь с клиентами в базе);
* зачем (намерение письма);
* к каким контейнерам/авто относится (поверх rule-based матчера);
* что делать: предложить дело (CREATE_TASK), задать вопрос владельцу
  (ASK_QUESTION) или ничего (NOTHING).

Один LLM-вызов на письмо со структурированным JSON-ответом — без
tool-use, дёшево. Результат проходит через
:func:`core.services.agent.agent_executor.propose_action` (журнал +
политика автономии).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM_TEMPLATE = """Ты — AI-ассистент владельца логистической компании Caromoto Lithuania.
Твоя задача — разобрать входящее письмо и решить, требует ли оно действий.

{context}

Проанализируй письмо и верни ТОЛЬКО валидный JSON:
{{
  "sender_role": "клиент|линия|порт_склад|брокер|поставщик|спам_рассылка|другое",
  "intent": "краткое намерение письма по-русски (1 предложение)",
  "related": "к каким контейнерам/авто/клиентам относится, по-русски",
  "action": "CREATE_TASK|ASK_QUESTION|NOTHING",
  "task": {{
    "title": "название дела (до 150 символов, по-русски)",
    "description": "что нужно сделать и почему (по-русски)",
    "priority": "LOW|MEDIUM|HIGH",
    "deadline_days": null
  }},
  "question": "вопрос владельцу, если action=ASK_QUESTION",
  "confidence": 0.0,
  "reasoning": "короткое обоснование решения по-русски"
}}

Правила выбора action:
- CREATE_TASK — письмо требует конкретного действия от сотрудников
  (см. бизнес-контекст: что обычно требует дела).
- Если приложена предыдущая переписка треда — используй её, чтобы понять,
  что уже обсуждено и сделано. Если последнее слово за нами (клиент ждёт
  ответа или подтверждения) — предложи дело «Ответить ...» с кратким
  планом ответа в description. Если мы уже ответили и новых вопросов
  в письме нет — NOTHING.
- NOTHING — автоматические уведомления, рассылки, спам, письма без
  требуемых действий, письма, на которые уже ответили.
- ASK_QUESTION — нестандартная ситуация: непонятно, нужно ли действие
  или как его выполнять, И в памяти агента нет подходящего правила.
  Сформулируй вопрос так, чтобы ответ стал переиспользуемым правилом.
- Если похожая ситуация описана в ПАМЯТИ АГЕНТА — следуй правилу из
  памяти, а не задавай вопрос повторно.
- deadline_days: целое число дней на выполнение, если из письма следует
  срочность (прибытие, дедлайн оплаты), иначе null.
- confidence: 0..1 — уверенность в решении.
"""


def analyze_email(email) -> dict:
    """Анализирует одно письмо; создаёт AgentAction/AgentQuestion.

    Возвращает словарь-итог для журнала. Письмо помечается
    ``agent_analyzed_at`` в любом случае (включая ошибку LLM — чтобы
    битое письмо не зацикливало очередь; ошибка видна в AgentRun).
    Исключение — исчерпание дневного бюджета: такое письмо не помечается
    и будет разобрано, когда бюджет восстановится.
    """
    from core.models import AgentAction, AgentRun
    from core.services.agent.agent_executor import propose_action
    from core.services.agent.context_builder import (
        build_system_context,
        describe_email,
        describe_thread_context,
    )
    from core.services.agent.llm_client import AgentBudgetExceeded, AgentLLMClient

    run = AgentRun.objects.create(kind=AgentRun.KIND_EMAIL_ANALYSIS, input_ref=f"email:{email.pk}")
    outcome: dict = {"email_id": email.pk, "action": "ERROR"}
    mark_analyzed = True

    try:
        email_text = describe_email(email)
        thread_block = describe_thread_context(email)
        if thread_block:
            email_text = f"{thread_block}\n\n=== НОВОЕ ПИСЬМО (анализируй его) ===\n{email_text}"
        retrieval_query = f"{email.from_addr} {email.subject} {(email.body_text or email.snippet or '')[:500]}"
        system = ANALYSIS_SYSTEM_TEMPLATE.format(context=build_system_context(retrieval_query))

        client = AgentLLMClient(run=run)
        data = client.complete_json(
            system=system,
            messages=[{"role": "user", "content": email_text}],
        )
        if not data or data.get("action") not in {"CREATE_TASK", "ASK_QUESTION", "NOTHING"}:
            raise ValueError(f"Невалидный ответ анализатора: {str(data)[:300]}")

        outcome = {
            "email_id": email.pk,
            "action": data["action"],
            "sender_role": data.get("sender_role", ""),
            "intent": data.get("intent", ""),
            "confidence": data.get("confidence"),
        }

        if data["action"] == "CREATE_TASK":
            task_data = data.get("task") or {}
            container = email.containers.first()
            car = email.cars.first()
            action = propose_action(
                action_type=AgentAction.TYPE_CREATE_TASK,
                title=(task_data.get("title") or email.subject or "Дело из письма")[:300],
                payload={
                    "title": task_data.get("title", ""),
                    "description": _build_task_description(data, email),
                    "priority": task_data.get("priority", "MEDIUM"),
                    "deadline_days": task_data.get("deadline_days"),
                    "container_id": container.pk if container else None,
                    "car_id": car.pk if car else None,
                },
                risk_level=AgentAction.RISK_LOW,
                run=run,
                source_email=email,
                reasoning=data.get("reasoning", ""),
                confidence=_safe_float(data.get("confidence")),
            )
            outcome["action_id"] = action.pk if action else None
        elif data["action"] == "ASK_QUESTION":
            from core.models import AgentQuestion

            question = AgentQuestion.objects.create(
                question=(data.get("question") or "").strip() or f"Как обработать письмо «{email.subject[:100]}»?",
                run=run,
                source_email=email,
                context_json={
                    "summary": (
                        f"Письмо от {email.from_addr[:100]}, тема «{email.subject[:150]}». "
                        f"Отправитель: {data.get('sender_role', '')}. "
                        f"Намерение: {data.get('intent', '')}. "
                        f"Относится к: {data.get('related', '')}"
                    ),
                    "email_id": email.pk,
                },
            )
            outcome["question_id"] = question.pk

        run.finish_ok(outcome)
    except AgentBudgetExceeded as exc:
        # Не помечаем письмо — оно будет разобрано после восстановления бюджета.
        mark_analyzed = False
        run.finish_error(str(exc))
        outcome["error"] = str(exc)[:300]
        outcome["budget_exceeded"] = True
    except Exception as exc:
        logger.exception("Анализ письма #%s упал", email.pk)
        run.finish_error(str(exc))
        outcome["error"] = str(exc)[:300]
    finally:
        if mark_analyzed:
            email.agent_analyzed_at = timezone.now()
            email.save(update_fields=["agent_analyzed_at"])

    return outcome


def _build_task_description(data: dict, email) -> str:
    task_data = data.get("task") or {}
    parts = [task_data.get("description", "").strip()]
    parts.append(
        f"\n— Создано ИИ из письма от {email.from_addr[:150]} "
        f"(тема: «{email.subject[:150]}», получено {email.received_at:%d.%m.%Y %H:%M})."
    )
    if data.get("intent"):
        parts.append(f"Суть письма: {data['intent']}")
    return "\n".join(p for p in parts if p)


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze_new_emails(limit: int | None = None) -> dict:
    """Разбирает накопившиеся новые входящие письма (вызывается из Celery).

    Берёт INCOMING-письма без ``agent_analyzed_at`` за последние 14 дней
    (хвост истории не трогаем) — не больше ``AGENT_MAX_EMAILS_PER_RUN``
    за один запуск. Настройка ``AGENT_ANALYZE_SINCE`` задаёт жёсткую
    нижнюю границу: письма старше неё не анализируются никогда.
    """
    from datetime import date, datetime, time

    from core.models import ContainerEmail

    limit = limit or int(getattr(settings, "AGENT_MAX_EMAILS_PER_RUN", 20))
    since = timezone.now() - timezone.timedelta(days=14)
    floor_raw = getattr(settings, "AGENT_ANALYZE_SINCE", "")
    if floor_raw:
        try:
            floor = timezone.make_aware(datetime.combine(date.fromisoformat(floor_raw), time.min))
            since = max(since, floor)
        except ValueError:
            logger.warning("AGENT_ANALYZE_SINCE=%r не похоже на ISO-дату — игнорирую", floor_raw)

    emails = list(
        ContainerEmail.objects.filter(
            direction=ContainerEmail.DIRECTION_INCOMING,
            agent_analyzed_at__isnull=True,
            received_at__gte=since,
            # Контентные дубли рассылок и отфильтрованные письма не анализируем —
            # они скрыты и из карточек (см. email_ingest._ingest_one).
            hidden_reason="",
        ).order_by("received_at")[:limit]
    )

    results = {"processed": 0, "tasks_proposed": 0, "questions": 0, "nothing": 0, "errors": 0}
    for email in emails:
        outcome = analyze_email(email)
        if outcome.get("budget_exceeded"):
            # Бюджет кончился — остальные письма дёргать бессмысленно,
            # они останутся в очереди до восстановления бюджета.
            results["budget_exceeded"] = True
            break
        results["processed"] += 1
        action = outcome.get("action")
        if action == "CREATE_TASK":
            results["tasks_proposed"] += 1
        elif action == "ASK_QUESTION":
            results["questions"] += 1
        elif action == "NOTHING":
            results["nothing"] += 1
        else:
            results["errors"] += 1
    return results
