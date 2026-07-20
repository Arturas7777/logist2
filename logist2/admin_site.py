"""
Кастомный AdminSite: sidebar-навигация по частоте использования
================================================================

Меню строится из ADMIN_GROUPS — единого конфига, где группа может содержать
и модели (строка = object_name в нижнем регистре), и ссылки на кастомные
страницы (dict с name/url/icon).

Принцип: ежедневная работа — наверху и развёрнута; справочники/настройки
и служебные разделы — свёрнуты по умолчанию (collapsed=True).

Модели из HIDDEN_MODELS зарегистрированы в админке (доступны по прямым URL
и из карточек), но в меню не показываются.
"""

from collections import OrderedDict

from django.contrib.admin import AdminSite as BaseAdminSite
from django.shortcuts import redirect

# ── Иконки моделей (Bootstrap Icons) ────────────────────────────────────────
MODEL_ICONS = {
    "car": "bi-car-front-fill",
    "container": "bi-box-seam-fill",
    "autotransport": "bi-truck",
    "client": "bi-people-fill",
    "company": "bi-building",
    "warehouse": "bi-geo-alt-fill",
    "line": "bi-water",
    "carrier": "bi-truck-front-fill",
    "newinvoice": "bi-receipt",
    "transaction": "bi-wallet2",
    "expensecategory": "bi-tags",
    "personalcard": "bi-credit-card-2-front",
    "personaltransfer": "bi-arrow-left-right",
    "bankconnection": "bi-bank2",
    "bankaccount": "bi-credit-card",
    "banktransaction": "bi-arrow-left-right",
    "siteproconnection": "bi-plug-fill",
    "siteproinvoicesync": "bi-arrow-repeat",
    "clientuser": "bi-person-badge",
    "aichat": "bi-chat-dots-fill",
    "newspost": "bi-newspaper",
    "contactmessage": "bi-envelope-fill",
    "trackingrequest": "bi-search",
    "notificationlog": "bi-bell-fill",
    "task": "bi-check2-square",
    "agentquestion": "bi-question-circle",
    "agentaction": "bi-lightning-charge",
    "agentrun": "bi-play-circle",
    "agentmemory": "bi-bookmark-star",
    "agentpolicy": "bi-sliders",
    "scanprocessingjob": "bi-file-earmark-image",
    "carmodelimage": "bi-image",
    "emailgroup": "bi-collection",
    "emailingestfilter": "bi-funnel",
    "gmailsyncstate": "bi-envelope-arrow-down",
    "contact": "bi-person-lines-fill",
    "containeremail": "bi-envelope",
    "user": "bi-person-fill",
    "group": "bi-people",
}


# ── Конфигурация меню ────────────────────────────────────────────────────────
# Элемент группы:
#   • строка  — object_name модели в нижнем регистре;
#   • dict    — ссылка на кастомную страницу: {"name", "url", "icon"};
#     опционально "match" — префикс URL для подсветки active (по умолчанию url).
ADMIN_GROUPS = OrderedDict(
    [
        (
            "Логистика",
            {
                "icon": "bi-truck",
                "collapsed": False,
                "items": [
                    "car",
                    "container",
                    "autotransport",
                    "containeremail",
                    {
                        "name": "Печать наклеек",
                        "url": "/admin/labels/print/",
                        "icon": "bi-printer",
                        "match": "/admin/labels/",
                    },
                ],
            },
        ),
        (
            "Финансы",
            {
                "icon": "bi-cash-stack",
                "collapsed": False,
                "items": [
                    "newinvoice",
                    "transaction",
                    "banktransaction",
                    {"name": "Сверка счетов", "url": "/admin/reconciliation/", "icon": "bi-graph-up-arrow"},
                    {"name": "Проверка счетов", "url": "/admin/invoice-audit/", "icon": "bi-shield-check"},
                    {"name": "Касса: расход", "url": "/admin/cash-expense/", "icon": "bi-dash-circle"},
                    {"name": "Касса: приход", "url": "/admin/cash-income/", "icon": "bi-plus-circle"},
                    {"name": "Личные карты", "url": "/admin/personal-cards/", "icon": "bi-credit-card-2-front"},
                    {"name": "Аналитика расходов", "url": "/admin/expense-analytics/", "icon": "bi-pie-chart-fill"},
                    {"name": "Сравнение сумм", "url": "/admin/comparison/", "icon": "bi-bar-chart-line"},
                ],
            },
        ),
        (
            "Партнёры",
            {
                "icon": "bi-people-fill",
                "collapsed": False,
                "items": ["client", "company", "warehouse", "line", "carrier", "contact"],
            },
        ),
        (
            "Дела + ИИ",
            {
                "icon": "bi-robot",
                "collapsed": False,
                "items": [
                    {"name": "Доска дел", "url": "/admin/tasks-board/", "icon": "bi-kanban"},
                    "scanprocessingjob",
                    "agentquestion",
                ],
            },
        ),
        (
            "Сайт",
            {
                "icon": "bi-globe",
                "collapsed": True,
                "items": ["clientuser", "newspost", "contactmessage", "trackingrequest", "aichat"],
            },
        ),
        (
            "Настройки",
            {
                "icon": "bi-gear",
                "collapsed": True,
                "items": [
                    "expensecategory",
                    "bankconnection",
                    "bankaccount",
                    "siteproconnection",
                    "siteproinvoicesync",
                    "emailgroup",
                    "emailingestfilter",
                    "carmodelimage",
                    "agentpolicy",
                    "user",
                    "group",
                ],
            },
        ),
        (
            "Система",
            {
                "icon": "bi-cpu",
                "collapsed": True,
                "items": [
                    {"name": "Мониторинг системы", "url": "/admin/system-monitor/", "icon": "bi-activity"},
                    "notificationlog",
                    "agentrun",
                    "agentaction",
                    "agentmemory",
                    "gmailsyncstate",
                ],
            },
        ),
    ]
)

# Модели, зарегистрированные в админке, но скрытые из меню:
# работа с ними идёт из карточек (письма — с Container/Car, автовозы и
# водители — inline на Перевозчике, Дела — через Доску дел, личные карты
# и переводы — через кастомную страницу /admin/personal-cards/).
HIDDEN_MODELS = {
    "task",
    "containeremaillink",
    "caremaillink",
    "carriertruck",
    "carrierdriver",
    "personalcard",
    "personaltransfer",
}


def _model_names_in_groups():
    names = set()
    for conf in ADMIN_GROUPS.values():
        for entry in conf["items"]:
            if isinstance(entry, str):
                names.add(entry)
    return names


_GROUPED_MODEL_NAMES = _model_names_in_groups()


def _build_model_to_group():
    mapping = {}
    for group_name, conf in ADMIN_GROUPS.items():
        for entry in conf["items"]:
            if isinstance(entry, str):
                mapping[entry] = group_name
    return mapping


_MODEL_TO_GROUP = _build_model_to_group()


class LogistAdminSite(BaseAdminSite):
    site_header = "Caromoto Lithuania"
    site_title = "Caromoto Admin"
    index_title = "Панель управления"

    # ────────────────────────────────────────────────────────────────────────
    def index(self, request, extra_context=None):
        """Стартовая страница админки — Dashboard.

        Стандартный index (плоский список приложений) дублирует sidebar
        и не несёт пользы.
        """
        return redirect("company_dashboard")

    # ────────────────────────────────────────────────────────────────────────
    def each_context(self, request):
        context = super().each_context(request)
        context["sidebar_nav"] = self._build_sidebar_nav(request)
        context["current_path"] = request.path
        return context

    # ────────────────────────────────────────────────────────────────────────
    def _collect_model_index(self, request):
        """object_name.lower() → запись модели из get_app_list (все приложения)."""
        index = {}
        for app in super().get_app_list(request):
            for model in app.get("models", []):
                index[model["object_name"].lower()] = model
        return index

    # ────────────────────────────────────────────────────────────────────────
    def _build_sidebar_nav(self, request):
        """
        Строит навигацию сайдбара из ADMIN_GROUPS.
        Возвращает список групп:
        [
            {
                'name': 'Логистика', 'icon': 'bi-truck',
                'items': [{'name', 'url', 'icon', 'active', 'add_url', 'view_only'}, ...],
                'is_open': bool,       # развёрнута при рендере
                'collapsed_default': bool,  # свёрнута по умолчанию (для localStorage)
            },
            ...
        ]
        """
        current_path = request.path
        model_index = self._collect_model_index(request)
        used_models = set()
        nav = []

        for group_name, conf in ADMIN_GROUPS.items():
            items = []
            has_active = False

            for entry in conf["items"]:
                if isinstance(entry, str):
                    model = model_index.get(entry)
                    if model is None:
                        continue  # нет прав или модель не зарегистрирована
                    used_models.add(entry)
                    model_url = model.get("admin_url", "")
                    is_active = current_path.startswith(model_url) if model_url else False
                    items.append(
                        {
                            "name": model.get("name", ""),
                            "url": model_url,
                            "icon": MODEL_ICONS.get(entry, "bi-circle"),
                            "active": is_active,
                            "add_url": model.get("add_url", ""),
                            "view_only": not model.get("add_url"),
                        }
                    )
                else:
                    match = entry.get("match", entry["url"])
                    is_active = current_path.startswith(match)
                    items.append(
                        {
                            "name": entry["name"],
                            "url": entry["url"],
                            "icon": entry["icon"],
                            "active": is_active,
                            "add_url": "",
                            "view_only": True,
                        }
                    )

                if is_active:
                    has_active = True

            if items:
                nav.append(
                    {
                        "name": group_name,
                        "icon": conf["icon"],
                        "items": items,
                        "is_open": has_active or not conf.get("collapsed", False),
                        "collapsed_default": conf.get("collapsed", False),
                    }
                )

        # Страховка: новые модели, не разнесённые по группам и не скрытые,
        # попадают в «Прочее» — чтобы не потерялись молча.
        leftover = []
        for model_name, model in model_index.items():
            if model_name in used_models or model_name in HIDDEN_MODELS:
                continue
            model_url = model.get("admin_url", "")
            leftover.append(
                {
                    "name": model.get("name", ""),
                    "url": model_url,
                    "icon": MODEL_ICONS.get(model_name, "bi-circle"),
                    "active": current_path.startswith(model_url) if model_url else False,
                    "add_url": model.get("add_url", ""),
                    "view_only": not model.get("add_url"),
                }
            )
        if leftover:
            nav.append(
                {
                    "name": "Прочее",
                    "icon": "bi-three-dots",
                    "items": leftover,
                    "is_open": any(i["active"] for i in leftover),
                    "collapsed_default": True,
                }
            )

        return nav

    # ────────────────────────────────────────────────────────────────────────
    def get_app_list(self, request, app_label=None):
        """
        Группирует модели по ADMIN_GROUPS (используется app_index и т.п.;
        сам index редиректит на Dashboard).
        """
        original = super().get_app_list(request, app_label=app_label)

        all_models = []
        for app in original:
            all_models.extend(app.get("models", []))

        if not all_models:
            return original

        groups = OrderedDict()
        ungrouped = []

        for model_entry in all_models:
            model_name = model_entry["object_name"].lower()
            group_name = _MODEL_TO_GROUP.get(model_name)
            if group_name:
                groups.setdefault(group_name, []).append(model_entry)
            else:
                # В т.ч. скрытые из меню модели: app_index остаётся полным
                # списком, прячет их только sidebar.
                ungrouped.append(model_entry)

        result = []
        for group_name, conf in ADMIN_GROUPS.items():
            models_in_group = groups.get(group_name, [])
            if not models_in_group:
                continue
            order = {e: i for i, e in enumerate(conf["items"]) if isinstance(e, str)}
            models_in_group.sort(key=lambda m: order.get(m["object_name"].lower(), 999))
            result.append(
                {
                    "name": group_name,
                    "app_label": "core",
                    "app_url": "/admin/core/",
                    "has_module_perms": True,
                    "models": models_in_group,
                }
            )

        if ungrouped:
            result.append(
                {
                    "name": "Прочее",
                    "app_label": "core",
                    "app_url": "/admin/core/",
                    "has_module_perms": True,
                    "models": ungrouped,
                }
            )

        return result


# ── Глобальный экземпляр ────────────────────────────────────────────────────
admin_site = LogistAdminSite(name="admin")
