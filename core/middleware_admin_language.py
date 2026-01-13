"""
Middleware для принудительной установки русского языка в Django Admin
"""
from django.utils import translation


class AdminRussianLanguageMiddleware:
    """
    Middleware, который устанавливает русский язык для всех страниц админки
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Если это админка, принудительно устанавливаем русский язык
        if request.path.startswith('/admin'):
            translation.activate('ru')
            request.LANGUAGE_CODE = 'ru'
        
        response = self.get_response(request)
        return response

