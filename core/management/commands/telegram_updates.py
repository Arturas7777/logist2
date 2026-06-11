"""Показывает свежие сообщения боту (getUpdates) для поиска chat_id клиентов.

Использование:
    python manage.py telegram_updates

Клиент должен один раз написать боту (например, /start или любое сообщение),
после чего его chat_id появится в выводе этой команды. Найденный chat_id
вписывается в карточку клиента (поле «Telegram Chat ID»).

Замечание: getUpdates не работает, если на бота установлен webhook. Для
рассылки уведомлений webhook не нужен, поэтому в этом проекте используется
обычный polling-доступ к getUpdates.
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
            raise CommandError(f"Ошибка запроса к Telegram API: {e}")

        data = resp.json() if resp.content else {}
        if not data.get("ok"):
            raise CommandError(f"Telegram API вернул ошибку: {data.get('description', resp.status_code)}")

        results = data.get("result", [])
        if not results:
            self.stdout.write(
                self.style.WARNING(
                    "Нет новых сообщений. Попросите клиента написать боту /start и повторите команду.\n"
                    "Учтите: getUpdates показывает только сообщения за последние ~24 часа и не работает при webhook."
                )
            )
            return

        seen = {}
        for upd in results:
            msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None or chat_id in seen:
                continue
            title = chat.get("title") or " ".join(p for p in [chat.get("first_name"), chat.get("last_name")] if p)
            username = chat.get("username")
            seen[chat_id] = (title or "—", username or "—", chat.get("type", "—"))

        self.stdout.write(self.style.SUCCESS(f"Найдено уникальных чатов: {len(seen)}\n"))
        self.stdout.write(f"{'chat_id':<16}{'тип':<12}{'username':<22}имя")
        self.stdout.write("-" * 70)
        for chat_id, (title, username, ctype) in seen.items():
            uname = ("@" + username) if username != "—" else "—"
            self.stdout.write(f"{chat_id!s:<16}{ctype:<12}{uname:<22}{title}")

        self.stdout.write("\nВпишите нужный chat_id в карточку клиента (Партнёры -> Клиент -> Telegram Chat ID).")
