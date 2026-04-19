from django.urls import path

from . import views, views_autotransport

app_name = 'core'

urlpatterns = [
    path('car/<int:car_id>/get_available_services/', views.get_available_services, name='get_available_services'),
    path('car/<int:car_id>/add_services/', views.add_services, name='add_services'),
    path('warehouses/', views.get_warehouses, name='get_warehouses'),
    path('container/<int:container_id>/sync-gdrive-photos/', views.sync_container_photos_from_gdrive, name='sync_container_photos_gdrive'),
    path('container/<int:container_id>/photos-json/', views.get_container_photos_json, name='get_container_photos_json'),
    # API для автокомплита в инвойсах
    path('api/search-counterparties/', views.search_counterparties, name='search_counterparties'),
    path('api/search-cars/', views.search_cars, name='search_cars'),
    path('api/search-invoices/', views.search_invoices, name='search_invoices'),
    # API для автовозов
    path('api/carrier/<int:carrier_id>/info/', views_autotransport.get_carrier_info, name='get_carrier_info'),
    path('api/driver/<int:driver_id>/phone/', views_autotransport.get_driver_phone, name='get_driver_phone'),
    path('api/driver/update-phone/', views_autotransport.update_driver_phone, name='update_driver_phone'),
    path('api/border-crossings/', views_autotransport.get_border_crossings, name='get_border_crossings'),
    path('api/carrier/create-truck/', views_autotransport.create_carrier_truck, name='create_carrier_truck'),
    path('api/carrier/create-driver/', views_autotransport.create_carrier_driver, name='create_carrier_driver'),

    # ── Переписка по контейнерам (Gmail) ────────────────────────────────────
    path('emails/<int:email_id>/', views.email_detail, name='email_detail'),
    path('emails/<int:email_id>/attachment/<int:idx>/', views.email_attachment, name='email_attachment'),
    path('emails/<int:email_id>/mark-read/', views.email_mark_read, name='email_mark_read'),
    path('emails/container/<int:container_id>/mark-all-read/', views.email_mark_container_read, name='email_mark_container_read'),
    path('emails/sync/', views.email_trigger_sync, name='email_trigger_sync'),

    # Phase 2: отправка писем из карточки контейнера
    path('emails/<int:email_id>/reply/draft/', views.email_reply_draft, name='email_reply_draft'),
    path('emails/<int:email_id>/reply/', views.email_reply_send, name='email_reply_send'),
    path('emails/compose/', views.email_compose_send, name='email_compose_send'),
]

