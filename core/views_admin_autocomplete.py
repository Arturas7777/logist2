"""Server-side AJAX-endpoint'ы для кастомных list_filter в админке.

Сейчас здесь только `clients_autocomplete` — используется фильтром
`ClientAutocompleteFilter` (core/admin_filters.py). Раньше фильтр
прокачивал ВСЕХ клиентов в JSON в HTML changelist'а (sidebar
раздувался по мере роста справочника). Теперь — server-side поиск
по term, 20 результатов на запрос.

Маршрут: `/admin/clients-autocomplete/?term=...`
        (см. `logist2/urls.py`, ДО подключения `admin.site.urls`).

Доступ: только staff (защита через `staff_member_required`).
"""

from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse


@staff_member_required
def clients_autocomplete(request):
    """JSON-список клиентов по поисковому term.

    Совместимый с Select2 формат:
        {"results": [{"id": <int>, "text": <str>}, ...]}

    Лимит — 20 результатов. Без term возвращаются последние созданные
    20 клиентов (полезно при первом фокусе на пустом поле).
    """
    from core.models import Client

    term = (request.GET.get("term") or "").strip()
    qs = Client.objects.all()
    if term:
        qs = qs.filter(name__icontains=term)
    qs = qs.order_by("name")[:20]
    return JsonResponse(
        {
            "results": [{"id": c.pk, "text": c.name} for c in qs],
        }
    )
