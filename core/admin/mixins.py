"""Общие миксины админки."""

from core.search_query import normalize_search_query


class NormalizeSearchMixin:
    """Снимает невидимые символы с ``q`` в поисковой строке changelist."""

    def get_search_results(self, request, queryset, search_term):
        return super().get_search_results(request, queryset, normalize_search_query(search_term))
