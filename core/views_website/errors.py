"""Обработчики ошибок сайта (клиентская часть)."""

from django.http import JsonResponse
from django.shortcuts import render

_STALE_FORM_MESSAGE = "Страница устарела. Обновите её и повторите действие."


def _wants_json(request) -> bool:
    """AJAX-запрос, которому HTML-страница ошибки только мешает."""
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    return "application/json" in (request.headers.get("accept") or "")


def csrf_failure(request, reason=""):
    """Дружелюбная страница при провале CSRF-проверки.

    Типичная причина — «протухшая» форма: страница была открыта до
    повторного входа в систему (токен ротируется на логине) или до захода
    на сайт по внешней ссылке. Пользователю достаточно вернуться назад,
    обновить страницу и отправить форму ещё раз.

    AJAX-запросам отдаём JSON: получив HTML, скрипт падал на разборе ответа
    и показывал клиенту «SyntaxError» вместо понятной причины.
    """
    if _wants_json(request):
        return JsonResponse({"ok": False, "error": _STALE_FORM_MESSAGE}, status=403)
    return render(request, "website/csrf_failure.html", status=403)
