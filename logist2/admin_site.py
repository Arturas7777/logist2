"""
Кастомный AdminSite с группировкой моделей по категориям
========================================================

Вместо одного плоского списка "Core" — 6 логических разделов:
  🚛 Логистика       — Car, Container, AutoTransport
  🤝 Партнёры        — Client, Company, Warehouse, Line, Carrier
  💰 Финансы         — NewInvoice, Transaction, ExpenseCategory
  🏦 Банкинг         — BankConnection, BankAccount, BankTransaction
  📊 Бухгалтерия     — SiteProConnection, SiteProInvoiceSync
  🌐 Сайт            — ClientUser, AIChat, NewsPost, ContactMessage,
                        TrackingRequest, NotificationLog
"""

from django.contrib.admin import AdminSite as BaseAdminSite
from collections import OrderedDict


# ── Конфигурация групп ──────────────────────────────────────────────────────
# Ключ = название группы в сайдбаре
# model_names = verbose_name_plural моделей, которые попадут в группу
# (берётся из model._meta.verbose_name_plural)

ADMIN_GROUPS = OrderedDict([
    ('🚛 Логистика', {
        'models': ['car', 'container', 'autotransport'],
        'order': 1,
    }),
    ('🤝 Партнёры', {
        'models': ['client', 'company', 'warehouse', 'line', 'carrier'],
        'order': 2,
    }),
    ('💰 Финансы', {
        'models': ['newinvoice', 'transaction', 'expensecategory'],
        'order': 3,
    }),
    ('🏦 Банкинг', {
        'models': ['bankconnection', 'bankaccount', 'banktransaction'],
        'order': 4,
    }),
    ('📊 Бухгалтерия', {
        'models': ['siteproconnection', 'siteproinvoicesync'],
        'order': 5,
    }),
    ('🌐 Сайт', {
        'models': [
            'clientuser', 'aichat', 'newspost',
            'contactmessage', 'trackingrequest', 'notificationlog',
        ],
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
