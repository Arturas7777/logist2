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


@pytest.fixture(autouse=True)
def _offline_nhtsa(monkeypatch):
    """Тесты не ходят в NHTSA: результат имитирует недоступность API.

    Без этого каждый VIN в проверке ждал бы сетевого таймаута, а результат
    зависел бы от внешнего сервиса. Тестам, которым нужна расшифровка VIN,
    достаточно подменить эту функцию своим ответом.
    """

    def _unavailable(vin, *, timeout=5):
        return {
            "ok": False,
            "make": None,
            "model": None,
            "year": None,
            "error_code": "",
            "error_text": "",
            "suggested_vin": "",
            "raw_failed": True,
        }

    monkeypatch.setattr("core.services.vin_validator.decode_vin_nhtsa", _unavailable)
    monkeypatch.setattr("core.services.vin_gate.decode_vin_nhtsa", _unavailable)
    yield
