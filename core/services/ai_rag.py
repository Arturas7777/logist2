import hashlib
import json
import logging
import os
import re
import threading
import time

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# In-process кэш RAG-индекса. Раньше query_rag_context() читал и парсил
# JSON с диска НА КАЖДЫЙ запрос (типичный размер 2-5 МБ). Теперь индекс
# держится в памяти процесса и переоткрывается только если изменился
# mtime файла. Внутри потока чтение защищено Lock'ом — несколько
# параллельных gthread-воркеров не выгребут IO одновременно.
_INDEX_CACHE: dict[str, object] = {"path": None, "mtime": None, "data": None}
_INDEX_LOCK = threading.Lock()

# TTL кэша эмбеддингов в Redis. Один и тот же запрос («где машина X?»,
# «как выставить инвойс») в течение 30 минут попадает в кэш без
# повторного похода в OpenAI Embeddings API.
_EMBEDDING_CACHE_TTL = 1800
_EMBEDDING_CACHE_PREFIX = "ai:embed:"


def _get_index_path() -> str:
    return getattr(
        settings,
        "AI_RAG_INDEX_PATH",
        os.path.join(settings.BASE_DIR, "data", "ai_rag_index.json"),
    )


def _load_index_cached(index_path: str) -> dict | None:
    """Читает индекс с диска и кэширует в памяти процесса.

    Возвращает None если файл отсутствует. Перечитывает только при
    изменении mtime файла (например, после `rebuild_ai_index`).
    """
    if not os.path.exists(index_path):
        return None
    try:
        mtime = os.path.getmtime(index_path)
    except OSError:
        return None

    cached_path = _INDEX_CACHE.get("path")
    cached_mtime = _INDEX_CACHE.get("mtime")
    cached_data = _INDEX_CACHE.get("data")
    if cached_path == index_path and cached_mtime == mtime and cached_data is not None:
        return cached_data  # type: ignore[return-value]

    with _INDEX_LOCK:
        # Double-checked: пока ждали лок, другой поток мог уже обновить.
        if (
            _INDEX_CACHE.get("path") == index_path
            and _INDEX_CACHE.get("mtime") == mtime
            and _INDEX_CACHE.get("data") is not None
        ):
            return _INDEX_CACHE["data"]  # type: ignore[return-value]
        try:
            with open(index_path, encoding="utf-8") as file_obj:
                data = json.load(file_obj)
        except (OSError, ValueError):
            logger.exception("Failed to read RAG index %s", index_path)
            return None
        _INDEX_CACHE["path"] = index_path
        _INDEX_CACHE["mtime"] = mtime
        _INDEX_CACHE["data"] = data
        return data


def _embedding_cache_key(model: str, text: str) -> str:
    """Стабильный ключ для одного embedding-запроса."""
    # usedforsecurity=False: хеш используется только как ключ кэша, не для защиты.
    digest = hashlib.sha1(f"{model}::{text}".encode(), usedforsecurity=False).hexdigest()
    return f"{_EMBEDDING_CACHE_PREFIX}{digest}"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _split_into_chunks(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    text = text or ""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, 0)
    return chunks


def _call_embeddings_api(text: str, *, use_cache: bool = True) -> list[float] | None:
    """Получить embedding для текста (с Redis-кэшем повторных запросов).

    use_cache=True — для query-режима (один и тот же вопрос в чате
    кэшируется на 30 минут). При построении индекса кэш можно отключить,
    но даже тогда Redis сильно сэкономит время на дубликатах чанков
    после повторного rebuild.
    """
    api_key = settings.AI_API_KEY
    if not api_key:
        return None
    model = getattr(settings, "AI_EMBEDDINGS_MODEL", "")
    if not model:
        return None

    text_clipped = (text or "")[:4000]
    cache_key = _embedding_cache_key(model, text_clipped) if use_cache else None
    if cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            return list(cached) if isinstance(cached, list | tuple) else cached

    base_url = settings.AI_API_BASE_URL.rstrip("/")
    url = f"{base_url}/embeddings"
    payload = {"model": model, "input": text_clipped}
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
    except requests.RequestException:
        logger.exception("Embeddings request failed")
        return None

    if not response.ok:
        logger.warning("Embeddings error: %s - %s", response.status_code, response.text[:200])
        return None

    data = response.json()
    try:
        embedding = data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Embeddings response parse failed: %s", data)
        return None

    if cache_key and embedding:
        try:
            cache.set(cache_key, embedding, _EMBEDDING_CACHE_TTL)
        except Exception:
            pass

    return embedding


def build_rag_index(
    source_paths: list[str],
    output_path: str | None = None,
    use_embeddings: bool = True,
) -> str:
    output_path = output_path or _get_index_path()
    chunks = []
    model_name = getattr(settings, "AI_EMBEDDINGS_MODEL", "") if use_embeddings else ""

    for path in source_paths:
        if not os.path.exists(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as file_obj:
                raw = file_obj.read()
        except OSError:
            logger.exception("Failed to read %s", path)
            continue

        normalized = _normalize_text(raw)
        for chunk in _split_into_chunks(normalized):
            # use_cache=True: при повторном rebuild чанки, не изменившиеся
            # с прошлого раза, не дёргают OpenAI заново.
            embedding = _call_embeddings_api(chunk, use_cache=True) if use_embeddings else None
            chunks.append(
                {
                    "source_path": path,
                    "content": chunk,
                    "embedding": embedding,
                }
            )

    index = {
        "version": 1,
        "model": model_name,
        "created_at": int(time.time()),
        "chunks": chunks,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file_obj:
        json.dump(index, file_obj, ensure_ascii=False)

    return output_path


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_score(query: str, text: str) -> float:
    terms = [t for t in re.split(r"[^\w]+", query.lower()) if len(t) > 2]
    if not terms:
        return 0.0
    text_lower = text.lower()
    return sum(1 for term in terms if term in text_lower) / len(terms)


def query_rag_context(query: str, top_k: int = 4) -> list[dict]:
    index_path = _get_index_path()
    index = _load_index_cached(index_path)
    if not index:
        return []

    chunks = index.get("chunks", [])
    if not chunks:
        return []

    query_embedding = _call_embeddings_api(query) if index.get("model") else None
    scored = []
    for chunk in chunks:
        content = chunk.get("content", "")
        embedding = chunk.get("embedding")
        if query_embedding and embedding:
            score = _cosine_similarity(query_embedding, embedding)
        else:
            score = _keyword_score(query, content)
        if score > 0:
            scored.append({**chunk, "score": score})

    scored.sort(key=lambda item: item.get("score", 0), reverse=True)
    return scored[:top_k]


def build_rag_snippets(query: str, top_k: int = 4) -> str:
    results = query_rag_context(query, top_k=top_k)
    if not results:
        return ""
    parts = []
    for item in results:
        path = item.get("source_path", "unknown")
        content = item.get("content", "")
        parts.append(f"Источник: {path}\nФрагмент: {content}")
    return "\n\n".join(parts)


def get_default_rag_sources() -> list[str]:
    base_dir = settings.BASE_DIR
    return [
        os.path.join(base_dir, "LOGIST2_PROGRESS_REPORT.md"),
        os.path.join(base_dir, "AI_PROJECT_CONTEXT.md"),
        os.path.join(base_dir, "PROMT_LOGIST2.md"),
        os.path.join(base_dir, "core", "models.py"),
        os.path.join(base_dir, "core", "admin.py"),
        os.path.join(base_dir, "core", "signals.py"),
        os.path.join(base_dir, "core", "views.py"),
        os.path.join(base_dir, "core", "models_billing.py"),
        os.path.join(base_dir, "core", "services", "ai_chat_service.py"),
    ]


def _get_mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def is_rag_index_stale(source_paths: list[str], max_age_seconds: int | None = None) -> dict[str, str]:
    index_path = _get_index_path()
    index_mtime = _get_mtime(index_path)
    if not index_mtime:
        return {"stale": "true", "reason": "index_missing"}

    for path in source_paths:
        source_mtime = _get_mtime(path)
        if source_mtime and source_mtime > index_mtime:
            return {"stale": "true", "reason": f"source_updated:{path}"}

    if max_age_seconds:
        age = time.time() - index_mtime
        if age > max_age_seconds:
            return {"stale": "true", "reason": f"older_than:{max_age_seconds}s"}

    return {"stale": "false", "reason": "fresh"}
