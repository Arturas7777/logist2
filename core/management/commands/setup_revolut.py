"""
Помощник для настройки Revolut Business API.

Этапы:
1. Генерация JWT (client assertion) из приватного ключа + client_id
2. Вывод ссылки для авторизации в Revolut
3. Обмен authorization code на access + refresh tokens
4. Сохранение всего в BankConnection

Использование:
    # Шаг 1: Генерация сертификатов (выполняется один раз)
    openssl genrsa -out privatecert.pem 2048
    openssl req -new -x509 -key privatecert.pem -out publiccert.cer -days 1825

    # Шаг 2: Загрузите publiccert.cer в Revolut Business → Settings → APIs → Business API
    # Скопируйте Client ID

    # Шаг 3: Запустите эту команду
    python manage.py setup_revolut
"""

import json
import base64
import time
import hashlib
import requests
from pathlib import Path
from django.core.management.base import BaseCommand
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Настройка подключения к Revolut Business API (генерация JWT, обмен токенов)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sandbox',
            action='store_true',
            help='Использовать sandbox-окружение Revolut',
        )
        parser.add_argument(
            '--private-key',
            type=str,
            default='privatecert.pem',
            help='Путь к файлу приватного ключа (по умолчанию: privatecert.pem)',
        )

    def handle(self, *args, **options):
        use_sandbox = options['sandbox']
        private_key_path = options['private_key']

        base_url = 'https://sandbox-b2b.revolut.com' if use_sandbox else 'https://b2b.revolut.com'
        app_url = 'https://sandbox-business.revolut.com' if use_sandbox else 'https://business.revolut.com'

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n  Настройка Revolut Business API {"(SANDBOX)" if use_sandbox else "(PRODUCTION)"}\n'
        ))

        # ── Шаг 1: Проверяем приватный ключ ──
        self.stdout.write(self.style.MIGRATE_HEADING('Шаг 1: Проверка приватного ключа'))

        key_file = Path(private_key_path)
        if not key_file.exists():
            self.stdout.write(self.style.ERROR(
                f'Файл {private_key_path} не найден!\n'
                f'Сгенерируйте ключ командой:\n'
                f'  openssl genrsa -out privatecert.pem 2048\n'
                f'  openssl req -new -x509 -key privatecert.pem -out publiccert.cer -days 1825\n'
            ))
            return

        self.stdout.write(self.style.SUCCESS(f'  Ключ найден: {key_file.resolve()}\n'))

        # ── Шаг 2: Запрашиваем Client ID и redirect URI ──
        self.stdout.write(self.style.MIGRATE_HEADING('Шаг 2: Данные из Revolut'))
        self.stdout.write(
            '  Откройте Revolut Business → Settings → APIs → Business API\n'
            '  и скопируйте Client ID после загрузки publiccert.cer\n'
        )

        client_id = input('  Введите Client ID: ').strip()
        if not client_id:
            self.stdout.write(self.style.ERROR('Client ID обязателен!'))
            return

        redirect_uri = input('  Введите OAuth Redirect URI (по умолчанию https://caromoto-lt.com/api/revolut/callback/): ').strip()
        if not redirect_uri:
            redirect_uri = 'https://caromoto-lt.com/api/revolut/callback/'

        # ── Шаг 3: Генерируем JWT ──
        self.stdout.write(self.style.MIGRATE_HEADING('\nШаг 3: Генерация JWT (client assertion)'))

        try:
            jwt_token = self._generate_jwt(
                private_key_path=str(key_file),
                client_id=client_id,
                redirect_uri=redirect_uri,
            )
            self.stdout.write(self.style.SUCCESS(f'  JWT создан ({len(jwt_token)} символов)\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Ошибка генерации JWT: {e}'))
            return

        # ── Шаг 4: Авторизация ──
        self.stdout.write(self.style.MIGRATE_HEADING('Шаг 4: Авторизация'))

        consent_url = (
            f'{app_url}/app-confirm?'
            f'client_id={client_id}&'
            f'redirect_uri={redirect_uri}&'
            f'response_type=code&'
            f'scope=READ'
        )

        self.stdout.write(f'\n  Откройте эту ссылку в браузере и нажмите "Authorise":\n')
        self.stdout.write(self.style.WARNING(f'  {consent_url}\n'))
        self.stdout.write(
            f'  После авторизации вас перенаправит на {redirect_uri}?code=XXXXX\n'
            f'  Скопируйте значение code из URL.\n'
        )

        auth_code = input('  Введите authorization code: ').strip()
        if not auth_code:
            self.stdout.write(self.style.ERROR('Authorization code обязателен!'))
            return

        # ── Шаг 5: Обмен code на токены ──
        self.stdout.write(self.style.MIGRATE_HEADING('\nШаг 5: Обмен authorization code на токены'))

        try:
            tokens = self._exchange_code(
                base_url=base_url,
                auth_code=auth_code,
                jwt_token=jwt_token,
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Ошибка обмена токенов: {e}'))
            return

        access_token = tokens.get('access_token', '')
        refresh_token = tokens.get('refresh_token', '')
        expires_in = tokens.get('expires_in', 2399)

        if not access_token or not refresh_token:
            self.stdout.write(self.style.ERROR(f'Не получены токены! Ответ API: {tokens}'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'  Access token получен (истекает через {expires_in}с)\n'
            f'  Refresh token получен\n'
        ))

        # ── Шаг 6: Сохраняем в БД ──
        self.stdout.write(self.style.MIGRATE_HEADING('Шаг 6: Сохранение в BankConnection'))

        from core.models_banking import BankConnection
        from core.models import Company
        from django.utils import timezone
        from datetime import timedelta

        company = Company.objects.filter(name__icontains='Caromoto').first()
        if not company:
            self.stdout.write(self.style.ERROR('Компания Caromoto не найдена!'))
            return

        conn_name = input(f'  Название подключения (по умолчанию "Revolut Business"): ').strip()
        if not conn_name:
            conn_name = 'Revolut Business'

        conn, created = BankConnection.objects.update_or_create(
            company=company,
            bank_type='REVOLUT',
            defaults={
                'name': conn_name,
                'is_active': True,
                'use_sandbox': use_sandbox,
            }
        )

        # Устанавливаем зашифрованные значения через property
        conn.client_id = client_id
        conn.refresh_token = refresh_token
        conn.access_token = access_token
        conn.jwt_assertion = jwt_token
        conn.access_token_expires_at = timezone.now() + timedelta(seconds=expires_in - 60)
        conn.last_error = ''
        conn.save()

        action = 'создано' if created else 'обновлено'
        self.stdout.write(self.style.SUCCESS(f'  Подключение {action}: {conn}\n'))

        # ── Шаг 7: Тестовая синхронизация ──
        self.stdout.write(self.style.MIGRATE_HEADING('Шаг 7: Тестовая синхронизация'))

        do_sync = input('  Выполнить тестовую синхронизацию? (y/n, по умолчанию y): ').strip().lower()
        if do_sync != 'n':
            from core.services.revolut_service import RevolutService
            service = RevolutService(conn)
            result = service.sync_all()

            if result['error']:
                self.stdout.write(self.style.ERROR(f'  Ошибка: {result["error"]}'))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'  Синхронизировано: {len(result["accounts"])} счетов, '
                    f'{len(result["transactions"])} транзакций\n'
                ))

                for acc in result['accounts']:
                    self.stdout.write(f'    {acc.name}: {acc.balance} {acc.currency}')

        self.stdout.write(self.style.SUCCESS('\n  Настройка завершена!\n'))
        self.stdout.write(
            '  Добавьте в cron для автоматической синхронизации:\n'
            '  */15 * * * * cd /var/www/logist2 && .venv/bin/python manage.py sync_bank_accounts\n'
        )

    def _generate_jwt(self, private_key_path: str, client_id: str, redirect_uri: str) -> str:
        """Генерирует JWT (client assertion) для Revolut API."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        # Извлекаем домен из redirect URI для iss
        from urllib.parse import urlparse
        iss = urlparse(redirect_uri).hostname or 'localhost'

        # JWT Header
        header = {
            'alg': 'RS256',
            'typ': 'JWT',
        }

        # JWT Payload (expire через 90 дней)
        payload = {
            'iss': iss,
            'sub': client_id,
            'aud': 'https://revolut.com',
            'exp': int(time.time()) + (90 * 24 * 3600),
        }

        # Base64url encode
        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

        header_b64 = b64url(json.dumps(header, separators=(',', ':')).encode())
        payload_b64 = b64url(json.dumps(payload, separators=(',', ':')).encode())
        message = f'{header_b64}.{payload_b64}'.encode()

        # Подписываем приватным ключом
        with open(private_key_path, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)

        signature = private_key.sign(
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        signature_b64 = b64url(signature)
        return f'{header_b64}.{payload_b64}.{signature_b64}'

    def _exchange_code(self, base_url: str, auth_code: str, jwt_token: str) -> dict:
        """Обменивает authorization code на access + refresh tokens."""
        url = f'{base_url}/api/1.0/auth/token'

        resp = requests.post(
            url,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'grant_type': 'authorization_code',
                'code': auth_code,
                'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
                'client_assertion': jwt_token,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            raise Exception(f'HTTP {resp.status_code}: {resp.text}')

        return resp.json()
