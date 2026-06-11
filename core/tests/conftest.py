"""Общие фикстуры тестов core.

Очистка Django-кэша перед каждым тестом: locmem-кэш живёт на весь процесс
pytest, а БД-состояние откатывается после каждого теста. Без очистки
TTL-кэши (например, ``company:default_id``) утаскивают pk из предыдущего
теста — на PostgreSQL (sequence не откатывается) это даёт флаки вида
«direction = INTERNAL, платёж молча не создан».
"""

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
