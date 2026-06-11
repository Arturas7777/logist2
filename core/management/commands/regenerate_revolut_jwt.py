"""
Перегенерация JWT-assertion (client_assertion) для существующего
Revolut-подключения.

Используется когда JWT истёк (по умолчанию — 90 дней с момента создания),
но **приватный ключ + client_id сохранены**. В отличие от `setup_revolut`
эта команда не требует прохождения OAuth-flow заново — `refresh_token`
остаётся валидным, и при следующем `sync_all()` access_token будет
обновлён уже новым JWT.

Использование:
    # На сервере (приватный ключ обычно в certs/privatecert.pem):
    .venv/bin/python manage.py regenerate_revolut_jwt \
        --private-key certs/privatecert.pem

    # Указать конкретное подключение (если их несколько):
    python manage.py regenerate_revolut_jwt \
        --private-key certs/privatecert.pem \
        --connection-id 1

    # Указать redirect_uri (по умолчанию совпадает с тем, что использовался
    # при первичной настройке — берётся из CREDENTIALS.md). Поле `iss`
    # внутри JWT — это домен из redirect_uri:
    python manage.py regenerate_revolut_jwt \
        --private-key certs/privatecert.pem \
        --redirect-uri https://caromoto-lt.com/api/revolut/callback/

После регенерации команда автоматически делает `sync_all()` чтобы
проверить, что новый JWT принят сервером Revolut.
"""

import base64
import json
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Перегенерирует JWT-assertion для Revolut-подключения из существующего приватного ключа (без OAuth-flow)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--private-key",
            type=str,
            required=True,
            help="Путь к privatecert.pem",
        )
        parser.add_argument(
            "--connection-id",
            type=int,
            default=None,
            help="ID BankConnection (если несколько Revolut-подключений). По умолчанию — единственное активное.",
        )
        parser.add_argument(
            "--redirect-uri",
            type=str,
            default="https://caromoto-lt.com/api/revolut/callback/",
            help="OAuth Redirect URI — определяет поле `iss` в JWT. "
            "Должно совпадать с тем, что использовалось при первичной "
            "настройке (см. docs/CREDENTIALS.md).",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Срок жизни JWT в днях (по умолчанию 90 — максимум для Revolut).",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Не выполнять тестовый sync_all после регенерации.",
        )

    def handle(self, *args, **options):
        from core.models_banking import BankConnection

        key_path = Path(options["private_key"])
        if not key_path.exists():
            raise CommandError(f"Файл приватного ключа не найден: {key_path}")

        conn_id = options["connection_id"]
        if conn_id:
            try:
                conn = BankConnection.objects.get(pk=conn_id, bank_type="REVOLUT")
            except BankConnection.DoesNotExist:
                raise CommandError(f"BankConnection id={conn_id} типа REVOLUT не найдено")
        else:
            qs = BankConnection.objects.filter(bank_type="REVOLUT", is_active=True)
            if qs.count() == 0:
                raise CommandError("Нет активных Revolut-подключений в БД")
            if qs.count() > 1:
                raise CommandError(
                    f"Найдено {qs.count()} активных Revolut-подключений. "
                    f"Укажите --connection-id явно. "
                    f"Доступные: {list(qs.values_list('id', 'name'))}"
                )
            conn = qs.first()

        client_id = conn.client_id
        if not client_id:
            raise CommandError(f"У {conn} пустой client_id. Сделайте полный setup_revolut.")

        self.stdout.write(
            self.style.MIGRATE_HEADING(f'\nПерегенерация JWT для подключения "{conn.name}" (id={conn.pk})')
        )
        self.stdout.write(f"  client_id: {client_id[:20]}...")
        self.stdout.write(f"  redirect_uri: {options['redirect_uri']}")
        self.stdout.write(f"  срок жизни: {options['days']} дней")

        old_exp = conn.jwt_expires_at
        if old_exp:
            self.stdout.write(f"  старый JWT истекал: {old_exp.isoformat()}")

        try:
            new_jwt = self._generate_jwt(
                private_key_path=str(key_path),
                client_id=client_id,
                redirect_uri=options["redirect_uri"],
                days=options["days"],
            )
        except Exception as e:
            raise CommandError(f"Ошибка генерации JWT: {e}")

        conn.jwt_assertion = new_jwt
        # Сбрасываем access_token: он истечёт сам через ~40 мин, но при
        # следующем sync_all RevolutService увидит is_token_expired=True
        # и вызовет _refresh_access_token уже с новым JWT.
        conn.access_token_expires_at = timezone.now() - timedelta(minutes=1)
        conn.last_error = ""
        conn.save(
            update_fields=[
                "_jwt_assertion",
                "access_token_expires_at",
                "last_error",
                "updated_at",
            ]
        )

        new_exp = conn.jwt_expires_at
        self.stdout.write(self.style.SUCCESS(f"  JWT обновлён, новый срок: {new_exp.isoformat() if new_exp else '?'}"))

        if options["no_sync"]:
            self.stdout.write("\nПропуск тестовой синхронизации (--no-sync).")
            return

        self.stdout.write(
            self.style.MIGRATE_HEADING("\nТестовая синхронизация (fetch_accounts + fetch_transactions)...")
        )
        from core.services.revolut_service import RevolutService

        result = RevolutService(conn).sync_all()

        if result["error"]:
            raise CommandError(
                f"Синхронизация не удалась: {result['error']}\n"
                f"JWT обновлён, но Revolut вернул ошибку. Проверьте refresh_token."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"  Синхронизировано: {len(result['accounts'])} счетов, "
                f"{len(result['transactions'])} транзакций, "
                f"{result.get('expenses_updated', 0)} expenses"
            )
        )
        for acc in result["accounts"]:
            self.stdout.write(f"    {acc.name}: {acc.balance} {acc.currency}")

        self.stdout.write(self.style.SUCCESS("\nГотово. Синхронизация Revolut восстановлена."))

    def _generate_jwt(self, private_key_path: str, client_id: str, redirect_uri: str, days: int) -> str:
        """Генерирует JWT (client assertion) для Revolut API.

        Логика идентична `setup_revolut.py::_generate_jwt`, но срок
        жизни вынесен в параметр.
        """
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        iss = urlparse(redirect_uri).hostname or "localhost"

        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": iss,
            "sub": client_id,
            "aud": "https://revolut.com",
            "exp": int(time.time()) + (days * 24 * 3600),
        }

        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        header_b64 = b64url(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = b64url(json.dumps(payload, separators=(",", ":")).encode())
        message = f"{header_b64}.{payload_b64}".encode()

        with open(private_key_path, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)

        signature = private_key.sign(
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        signature_b64 = b64url(signature)
        return f"{header_b64}.{payload_b64}.{signature_b64}"
