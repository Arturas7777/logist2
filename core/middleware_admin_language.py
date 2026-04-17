"""
Middleware для принудительной установки русского языка в Django Admin
"""
from django.utils import translation


class AdminRussianLanguageMiddleware:
    """
    Middleware, который устанавливает русский язык для всех страниц админки
    и восстанавливает предыдущий после обработки запроса.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        prev_lang = translation.get_language()
        if request.path.startswith('/admin'):
            translation.activate('ru')
            request.LANGUAGE_CODE = 'ru'

        response = self.get_response(request)

        if request.path.startswith('/admin') and prev_lang:
            translation.activate(prev_lang)

        return response
