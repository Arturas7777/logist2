"""Выписать токен бота. Запускает только оператор на сервере, не сами боты.

Токен печатается один раз. В базе остаётся SHA-256.
"""

from django.core.management.base import BaseCommand, CommandError

from core.services.bot_runtime import BotError, issue_token


class Command(BaseCommand):
    help = "Создать или сменить токен бота."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--role", required=True)

    def handle(self, *args, **options):
        try:
            principal, token = issue_token(options["name"], options["role"])
        except BotError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"bot={principal.name} role={principal.role}")
        self.stdout.write(f"token={token}")
