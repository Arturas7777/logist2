"""Пакет ``core.views_website`` — views клиентского сайта по доменам.

Раньше всё лежало в одном файле ``core/views_website.py`` (~1140 строк).
В рамках H6c (см. ``docs/ROADMAP_2026-05_high_medium.md``) он распилен
на подмодули по доменам. Этот ``__init__.py`` реэкспортирует всё, что
импортируется в ``core/urls_website.py`` через
``from . import views_website`` + ``views_website.<name>``, чтобы
URL-конфиг работал без изменений.

Подмодули:

* :mod:`.public`         — статические страницы (home/about/services/
  contact/news_list/news_detail), кэшируются ``@cache_page``.
* :mod:`.client_portal`  — личный кабинет (``client_dashboard``,
  ``car_detail``, ``container_detail``), `@login_required`.
* :mod:`.api`            — DRF ``ViewSet``-ы и permission
  :class:`IsClientUser`.
* :mod:`.tracking`       — публичный ``/api/track/`` + аналитика
  ``TrackingRequest``.
* :mod:`.photos_authed`  — скачивание фото по сессии клиента
  (``download_car_photo``, ``download_container_photo``,
  ``download_all_car_photos``).
* :mod:`.ai_chat`        — ИИ-помощник (``ai_chat`` + история, локальный
  fallback :func:`get_ai_response`).
* :mod:`.signed_photos`  — публичные signed-URL для галереи (H5a):
  ``get_container_photos``, ``download_photos_archive``,
  ``serve_signed_photo``.
"""

from .ai_chat import (
    ai_chat,
    ai_chat_feedback,
    ai_chat_history,
    get_ai_response,
)
from .api import (
    ClientCarViewSet,
    ClientContainerViewSet,
    ContactMessageViewSet,
    IsClientUser,
    NewsViewSet,
)
from .client_portal import (
    car_detail,
    client_dashboard,
    container_detail,
)
from .photos_authed import (
    download_all_car_photos,
    download_car_photo,
    download_container_photo,
)
from .public import (
    about_page,
    contact_page,
    news_detail,
    news_list,
    services_page,
    website_home,
)
from .signed_photos import (
    download_photos_archive,
    get_container_photos,
    serve_signed_photo,
)
from .tracking import track_shipment

__all__ = [
    # public
    'website_home', 'about_page', 'services_page', 'contact_page',
    'news_list', 'news_detail',
    # client_portal
    'client_dashboard', 'car_detail', 'container_detail',
    # api
    'IsClientUser', 'ClientCarViewSet', 'ClientContainerViewSet',
    'NewsViewSet', 'ContactMessageViewSet',
    # tracking
    'track_shipment',
    # photos_authed
    'download_car_photo', 'download_container_photo', 'download_all_car_photos',
    # ai_chat
    'get_ai_response', 'ai_chat', 'ai_chat_feedback', 'ai_chat_history',
    # signed_photos
    'get_container_photos', 'download_photos_archive', 'serve_signed_photo',
]
