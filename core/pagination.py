"""
Система пагинации для больших списков
"""

from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils.functional import cached_property
import logging

logger = logging.getLogger(__name__)

class OptimizedPaginator(Paginator):
    """Оптимизированный пагинатор с улучшенной производительностью"""
    
    def __init__(self, object_list, per_page, orphans=0, allow_empty_first_page=True):
        super().__init__(object_list, per_page, orphans, allow_empty_first_page)
        self._count = None
    
    @cached_property
    def count(self):
        """Кэшированный подсчет объектов"""
        if self._count is None:
            try:
                # Используем count() для оптимизации
                if hasattr(self.object_list, 'count'):
                    self._count = self.object_list.count()
                else:
                    self._count = len(self.object_list)
            except (TypeError, ValueError):
                self._count = len(self.object_list)
        return self._count
    
    def page(self, number):
        """Возвращает страницу с оптимизацией"""
        try:
            return super().page(number)
        except (PageNotAnInteger, EmptyPage):
            return super().page(1)

class PaginationMixin:
    """Миксин для добавления пагинации к представлениям"""
    
    paginate_by = 25
    paginate_orphans = 5
    max_paginate_by = 100
    
    def get_paginate_by(self, queryset):
        """Определяет количество элементов на странице"""
        if 'paginate_by' in self.request.GET:
            try:
                paginate_by = int(self.request.GET['paginate_by'])
                if paginate_by > 0:
                    return min(paginate_by, self.max_paginate_by)
            except (ValueError, TypeError):
                pass
        return self.paginate_by
    
    def paginate_queryset(self, queryset, page_size=None):
        """Пагинирует queryset"""
        if page_size is None:
            page_size = self.get_paginate_by(queryset)
        
        paginator = OptimizedPaginator(queryset, page_size, self.paginate_orphans)
        page_number = self.request.GET.get('page')
        
        try:
            page = paginator.page(page_number)
        except (PageNotAnInteger, EmptyPage):
            page = paginator.page(1)
        
        return page, paginator

def paginate_queryset(queryset, request, per_page=25, orphans=5, max_per_page=100):
    """
    Универсальная функция для пагинации queryset
    
    Args:
        queryset: QuerySet для пагинации
        request: HTTP запрос
        per_page: Количество элементов на странице по умолчанию
        orphans: Минимальное количество элементов на последней странице
        max_per_page: Максимальное количество элементов на странице
    
    Returns:
        tuple: (page, paginator)
    """
    # Определяем количество элементов на странице
    if 'per_page' in request.GET:
        try:
            per_page = int(request.GET['per_page'])
            per_page = min(per_page, max_per_page)
        except (ValueError, TypeError):
            pass
    
    paginator = OptimizedPaginator(queryset, per_page, orphans)
    page_number = request.GET.get('page')
    
    try:
        page = paginator.page(page_number)
    except (PageNotAnInteger, EmptyPage):
        page = paginator.page(1)
    
    return page, paginator

def render_paginated_response(request, queryset, template_name, context=None, per_page=25):
    """
    Рендерит пагинированный ответ
    
    Args:
        request: HTTP запрос
        queryset: QuerySet для пагинации
        template_name: Имя шаблона
        context: Дополнительный контекст
        per_page: Количество элементов на странице
    
    Returns:
        HttpResponse: Пагинированный ответ
    """
    if context is None:
        context = {}
    
    page, paginator = paginate_queryset(queryset, request, per_page)
    
    context.update({
        'page': page,
        'paginator': paginator,
        'is_paginated': page.has_other_pages(),
        'page_obj': page,
    })
    
    return render_to_string(template_name, context, request)

def get_pagination_info(page, paginator):
    """
    Возвращает информацию о пагинации для API
    
    Args:
        page: Объект страницы
        paginator: Объект пагинатора
    
    Returns:
        dict: Информация о пагинации
    """
    return {
        'count': paginator.count,
        'num_pages': paginator.num_pages,
        'current_page': page.number,
        'has_next': page.has_next(),
        'has_previous': page.has_previous(),
        'next_page': page.next_page_number() if page.has_next() else None,
        'previous_page': page.previous_page_number() if page.has_previous() else None,
        'start_index': page.start_index(),
        'end_index': page.end_index(),
        'per_page': paginator.per_page,
    }

def paginated_json_response(page, paginator, data_key='results'):
    """
    Возвращает JSON ответ с пагинированными данными
    
    Args:
        page: Объект страницы
        paginator: Объект пагинатора
        data_key: Ключ для данных в JSON ответе
    
    Returns:
        JsonResponse: JSON ответ с пагинацией
    """
    response_data = {
        data_key: list(page.object_list),
        'pagination': get_pagination_info(page, paginator)
    }
    
    return JsonResponse(response_data, safe=False)

class PaginationHelper:
    """Вспомогательный класс для работы с пагинацией"""
    
    def __init__(self, request, queryset, per_page=25):
        self.request = request
        self.queryset = queryset
        self.per_page = per_page
        self._page = None
        self._paginator = None
    
    @property
    def page(self):
        """Получает текущую страницу"""
        if self._page is None:
            self._page, self._paginator = paginate_queryset(
                self.queryset, self.request, self.per_page
            )
        return self._page
    
    @property
    def paginator(self):
        """Получает пагинатор"""
        if self._paginator is None:
            self._page, self._paginator = paginate_queryset(
                self.queryset, self.request, self.per_page
            )
        return self._paginator
    
    def get_context_data(self, **kwargs):
        """Возвращает контекстные данные для шаблона"""
        context = {
            'page': self.page,
            'paginator': self.paginator,
            'is_paginated': self.page.has_other_pages(),
            'page_obj': self.page,
        }
        context.update(kwargs)
        return context
    
    def get_json_data(self, data_key='results'):
        """Возвращает данные в формате JSON"""
        return paginated_json_response(self.page, self.paginator, data_key)
    
    def get_page_range(self, window=5):
        """
        Возвращает диапазон страниц для отображения
        
        Args:
            window: Количество страниц по бокам от текущей
        
        Returns:
            list: Список номеров страниц
        """
        current = self.page.number
        total = self.paginator.num_pages
        
        start = max(1, current - window)
        end = min(total, current + window)
        
        return list(range(start, end + 1))

# Утилиты для админ-интерфейса
def get_admin_pagination_context(request, queryset, per_page=25):
    """
    Возвращает контекст пагинации для админ-интерфейса
    
    Args:
        request: HTTP запрос
        queryset: QuerySet для пагинации
        per_page: Количество элементов на странице
    
    Returns:
        dict: Контекст для админ-шаблона
    """
    helper = PaginationHelper(request, queryset, per_page)
    return helper.get_context_data()

def render_admin_paginated_table(request, queryset, template_name, context=None, per_page=25):
    """
    Рендерит пагинированную таблицу для админ-интерфейса
    
    Args:
        request: HTTP запрос
        queryset: QuerySet для пагинации
        template_name: Имя шаблона
        context: Дополнительный контекст
        per_page: Количество элементов на странице
    
    Returns:
        str: HTML код пагинированной таблицы
    """
    if context is None:
        context = {}
    
    pagination_context = get_admin_pagination_context(request, queryset, per_page)
    context.update(pagination_context)
    
    return render_to_string(template_name, context, request)
