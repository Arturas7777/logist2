"""Вечная память агента: дистилляция знаний и retrieval.

Память — записи :class:`core.models.AgentMemory`. Два пути пополнения:

* ответ владельца на :class:`core.models.AgentQuestion` → LLM дистиллирует
  суть в 1-3 предложения (:func:`distill_answer_to_memory`);
* причина отклонения предложения агента (:func:`add_rejection_memory`).

Retrieval (:func:`retrieve_memories`) — cosine-схожесть по embedding
(через существующий OpenAI-эндпоинт из ``ai_rag``), с fallback на
keyword-поиск, когда embeddings недоступны.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from core.services.ai_rag import _call_embeddings_api, _cosine_similarity, _keyword_score

logger = logging.getLogger(__name__)

DISTILL_SYSTEM_PROMPT = """Ты — модуль памяти AI-агента логистической компании.
Владелец бизнеса ответил на вопрос агента. Преврати его ответ в одно
универсальное правило, которое поможет агенту в БУДУЩИХ похожих ситуациях.

Требования к правилу:
- 1-3 предложения, на русском;
- обобщи: не «ответить Ивану про MSKU1234567», а «письма такого-то типа
  от такого-то отправителя обрабатывать так-то»;
- сохрани конкретику, которая переиспользуема (имена контрагентов, типы
  писем, пороги), убери одноразовые детали (номера конкретных контейнеров);
- если ответ владельца содержит несколько правил — выбери главное.

Верни ТОЛЬКО JSON: {"kind": "RULE|FACT|CONTACT", "content": "текст правила"}
- RULE — как действовать в ситуации;
- FACT — факт о бизнесе (процессы, сроки, ответственные);
- CONTACT — знание об отправителе/контрагенте.
"""


def embed_text(text: str) -> list[float] | None:
    """Embedding текста (или None, если AI_API_KEY не настроен)."""
    try:
        return _call_embeddings_api(text)
    except Exception:
        logger.exception("Embedding памяти не получился")
        return None


def save_memory(
    *,
    content: str,
    kind: str = "RULE",
    source: str = "MANUAL",
    created_by: str = "",
):
    """Создаёт запись памяти с embedding."""
    from core.models import AgentMemory

    return AgentMemory.objects.create(
        kind=kind if kind in {"RULE", "FACT", "CONTACT"} else "RULE",
        content=content.strip(),
        source=source,
        created_by=created_by,
        embedding=embed_text(content),
    )


def distill_answer_to_memory(question) -> object | None:
    """Дистиллирует ответ владельца на вопрос агента в запись памяти.

    Возвращает созданную AgentMemory (или None при ошибке LLM — тогда
    сохраняем ответ как есть, чтобы знание не потерялось).
    """
    from core.models import AgentMemory, AgentRun
    from core.services.agent.llm_client import AgentLLMClient

    context = question.context_json or {}
    user_prompt = (
        f"Ситуация, в которой агент задал вопрос:\n"
        f"{context.get('summary', '') or context}\n\n"
        f"Вопрос агента: {question.question}\n\n"
        f"Ответ владельца: {question.answer}"
    )

    run = AgentRun.objects.create(kind=AgentRun.KIND_MEMORY_DISTILL, input_ref=f"question:{question.pk}")
    try:
        client = AgentLLMClient(run=run)
        data = client.complete_json(
            system=DISTILL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if not data or not (data.get("content") or "").strip():
            raise ValueError(f"Дистилляция вернула пусто: {data}")
        memory = save_memory(
            content=data["content"],
            kind=data.get("kind", "RULE"),
            source=AgentMemory.SOURCE_QUESTION,
            created_by=question.answered_by,
        )
        run.finish_ok({"memory_id": memory.pk, "content": memory.content})
        return memory
    except Exception as exc:
        logger.exception("Дистилляция ответа на вопрос #%s не удалась", question.pk)
        run.finish_error(str(exc))
        # Fallback: сохраняем сырой ответ — лучше неотшлифованное знание,
        # чем потерянное.
        return save_memory(
            content=f"Вопрос: {question.question}\nОтвет владельца: {question.answer}",
            kind=AgentMemory.KIND_RULE,
            source=AgentMemory.SOURCE_QUESTION,
            created_by=question.answered_by,
        )


def add_rejection_memory(action, reason: str, by: str = "") -> object | None:
    """Отклонение предложения с причиной → обучающее правило."""
    from core.models import AgentMemory

    reason = (reason or "").strip()
    if not reason:
        return None
    content = f"Владелец отклонил предложение агента «{action.title}» (тип {action.action_type}). Причина: {reason}"
    return save_memory(
        content=content,
        kind=AgentMemory.KIND_RULE,
        source=AgentMemory.SOURCE_REJECTION,
        created_by=by,
    )


def retrieve_memories(query: str, top_k: int | None = None) -> list:
    """Top-K релевантных активных записей памяти для запроса.

    Cosine по embedding; записи без embedding (или когда embeddings
    недоступны) скорятся keyword-метрикой. Обновляет статистику
    использования (times_used / last_used_at).
    """
    from core.models import AgentMemory

    top_k = top_k or int(getattr(settings, "AGENT_MEMORY_TOP_K", 6))
    memories = list(AgentMemory.objects.filter(is_active=True))
    if not memories:
        return []

    query_embedding = embed_text(query)
    scored = []
    for memory in memories:
        if query_embedding and memory.embedding:
            score = _cosine_similarity(query_embedding, memory.embedding)
        else:
            score = _keyword_score(query, memory.content)
        if score > 0:
            scored.append((score, memory))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected = [memory for _, memory in scored[:top_k]]

    if selected:
        AgentMemory.objects.filter(pk__in=[m.pk for m in selected]).update(
            times_used=F("times_used") + 1, last_used_at=timezone.now()
        )
    return selected


def format_memories_block(memories: list) -> str:
    """Блок памяти для системного промпта."""
    if not memories:
        return ""
    lines = [f"- [{m.get_kind_display()}] {m.content}" for m in memories]
    return "ПАМЯТЬ АГЕНТА (правила и факты, накопленные от владельца):\n" + "\n".join(lines)
