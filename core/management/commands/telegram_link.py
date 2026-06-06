"""Привязывает chat_id клиентов по персональным ссылкам ?start=<token>.

Ручной аналог периодической задачи process_telegram_starts_task. Полезно,
если Celery beat не запущен или нужно привязать прямо сейчас.

Использование:
    python manage.py telegram_link
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Привязывает chat_id клиентов по персональным ссылкам Telegram (?start=<token>)"

    def handle(self, *args, **options):
        from django.conf import settings

        if not getattr(settings, "TELEGRAM_BOT_TOKEN", ""):
            raise CommandError("TELEGRAM_BOT_TOKEN не задан в .env.")

        from core.services.telegram_service import process_telegram_starts

        linked = process_telegram_starts()
        if linked:
            self.stdout.write(self.style.SUCCESS(f"Привязано новых чатов: {linked}"))
        else:
            self.stdout.write(self.style.WARNING(
                "Новых привязок нет. Убедитесь, что клиент перешёл по своей персональной "
                "ссылке и нажал Start. Ссылку можно скопировать в карточке клиента."
            ))
