"""Показывает свежие сообщения боту (getUpdates) для поиска chat_id клиентов.

Использование:
    python manage.py telegram_updates

Клиент должен один раз написать боту (например, /start или любое сообщение),
после чего его chat_id появится в выводе этой команды. Найденный chat_id
вписывается в карточку клиента (поле «Telegram Chat ID»).

Замечание: getUpdates не работает, если на бота установлен webhook. Для
рассылки уведомлений webhook не нужен, поэтому в этом проекте используется
обычный polling-доступ к getUpdates.

Периодическая задача ``process_telegram_starts`` подтверждает апдейты
(offset), и Telegram их больше не отдаёт — поэтому команда дополнительно
показывает чаты, которые задача видела за последние 7 дней (кэш).
"""

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"


class Command(BaseCommand):
    help = "Показывает chat_id из последних сообщений боту (для заполнения Telegram Chat ID клиентов)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Сколько последних апдейтов запросить (по умолчанию 100)",
        )

    def handle(self, *args, **options):
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN не задан в .env — нечего опрашивать.")

        url = GET_UPDATES_URL.format(token=token)
        timeout = int(getattr(settings, "TELEGRAM_API_TIMEOUT", 10))

        try:
            resp = requests.get(url, params={"limit": options["limit"]}, timeout=timeout)
        except requests.RequestException as e:
            from core.services.telegram_service import describe_request_error

            raise CommandError(f"Ошибка запроса к Telegram API: {describe_request_error(e)}") from None

        data = resp.json() if resp.content else {}
        if not data.get("ok"):
            raise CommandError(f"Telegram API вернул ошибку: {data.get('description', resp.status_code)}")

        from django.core.cache import cache

        from core.services.telegram_service import TELEGRAM_RECENT_CHATS_KEY, describe_chat

        seen = dict(cache.get(TELEGRAM_RECENT_CHATS_KEY) or {})
        for upd in data.get("result", []):
            msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is not None:
                seen[chat_id] = describe_chat(chat)

        if not seen:
            self.stdout.write(
                self.style.WARNING(
                    "Нет новых сообщений. Попросите клиента написать боту /start и повторите команду.\n"
                    "Учтите: getUpdates показывает только сообщения за последние ~24 часа и не работает при webhook."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS(f"Найдено уникальных чатов: {len(seen)}\n"))
        self.stdout.write(f"{'chat_id':<16}{'тип':<12}{'username':<22}имя")
        self.stdout.write("-" * 70)
        for chat_id, (title, username, ctype) in seen.items():
            uname = ("@" + username) if username != "—" else "—"
            self.stdout.write(f"{chat_id!s:<16}{ctype:<12}{uname:<22}{title}")

        self.stdout.write("\nВпишите нужный chat_id в карточку клиента (Партнёры -> Клиент -> Telegram Chat ID).")
