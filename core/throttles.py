from rest_framework.throttling import AnonRateThrottle


class TrackShipmentThrottle(AnonRateThrottle):
    scope = "track_shipment"


class AIChatThrottle(AnonRateThrottle):
    scope = "ai_chat"


class PhotoDownloadThrottle(AnonRateThrottle):
    """Лимит на скачивание/просмотр фотографий контейнеров.

    Скоуп `photo_download` объявлен в settings (30/min), но раньше класса
    не было — лимит фактически не применялся ни на `get_container_photos`,
    ни на `download_photos_archive`. Теперь оба endpoint'а используют этот
    класс, защищая от скриптового скачивания всех фотографий подряд.
    """

    scope = "photo_download"
