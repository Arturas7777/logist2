from django.contrib.admin import SimpleListFilter
from django.utils.translation import gettext_lazy as _


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
    """Фильтр клиентов с server-side autocomplete.

    Раньше (до post-M5 рефакторинга) фильтр прокачивал ВСЕХ клиентов
    в JSON прямо в HTML changelist'а — sidebar раздувался по мере
    роста справочника (200+ клиентов = ~30 KB лишнего HTML на каждой
    странице списка).

    Теперь — server-side AJAX (`/admin/clients-autocomplete/?term=...`),
    `lookups()` пустой, при наличии value дополнительно подгружаем
    имя выбранного клиента (один запрос по PK) для отображения в
    "✓ Иванов" блоке.

    Чтобы переиспользовать для других FK на Client (например
    `NewInvoice.recipient_client`), достаточно унаследовать и
    переопределить `field_name` (вычисляется `parameter_name`):

        class RecipientClientAutocompleteFilter(ClientAutocompleteFilter):
            title = "Получатель"
            field_name = "recipient_client"
    """

    title = _('Клиент')
    field_name = 'client'
    template = 'admin/client_autocomplete_filter.html'

    @property
    def parameter_name(self):  # type: ignore[override]
        return f"{self.field_name}__id__exact"

    def lookups(self, request, model_admin):
        # Возвращаем пустой список — данные подгружаются через AJAX.
        # Django требует, чтобы lookups вернул что-то truthy, иначе
        # фильтр не отрисует choices(). Возвращаем sentinel для
        # current value (если выбран), иначе пустой кортеж — choices()
        # сам отдаст единственный yield.
        return ()

    def has_output(self):  # type: ignore[override]
        # Sidebar показывается всегда (для поискового поля), независимо
        # от наличия lookups.
        return True

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(**{f"{self.field_name}_id": self.value()})
        return queryset

    def choices(self, changelist):
        current_value = self.value()
        current_text = ''
        if current_value:
            from core.models import Client
            current_text = (
                Client.objects.filter(pk=current_value).values_list('name', flat=True).first()
                or ''
            )

        yield {
            'selected': current_value is None,
            'query_string': changelist.get_query_string(remove=[self.parameter_name]),
            'display': _('Все'),
            'current_value': current_value or '',
            'current_text': current_text,
            'parameter_name': self.parameter_name,
        }


class RecipientClientAutocompleteFilter(ClientAutocompleteFilter):
    """ClientAutocompleteFilter для `NewInvoice.recipient_client`."""

    title = _('Получатель')
    field_name = 'recipient_client'
