"""Разовая отметка всех писем прочитанными (то же, что кнопка в админке)."""

from django.core.management.base import BaseCommand

from core.services.email_read import mark_all_emails_read


class Command(BaseCommand):
    help = "Отметить прочитанными все письма в карточках контейнеров, авто и заявок."

    def handle(self, *args, **options):
        stats = mark_all_emails_read()
        self.stdout.write(
            "Прочитано: контейнеры {containers}, авто {cars}, заявки {requests}.".format(**stats)
        )
