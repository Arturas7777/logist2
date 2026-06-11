"""Сервис отправки Telegram-уведомлений клиентам о разгрузке.

Дублирует email-канал (``email_service.py``): те же события (планируемая
разгрузка контейнера, фактическая разгрузка контейнера, разгрузка
отдельного ТС), но отправка идёт в Telegram через Bot API.

Дедуп независим от email: в ``NotificationLog`` пишется ``channel='TELEGRAM'``,
поэтому повторная отправка одному клиенту по контейнеру/ТС не происходит,
даже если email уже был отправлен (и наоборот).

Получатель определяется полем ``Client.telegram_chat_id``. Клиент должен
один раз написать боту (``/start``); найти chat_id можно командой
``python manage.py telegram_updates``.
"""

import html
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"


def _telegram_enabled():
    """True, если Telegram-канал настроен и включён глобально."""
    return bool(getattr(settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False)) and bool(
        getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    )


def send_telegram_message(chat_id, text):
    """Низкоуровневая отправка одного сообщения в Telegram.

    Возвращает (success: bool, error_message: str). Не бросает исключений —
    все ошибки сети/API оборачиваются в error_message.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return False, "TELEGRAM_BOT_TOKEN не задан"
    if not chat_id:
        return False, "Не указан chat_id"

    url = TELEGRAM_API_URL.format(token=token)
    timeout = int(getattr(settings, "TELEGRAM_API_TIMEOUT", 10))

    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )
        data = {}
        try:
            data = response.json()
        except ValueError:
            pass

        if response.status_code == 200 and data.get("ok"):
            return True, ""

        error = data.get("description") or f"HTTP {response.status_code}"
        return False, str(error)
    except requests.RequestException as e:
        return False, str(e)


def process_telegram_starts():
    """Привязывает chat_id к клиентам по персональным ссылкам ?start=<token>.

    Опрашивает getUpdates и для каждого сообщения вида ``/start <token>``
    находит клиента по ``telegram_link_token`` и добавляет chat_id в первый
    свободный слот. Бот отвечает подтверждением. Идемпотентно: если chat_id
    уже привязан, повторного сообщения не будет (поэтому безопасно вызывать
    периодически без отслеживания offset — Telegram хранит апдейты ~24ч).

    Возвращает количество новых привязок.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return 0

    from core.models import Client

    url = TELEGRAM_GET_UPDATES_URL.format(token=token)
    timeout = int(getattr(settings, "TELEGRAM_API_TIMEOUT", 10))

    try:
        resp = requests.get(url, params={"limit": 100}, timeout=timeout)
        data = resp.json() if resp.content else {}
    except (requests.RequestException, ValueError) as e:
        logger.error("Telegram getUpdates failed: %s", e)
        return 0

    if not data.get("ok"):
        logger.error("Telegram getUpdates not ok: %s", data.get("description"))
        return 0

    linked = 0
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        if not text.startswith("/start"):
            continue
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            continue  # просто /start без токена — привязать не к кому
        start_token = parts[1].strip()
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if not start_token or chat_id is None:
            continue

        client = Client.objects.filter(telegram_link_token=start_token).first()
        if not client:
            logger.warning("Telegram /start с неизвестным токеном: %s (chat %s)", start_token, chat_id)
            continue

        if client.add_telegram_chat_id(chat_id):
            linked += 1
            logger.info("Telegram: привязан chat %s к клиенту %s (id=%s)", chat_id, client.name, client.id)
            send_telegram_message(
                chat_id,
                f"✅ Вы подписаны на уведомления о разгрузке для клиента <b>{client.name}</b>.",
            )

    if linked:
        logger.info("Telegram process_telegram_starts: привязано %d новых чатов", linked)
    return linked


class TelegramNotificationService:
    """Telegram-уведомления о контейнерах (mirror email-сервиса)."""

    # ── Сборка текста сообщений ────────────────────────────────────────────

    @staticmethod
    def _company_footer():
        name = html.escape(getattr(settings, "COMPANY_NAME", "Caromoto Lithuania"))
        phone = getattr(settings, "COMPANY_PHONE", "")
        website = getattr(settings, "COMPANY_WEBSITE", "")
        lines = [f"\n<b>{name}</b>"]
        if phone:
            lines.append(html.escape(phone))
        if website:
            lines.append(html.escape(website))
        return "\n".join(lines)

    @staticmethod
    def _cars_block(cars):
        if not cars:
            return ""
        rows = []
        for car in cars:
            brand = html.escape(str(car.get("brand") or ""))
            year = html.escape(str(car.get("year") or ""))
            vin = html.escape(str(car.get("vin") or ""))
            label = " ".join(p for p in [brand, year] if p)
            if vin:
                rows.append(f"• {label} (VIN: {vin})")
            else:
                rows.append(f"• {label}")
        return "\n".join(rows)

    @staticmethod
    def _build_planned_text(container, client, cars, site_address):
        warehouse = html.escape(container.warehouse.name if container.warehouse else "Не указан")
        text = (
            f"📦 <b>Планируемая разгрузка контейнера {html.escape(container.number)}</b>\n\n"
            f"Здравствуйте, {html.escape(client.name)}!\n\n"
            f"Планируемая дата разгрузки: <b>{container.planned_unload_date}</b>\n"
            f"Склад: {warehouse}"
        )
        if site_address:
            text += f"\nАдрес: {html.escape(site_address)}"
        cars_block = TelegramNotificationService._cars_block(cars)
        if cars_block:
            text += f"\n\nВаши автомобили:\n{cars_block}"
        text += "\n" + TelegramNotificationService._company_footer()
        return text

    @staticmethod
    def _build_unload_text(container, client, cars, site_address):
        warehouse = html.escape(container.warehouse.name if container.warehouse else "Не указан")
        text = (
            f"✅ <b>Контейнер {html.escape(container.number)} разгружен</b>\n\n"
            f"Здравствуйте, {html.escape(client.name)}!\n\n"
            f"Дата разгрузки: <b>{container.unload_date}</b>\n"
            f"Склад: {warehouse}"
        )
        if site_address:
            text += f"\nАдрес: {html.escape(site_address)}"
        cars_block = TelegramNotificationService._cars_block(cars)
        if cars_block:
            text += f"\n\nВаши автомобили:\n{cars_block}"
        text += "\n" + TelegramNotificationService._company_footer()
        return text

    # ── Отправка по контейнеру ─────────────────────────────────────────────

    @staticmethod
    def send_planned_notification(container, client, user=None):
        """Отправляет уведомление о планируемой разгрузке клиенту в Telegram."""
        if not _telegram_enabled():
            return False
        if not client.has_telegram():
            logger.info("Telegram: клиент %s без chat_id/выключен — пропуск PLANNED", client.name)
            return False
        if not container.planned_unload_date:
            return False

        cars = list(container.container_cars.filter(client=client))
        if not cars:
            return False
        cars_list = [{"vin": c.vin, "brand": c.brand, "year": c.year} for c in cars]

        _name, site_address = container.get_unload_address()
        text = TelegramNotificationService._build_planned_text(container, client, cars_list, site_address)
        subject = f"Планируемая разгрузка контейнера {container.number}"

        return TelegramNotificationService._send_and_log(
            notification_type="PLANNED",
            container=container,
            car=None,
            client=client,
            subject=subject,
            text=text,
            cars_list=cars_list,
            user=user,
        )

    @staticmethod
    def send_unload_notification(container, client, user=None):
        """Отправляет уведомление о фактической разгрузке клиенту в Telegram."""
        if not _telegram_enabled():
            return False
        if not client.has_telegram():
            logger.info("Telegram: клиент %s без chat_id/выключен — пропуск UNLOADED", client.name)
            return False
        if not container.unload_date:
            return False

        cars = list(container.container_cars.filter(client=client))
        if not cars:
            return False
        cars_list = [{"vin": c.vin, "brand": c.brand, "year": c.year} for c in cars]

        _name, site_address = container.get_unload_address()
        text = TelegramNotificationService._build_unload_text(container, client, cars_list, site_address)
        subject = f"Контейнер {container.number} разгружен"

        return TelegramNotificationService._send_and_log(
            notification_type="UNLOADED",
            container=container,
            car=None,
            client=client,
            subject=subject,
            text=text,
            cars_list=cars_list,
            user=user,
        )

    @staticmethod
    def send_planned_to_all_clients(container, user=None):
        """Шлёт PLANNED всем клиентам контейнера, не уведомлённым в Telegram."""
        return TelegramNotificationService._send_to_all_clients(container, "PLANNED", user=user)

    @staticmethod
    def send_unload_to_all_clients(container, user=None):
        """Шлёт UNLOADED всем клиентам контейнера, не уведомлённым в Telegram."""
        return TelegramNotificationService._send_to_all_clients(container, "UNLOADED", user=user)

    @staticmethod
    def _send_to_all_clients(container, notification_type, user=None):
        """Общая логика рассылки по всем клиентам контейнера с дедупом."""
        if not _telegram_enabled():
            return 0, 0

        from django.db import transaction as db_transaction

        from core.models_website import NotificationLog

        with db_transaction.atomic():
            already_notified = set(
                NotificationLog.objects.select_for_update()
                .filter(
                    container=container,
                    notification_type=notification_type,
                    channel="TELEGRAM",
                    success=True,
                )
                .values_list("client_id", flat=True)
            )

            clients = set()
            for car in container.container_cars.select_related("client").all():
                if car.client and car.client.has_telegram() and car.client.id not in already_notified:
                    clients.add(car.client)

            sent = 0
            failed = 0
            for client in clients:
                if notification_type == "PLANNED":
                    ok = TelegramNotificationService.send_planned_notification(container, client, user)
                else:
                    ok = TelegramNotificationService.send_unload_notification(container, client, user)
                if ok:
                    sent += 1
                else:
                    failed += 1

        return sent, failed

    # ── Отправка по отдельному ТС ──────────────────────────────────────────

    @staticmethod
    def send_car_unload_notification(car, user=None):
        """Отправляет уведомление о разгрузке отдельного ТС (без контейнера)."""
        if not _telegram_enabled():
            return False
        if not car.client:
            return False
        client = car.client
        if not client.has_telegram():
            logger.info("Telegram: клиент %s без chat_id/выключен — пропуск CAR_UNLOADED", client.name)
            return False
        if not car.unload_date:
            return False

        car_info = {"vin": car.vin, "brand": car.brand, "year": car.year}
        _name, site_address = car.get_unload_address()
        warehouse = html.escape(car.warehouse.name if car.warehouse else "Не указан")

        text = (
            f"✅ <b>Ваш автомобиль разгружен</b>\n\n"
            f"Здравствуйте, {html.escape(client.name)}!\n\n"
            f"{html.escape(str(car.brand or ''))} {html.escape(str(car.year or ''))} "
            f"(VIN: {html.escape(str(car.vin or ''))})\n"
            f"Дата разгрузки: <b>{car.unload_date}</b>\n"
            f"Склад: {warehouse}"
        )
        if site_address:
            text += f"\nАдрес: {html.escape(site_address)}"
        text += "\n" + TelegramNotificationService._company_footer()

        subject = f"Ваш автомобиль {car.brand} ({car.vin}) разгружен"

        return TelegramNotificationService._send_and_log(
            notification_type="CAR_UNLOADED",
            container=None,
            car=car,
            client=client,
            subject=subject,
            text=text,
            cars_list=[car_info],
            user=user,
        )

    # ── Низкий уровень: отправка + лог ─────────────────────────────────────

    @staticmethod
    def _send_and_log(notification_type, container, car, client, subject, text, cars_list, user=None):
        """Отправляет сообщение на все chat_id клиента и пишет лог по каждому.

        Возвращает True, если хотя бы одно сообщение доставлено успешно.
        """
        from core.models_website import NotificationLog

        chat_ids = client.get_telegram_chat_ids()
        if not chat_ids:
            return False

        ref = container.number if container else (car.vin if car else "")
        success_count = 0

        for chat_id in chat_ids:
            success, error_message = send_telegram_message(chat_id, text)

            if success:
                success_count += 1
                logger.info("✅ Telegram отправлен: %s для %s → chat %s", notification_type, ref, chat_id)
            else:
                logger.error(
                    "❌ Telegram не отправлен: %s для %s → chat %s: %s",
                    notification_type,
                    ref,
                    chat_id,
                    error_message,
                )

            try:
                NotificationLog.objects.create(
                    container=container,
                    car=car,
                    client=client,
                    notification_type=notification_type,
                    channel="TELEGRAM",
                    email_to=str(chat_id or ""),
                    subject=subject,
                    cars_info=json.dumps(cars_list, ensure_ascii=False),
                    success=success,
                    error_message=error_message,
                    created_by=user,
                )
            except Exception as e:
                logger.error("Failed to create telegram notification log: %s", e)

        logger.info(
            "📨 Telegram %s для %s: %d/%d доставлено клиенту %s",
            notification_type,
            ref,
            success_count,
            len(chat_ids),
            client.name,
        )
        return success_count > 0

    # ── Дедуп-хелперы ──────────────────────────────────────────────────────

    @staticmethod
    def was_planned_notification_sent(container):
        from core.models_website import NotificationLog

        return NotificationLog.objects.filter(
            container=container, notification_type="PLANNED", channel="TELEGRAM", success=True
        ).exists()

    @staticmethod
    def was_unload_notification_sent(container):
        from core.models_website import NotificationLog

        return NotificationLog.objects.filter(
            container=container, notification_type="UNLOADED", channel="TELEGRAM", success=True
        ).exists()

    @staticmethod
    def was_car_unload_notification_sent(car):
        from core.models_website import NotificationLog

        return NotificationLog.objects.filter(
            car=car, notification_type="CAR_UNLOADED", channel="TELEGRAM", success=True
        ).exists()
