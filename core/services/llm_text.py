"""Общий хелпер извлечения текста из ответов Anthropic API.

Новые модели (claude-sonnet-5 и далее) могут возвращать в ``response.content``
thinking-блоки ПЕРЕД текстовым блоком. Обращение ``response.content[0].text``
в этом случае падает с ``AttributeError: 'ThinkingBlock' object has no
attribute 'text'`` — поэтому текст нужно собирать только из блоков
``type == "text"``.
"""

from __future__ import annotations


def anthropic_response_text(response) -> str:
    """Склеивает все text-блоки ответа Anthropic, пропуская thinking и пр."""
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
    return "".join(parts).strip()
