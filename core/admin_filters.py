from django.contrib.admin import SimpleListFilter
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from django.urls import reverse
import json


class MultiStatusFilter(SimpleListFilter):
    """Кастомный фильтр для множественного выбора статусов с чекбоксами"""
    title = _('Статус')
    parameter_name = 'status_multi'
    template = 'admin/multi_status_filter.html'

    def lookups(self, request, model_admin):
        """Возвращает список доступных статусов"""
        # Получаем все возможные статусы из модели
        status_choices = None
        
        # Проверяем, есть ли STATUS_CHOICES в самой модели
        if hasattr(model_admin.model, 'STATUS_CHOICES'):
            status_choices = model_admin.model.STATUS_CHOICES
        # Если нет, проверяем Container.STATUS_CHOICES (для модели Car)
        elif hasattr(model_admin.model, 'Container') and hasattr(model_admin.model.Container, 'STATUS_CHOICES'):
            status_choices = model_admin.model.Container.STATUS_CHOICES
        # Если и это не работает, импортируем Container напрямую
        else:
            try:
                from core.models import Container
                if hasattr(Container, 'STATUS_CHOICES'):
                    status_choices = Container.STATUS_CHOICES
            except ImportError:
                pass
        
        if status_choices:
            choices = []
            for status_code, status_name in status_choices:
                choices.append((status_code, status_name))
            return choices
        
        # Если ничего не найдено, получаем из данных
        statuses = model_admin.model.objects.values_list('status', flat=True).distinct().order_by('status')
        choices = []
        for status in statuses:
            choices.append((status, status))
        return choices

    def queryset(self, request, queryset):
        """Фильтрует queryset на основе выбранных статусов"""
        # Получаем выбранные статусы из параметров запроса
        selected_statuses = request.GET.getlist(self.parameter_name)
        if selected_statuses:
            return queryset.filter(status__in=selected_statuses)
        return queryset

    def choices(self, changelist):
        """Возвращает список вариантов для отображения в фильтре"""
        # Получаем текущие выбранные значения из request
        request = getattr(changelist, 'request', None)
        if not request:
            # Если нет request, используем пустой список
            current_selections = []
        else:
            current_selections = request.GET.getlist(self.parameter_name)
        
        for lookup, title in self.lookup_choices:
            yield {
                'selected': lookup in current_selections,
                'query_string': changelist.get_query_string({
                    self.parameter_name: lookup
                }, []),
                'display': title,
                'value': lookup,
            }


class MultiWarehouseFilter(SimpleListFilter):
    """Кастомный фильтр для множественного выбора складов с чекбоксами"""
    title = _('Склад')
    parameter_name = 'warehouse_multi'
    template = 'admin/multi_warehouse_filter.html'

    def lookups(self, request, model_admin):
        """Возвращает список доступных складов"""
        from core.models import Warehouse
        return list(Warehouse.objects.values_list('id', 'name').order_by('name'))

    def queryset(self, request, queryset):
        """Фильтрует queryset на основе выбранных складов"""
        if self.value():
            selected_warehouses = request.GET.getlist(self.parameter_name)
            if selected_warehouses:
                return queryset.filter(warehouse_id__in=selected_warehouses)
        return queryset

    def choices(self, changelist):
        """Возвращает список вариантов для отображения в фильтре"""
        # Получаем текущие выбранные значения из request
        request = getattr(changelist, 'request', None)
        if not request:
            # Если нет request, используем пустой список
            current_selections = []
        else:
            current_selections = request.GET.getlist(self.parameter_name)
        
        for lookup, title in self.lookup_choices:
            yield {
                'selected': lookup in current_selections,
                'query_string': changelist.get_query_string({
                    self.parameter_name: lookup
                }, []),
                'display': title,
                'value': lookup,
            }


class ClientAutocompleteFilter(SimpleListFilter):
    """Фильтр клиентов с автодополнением (поиск по имени)"""
    title = _('Клиент')
    parameter_name = 'client__id__exact'
    template = 'admin/client_autocomplete_filter.html'

    def lookups(self, request, model_admin):
        from core.models import Client
        return Client.objects.values_list('id', 'name').order_by('name')

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(client_id=self.value())
        return queryset

    def choices(self, changelist):
        clients_data = [{'id': str(pk), 'text': name} for pk, name in self.lookup_choices]
        
        current_value = self.value()
        current_text = ''
        if current_value:
            lookup = {str(pk): name for pk, name in self.lookup_choices}
            current_text = lookup.get(str(current_value), '')
        
        yield {
            'selected': current_value is None,
            'query_string': changelist.get_query_string(remove=[self.parameter_name]),
            'display': _('Все'),
            'clients_json': json.dumps(clients_data),
            'current_value': current_value or '',
            'current_text': current_text,
            'parameter_name': self.parameter_name,
        }
