"""
HTTP-моки для контрактных тестов интеграций (T1, AUDIT_ROUND3).

`FakeSession` подменяет `requests.Session` внутри сервиса: маршруты
регистрируются по (метод, подстрока URL), ответы — `FakeResponse` с
записанными JSON-фикстурами из `core/tests/fixtures/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    """Загрузить записанный JSON-ответ API из core/tests/fixtures/."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class FakeResponse:
    """Минимальный аналог requests.Response для контрактных тестов."""

    def __init__(self, json_data=None, status_code: int = 200,
                 content: bytes = b"", headers: dict | None = None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}
        if content:
            self.content = content
        elif json_data is not None:
            self.content = json.dumps(json_data).encode("utf-8")
        else:
            self.content = b""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        if self._json is None:
            raise ValueError("No JSON body")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Error", response=self,
            )


class FakeSession:
    """Подменный requests.Session с маршрутизацией по подстроке URL.

    Ответ может быть FakeResponse или список FakeResponse (выдаются по
    очереди; последний повторяется). Все вызовы записываются в `self.calls`
    как (method, url, kwargs) — по ним проверяется контракт запросов.
    """

    def __init__(self):
        self.routes: list[list] = []  # [method, substring, response(s)]
        self.calls: list[tuple] = []
        self.headers: dict = {}

    def add(self, method: str, url_substring: str, response):
        self.routes.append([method.upper(), url_substring, response])
        return self

    def _dispatch(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        for m, sub, resp in self.routes:
            if m == method and sub in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        raise AssertionError(f"Незамоканный запрос: {method} {url}")

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, **kwargs)

    def calls_to(self, url_substring: str) -> list[tuple]:
        return [c for c in self.calls if url_substring in c[1]]

    def posted_json(self, url_substring: str) -> list[dict]:
        """Распарсенные JSON-тела POST-запросов к эндпоинту."""
        bodies = []
        for method, _url, kwargs in self.calls_to(url_substring):
            if method != "POST":
                continue
            raw = kwargs.get("data") or kwargs.get("json")
            if isinstance(raw, str | bytes):
                bodies.append(json.loads(raw))
            elif isinstance(raw, dict):
                bodies.append(raw)
        return bodies
