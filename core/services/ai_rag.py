import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_index_path() -> str:
    return getattr(
        settings,
        "AI_RAG_INDEX_PATH",
        os.path.join(settings.BASE_DIR, "core", "ai_rag_index.json"),
    )


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _split_into_chunks(text: str, chunk_size: int = 1200, overlap: int = 200) -> List[str]:
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


def _call_embeddings_api(text: str) -> Optional[List[float]]:
    api_key = settings.AI_API_KEY
    if not api_key:
        return None
    model = getattr(settings, "AI_EMBEDDINGS_MODEL", "")
    if not model:
        return None

    base_url = settings.AI_API_BASE_URL.rstrip("/")
    url = f"{base_url}/embeddings"
    payload = {"model": model, "input": text[:4000]}
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
        return data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Embeddings response parse failed: %s", data)
        return None


def build_rag_index(
    source_paths: List[str],
    output_path: Optional[str] = None,
    use_embeddings: bool = True,
) -> str:
    output_path = output_path or _get_index_path()
    chunks = []
    model_name = getattr(settings, "AI_EMBEDDINGS_MODEL", "") if use_embeddings else ""

    for path in source_paths:
        if not os.path.exists(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as file_obj:
                raw = file_obj.read()
        except OSError:
            logger.exception("Failed to read %s", path)
            continue

        normalized = _normalize_text(raw)
        for chunk in _split_into_chunks(normalized):
            embedding = _call_embeddings_api(chunk) if use_embeddings else None
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


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
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


def query_rag_context(query: str, top_k: int = 4) -> List[Dict]:
    index_path = _get_index_path()
    if not os.path.exists(index_path):
        return []
    try:
        with open(index_path, "r", encoding="utf-8") as file_obj:
            index = json.load(file_obj)
    except OSError:
        logger.exception("Failed to read RAG index")
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


def get_default_rag_sources() -> List[str]:
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


def _get_mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def is_rag_index_stale(source_paths: List[str], max_age_seconds: Optional[int] = None) -> Dict[str, str]:
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
