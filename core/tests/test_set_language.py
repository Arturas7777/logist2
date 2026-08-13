"""Смена языка на сайте (GET, без CSRF)."""

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

LANGUAGE_COOKIE = settings.LANGUAGE_COOKIE_NAME


def test_set_language_sets_cookie_and_redirects(client):
    url = reverse("website:set_language")
    response = client.get(url, {"language": "en", "next": "/about/"})
    assert response.status_code == 302
    assert response.url == "/about/"
    assert response.cookies[LANGUAGE_COOKIE].value == "en"


def test_set_language_rejects_open_redirect(client):
    url = reverse("website:set_language")
    response = client.get(url, {"language": "lt", "next": "https://evil.example/"})
    assert response.status_code == 302
    assert response.url == "/"
    assert response.cookies[LANGUAGE_COOKIE].value == "lt"


def test_set_language_invalid_code_does_not_set_cookie(client):
    url = reverse("website:set_language")
    response = client.get(url, {"language": "xx", "next": "/about/"})
    assert response.status_code == 302
    assert response.url == "/about/"
    assert LANGUAGE_COOKIE not in response.cookies


def test_language_switcher_is_get_link(client):
    response = client.get(reverse("website:home"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "/i18n/setlang/" not in html
    assert reverse("website:set_language") in html
    assert "language=ru" in html
