"""Легковесный rate-limit декоратор для staff-only JSON API.

Используется как защита от:
- случайного дедлока UI, который шлёт 1000 req/sec на автокомплит;
- скомпрометированной сессии, выкачивающей данные массово.

Считаем запросы в скользящем окне (fixed window по секундам) через Django cache.
При превышении возвращаем 429 JSON.

Пример:

    from core.view_utils import ratelimit_staff

    @staff_member_required
    @ratelimit_staff(rate=120, per=60, scope='search_partners')
    def search_partners_api(request):
        ...
"""

from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse


def ratelimit_staff(rate: int = 120, per: int = 60, scope: str = 'default'):
    """Разрешает максимум `rate` запросов за `per` секунд на пользователя/scope."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = getattr(request, 'user', None)
            uid = getattr(user, 'pk', None) or request.META.get('REMOTE_ADDR', 'anon')

            # Фиксированное окно: ключ включает целочисленный бакет по времени.
            import time
            bucket = int(time.time()) // per
            key = f'rl:{scope}:{uid}:{bucket}'

            try:
                count = cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=per + 5)
                count = 1

            if count > rate:
                return JsonResponse(
                    {'error': 'rate limited', 'retry_after_sec': per},
                    status=429,
                )
            return view_func(request, *args, **kwargs)

        return wrapper
    return decorator
