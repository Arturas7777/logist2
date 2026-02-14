"""
Кастомный AdminSite с группировкой моделей по категориям + sidebar навигация
============================================================================

Вместо одного плоского списка "Core" — 6 логических разделов:
  Логистика       — Car, Container, AutoTransport
  Партнёры        — Client, Company, Warehouse, Line, Carrier
  Финансы         — NewInvoice, Transaction, ExpenseCategory
  Банкинг         — BankConnection, BankAccount, BankTransaction
  Бухгалтерия     — SiteProConnection, SiteProInvoiceSync
  Сайт            — ClientUser, AIChat, NewsPost, ContactMessage,
                    TrackingRequest, NotificationLog
"""

from django.contrib.admin import AdminSite as BaseAdminSite
from django.urls import reverse, NoReverseMatch
from collections import OrderedDict


# ── Иконки моделей (Bootstrap Icons) ────────────────────────────────────────
MODEL_ICONS = {
    'car': 'bi-car-front-fill',
    'container': 'bi-box-seam-fill',
    'autotransport': 'bi-truck',
    'client': 'bi-people-fill',
    'company': 'bi-building',
    'warehouse': 'bi-geo-alt-fill',
    'line': 'bi-water',
    'carrier': 'bi-truck-front-fill',
    'newinvoice': 'bi-receipt',
    'transaction': 'bi-wallet2',
    'expensecategory': 'bi-tags',
    'bankconnection': 'bi-bank2',
    'bankaccount': 'bi-credit-card',
    'banktransaction': 'bi-arrow-left-right',
    'siteproconnection': 'bi-plug-fill',
    'siteproinvoicesync': 'bi-arrow-repeat',
    'clientuser': 'bi-person-badge',
    'aichat': 'bi-chat-dots-fill',
    'newspost': 'bi-newspaper',
    'contactmessage': 'bi-envelope-fill',
    'trackingrequest': 'bi-search',
    'notificationlog': 'bi-bell-fill',
    # auth models
    'user': 'bi-person-fill',
    'group': 'bi-people',
}


# ── Конфигурация групп ──────────────────────────────────────────────────────
ADMIN_GROUPS = OrderedDict([
    ('Логистика', {
        'models': ['car', 'container', 'autotransport'],
        'icon': 'bi-truck',
        'order': 1,
    }),
    ('Партнёры', {
        'models': ['client', 'company', 'warehouse', 'line', 'carrier'],
        'icon': 'bi-people-fill',
        'order': 2,
    }),
    ('Финансы', {
        'models': ['newinvoice', 'transaction', 'expensecategory'],
        'icon': 'bi-cash-stack',
        'order': 3,
    }),
    ('Банкинг', {
        'models': ['bankconnection', 'bankaccount', 'banktransaction'],
        'icon': 'bi-bank',
        'order': 4,
    }),
    ('Бухгалтерия', {
        'models': ['siteproconnection', 'siteproinvoicesync'],
        'icon': 'bi-journal-text',
        'order': 5,
    }),
    ('Сайт', {
        'models': [
            'clientuser', 'aichat', 'newspost',
            'contactmessage', 'trackingrequest', 'notificationlog',
        ],
        'icon': 'bi-globe',
        'order': 6,
    }),
])


def _build_model_to_group():
    """Строим обратный маппинг: model_name → group_name"""
    mapping = {}
    for group_name, conf in ADMIN_GROUPS.items():
        for model_name in conf['models']:
            mapping[model_name.lower()] = group_name
    return mapping


_MODEL_TO_GROUP = _build_model_to_group()


class LogistAdminSite(BaseAdminSite):
    site_header = 'Caromoto Lithuania'
    site_title = 'Caromoto Admin'
    index_title = 'Панель управления'

    # ────────────────────────────────────────────────────────────────────────
    def each_context(self, request):
        """
        Расширяем контекст каждой страницы:
        - sidebar_nav: структура навигации для sidebar
        - current_path: текущий URL для подсветки active
        """
        context = super().each_context(request)
        context['sidebar_nav'] = self._build_sidebar_nav(request)
        context['current_path'] = request.path
        return context

    # ────────────────────────────────────────────────────────────────────────
    def _build_sidebar_nav(self, request):
        """
        Формирует структуру навигации для sidebar.
        Возвращает список групп:
        [
            {
                'name': 'Логистика',
                'icon': 'bi-truck',
                'items': [
                    {'name': 'Автомобили', 'url': '/admin/core/car/', 'icon': 'bi-car-front-fill', 'active': True},
                    ...
                ],
                'is_open': True  # если есть активный item
            },
            ...
        ]
        """
        app_list = self.get_app_list(request)
        current_path = request.path
        nav = []

        for app in app_list:
            app_name = app.get('name', '')

            # Определяем иконку группы
            group_conf = ADMIN_GROUPS.get(app_name)
            group_icon = group_conf['icon'] if group_conf else 'bi-gear'

            # Специальные случаи для стандартных Django app-ов
            if app.get('app_label') == 'auth':
                group_icon = 'bi-shield-lock'
            elif app_name == '⚙️ Прочее':
                group_icon = 'bi-gear'

            items = []
            is_open = False

            for model in app.get('models', []):
                model_name = model.get('object_name', '').lower()
                model_icon = MODEL_ICONS.get(model_name, 'bi-circle')
                model_url = model.get('admin_url', '')
                is_active = current_path.startswith(model_url) if model_url else False

                if is_active:
                    is_open = True

                items.append({
                    'name': model.get('name', ''),
                    'url': model_url,
                    'icon': model_icon,
                    'active': is_active,
                    'add_url': model.get('add_url', ''),
                    'view_only': not model.get('add_url'),
                })

            if items:
                nav.append({
                    'name': app_name,
                    'icon': group_icon,
                    'items': items,
                    'is_open': is_open,
                })

        return nav

    # ────────────────────────────────────────────────────────────────────────
    def get_app_list(self, request, app_label=None):
        """
        Переопределяем стандартный get_app_list:
        1. Получаем оригинальный список приложений от Django
        2. Модели из 'core' разбиваем на логические группы
        3. Всё остальное (auth, и т.д.) оставляем как есть
        """
        original = super().get_app_list(request, app_label=app_label)

        # Разделяем: core-модели отдельно, остальные app-ы как есть
        core_models = []
        other_apps = []

        for app in original:
            if app['app_label'] == 'core':
                core_models.extend(app.get('models', []))
            else:
                other_apps.append(app)

        if not core_models:
            return other_apps

        # Раскидываем core-модели по группам
        groups = OrderedDict()
        ungrouped = []

        for model_entry in core_models:
            model_name = model_entry['object_name'].lower()
            group_name = _MODEL_TO_GROUP.get(model_name)

            if group_name:
                groups.setdefault(group_name, []).append(model_entry)
            else:
                ungrouped.append(model_entry)

        # Формируем финальный app_list
        result = []

        for group_name, conf in ADMIN_GROUPS.items():
            models_in_group = groups.get(group_name, [])
            if not models_in_group:
                continue

            # Сортируем модели внутри группы в порядке, указанном в ADMIN_GROUPS
            model_order = {m.lower(): i for i, m in enumerate(conf['models'])}
            models_in_group.sort(
                key=lambda m: model_order.get(m['object_name'].lower(), 999)
            )

            result.append({
                'name': group_name,
                'app_label': 'core',  # все ссылки ведут к core
                'app_url': '/admin/core/',
                'has_module_perms': True,
                'models': models_in_group,
            })

        # Если остались модели, не попавшие ни в одну группу
        if ungrouped:
            result.append({
                'name': '⚙️ Прочее',
                'app_label': 'core',
                'app_url': '/admin/core/',
                'has_module_perms': True,
                'models': ungrouped,
            })

        # Другие приложения (auth) — в конец
        result.extend(other_apps)

        return result


# ── Глобальный экземпляр ────────────────────────────────────────────────────
admin_site = LogistAdminSite(name='admin')
