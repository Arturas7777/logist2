"""
Сервис для работы с Revolut Business API
=========================================

Основные возможности:
- Автоматическое обновление access_token через refresh_token
- Получение балансов счетов
- Получение последних транзакций
- Полная синхронизация (sync_all)

API Reference: https://developer.revolut.com/docs/business/business-api
"""

import requests
import logging
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone

logger = logging.getLogger(__name__)

# Маппинг типов транзакций Revolut -> наши типы
REVOLUT_TYPE_MAP = {
    'card_payment': 'card_payment',
    'card_refund': 'card_refund',
    'transfer': 'transfer',
    'exchange': 'exchange',
    'topup': 'topup',
    'fee': 'fee',
    'atm': 'atm',
    'refund': 'refund',
    'tax': 'tax',
    'tax_refund': 'tax',
    'topup_return': 'topup',
    'card_chargeback': 'card_refund',
    'card_credit': 'card_payment',
}


class RevolutAPIError(Exception):
    """Ошибка при обращении к Revolut API."""

    def __init__(self, message, status_code=None, response_body=None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class RevolutService:
    """Клиент для Revolut Business API v1.0."""

    TOKEN_ENDPOINT = '/api/1.0/auth/token'
    ACCOUNTS_ENDPOINT = '/api/1.0/accounts'
    TRANSACTIONS_ENDPOINT = '/api/1.0/transactions'

    def __init__(self, connection):
        """
        Args:
            connection: экземпляр BankConnection с типом REVOLUT
        """
        self.connection = connection
        self.base_url = connection.base_url
        self._session = requests.Session()
        self._session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        })

    # ========================================================================
    # TOKEN MANAGEMENT
    # ========================================================================

    def _get_valid_token(self) -> str:
        """
        Возвращает валидный access_token.
        Если текущий истёк — обновляет через refresh_token + JWT.
        """
        if not self.connection.is_token_expired:
            return self.connection.access_token

        logger.info(f'[Revolut] Access token истёк для {self.connection}, обновляем...')
        return self._refresh_access_token()

    def _refresh_access_token(self) -> str:
        """Обновляет access_token через refresh_token и JWT assertion."""
        url = f'{self.base_url}{self.TOKEN_ENDPOINT}'

        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.connection.refresh_token,
            'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
            'client_assertion': self.connection.jwt_assertion,
        }

        try:
            resp = self._session.post(
                url,
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            error_msg = f'Ошибка обновления токена: {e}'
            logger.error(f'[Revolut] {error_msg}')
            self.connection.last_error = error_msg
            self.connection.save(update_fields=['last_error', 'updated_at'])
            raise RevolutAPIError(error_msg, getattr(e.response, 'status_code', None))

        body = resp.json()
        new_token = body.get('access_token', '')
        expires_in = body.get('expires_in', 2399)  # по умолчанию ~40 мин

        # Сохраняем новый токен
        self.connection.access_token = new_token
        self.connection.access_token_expires_at = timezone.now() + timedelta(seconds=expires_in - 60)
        self.connection.last_error = ''
        self.connection.save(update_fields=[
            '_access_token', 'access_token_expires_at', 'last_error', 'updated_at',
        ])

        logger.info(f'[Revolut] Токен обновлён, истекает через {expires_in}с')
        return new_token

    def _get_auth_headers(self) -> dict:
        token = self._get_valid_token()
        return {'Authorization': f'Bearer {token}'}

    def _api_get(self, endpoint: str, params: dict = None) -> dict | list:
        """Выполняет GET-запрос к Revolut API с авторизацией."""
        url = f'{self.base_url}{endpoint}'
        try:
            resp = self._session.get(
                url,
                headers=self._get_auth_headers(),
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            body = None
            if hasattr(e, 'response') and e.response is not None:
                try:
                    body = e.response.json()
                except Exception:
                    body = e.response.text
            error_msg = f'API GET {endpoint} ошибка: {e}'
            logger.error(f'[Revolut] {error_msg}')
            raise RevolutAPIError(error_msg, status, body)

    # ========================================================================
    # ACCOUNTS
    # ========================================================================

    def fetch_accounts(self) -> list:
        """
        Получает список счетов из Revolut и обновляет BankAccount в БД.
        Возвращает список обновлённых BankAccount.
        """
        from ..models_banking import BankAccount

        logger.info(f'[Revolut] Загружаем счета для {self.connection}')
        data = self._api_get(self.ACCOUNTS_ENDPOINT)

        updated_accounts = []
        seen_ids = set()

        for item in data:
            ext_id = item.get('id', '')
            seen_ids.add(ext_id)

            account, created = BankAccount.objects.update_or_create(
                connection=self.connection,
                external_id=ext_id,
                defaults={
                    'name': item.get('name', ''),
                    'currency': item.get('currency', ''),
                    'balance': Decimal(str(item.get('balance', 0))),
                    'state': item.get('state', 'active'),
                },
            )
            updated_accounts.append(account)
            action = 'создан' if created else 'обновлён'
            logger.debug(f'[Revolut] Счёт {account.name} ({account.currency}) {action}: {account.balance}')

        # Деактивируем счета, которых больше нет в API
        BankAccount.objects.filter(
            connection=self.connection,
        ).exclude(
            external_id__in=seen_ids,
        ).update(state='inactive')

        logger.info(f'[Revolut] Загружено {len(updated_accounts)} счетов')
        return updated_accounts

    # ========================================================================
    # TRANSACTIONS
    # ========================================================================

    def fetch_transactions(self, days: int = 30, limit: int = 100) -> list:
        """
        Получает последние транзакции из Revolut и сохраняет в БД.
        
        Args:
            days: за сколько дней загружать
            limit: макс. количество транзакций для сохранения
            
        Returns:
            список обновлённых BankTransaction
        """
        from ..models_banking import BankTransaction

        logger.info(f'[Revolut] Загружаем транзакции за {days} дней для {self.connection}')

        from_date = (timezone.now() - timedelta(days=days)).isoformat()
        params = {
            'from': from_date,
            'count': min(limit, 1000),  # API максимум 1000
        }

        data = self._api_get(self.TRANSACTIONS_ENDPOINT, params=params)

        updated_transactions = []

        for item in data[:limit]:
            ext_id = item.get('id', '')
            raw_type = item.get('type', 'other').lower()
            tx_type = REVOLUT_TYPE_MAP.get(raw_type, 'other')

            # Парсим сумму из legs
            legs = item.get('legs', [])
            amount = Decimal('0')
            currency = ''
            counterparty = ''
            description = item.get('reference', '') or item.get('description', '')

            if legs:
                leg = legs[0]
                amount = Decimal(str(leg.get('amount', 0)))
                currency = leg.get('currency', '')
                cp = leg.get('counterparty', {})
                if isinstance(cp, dict):
                    counterparty = cp.get('account_name', '') or cp.get('name', '')

            # Парсим дату
            created_at_str = item.get('created_at', '')
            try:
                from django.utils.dateparse import parse_datetime
                created_at = parse_datetime(created_at_str)
                if created_at is None:
                    created_at = timezone.now()
            except Exception:
                created_at = timezone.now()

            state = item.get('state', 'completed').lower()

            tx, created = BankTransaction.objects.update_or_create(
                connection=self.connection,
                external_id=ext_id,
                defaults={
                    'transaction_type': tx_type,
                    'amount': amount,
                    'currency': currency,
                    'description': description[:500] if description else '',
                    'counterparty_name': counterparty[:200] if counterparty else '',
                    'state': state,
                    'created_at': created_at,
                },
            )
            updated_transactions.append(tx)

        # Удаляем старые транзакции (старше 90 дней) для экономии места
        cutoff = timezone.now() - timedelta(days=90)
        deleted_count, _ = BankTransaction.objects.filter(
            connection=self.connection,
            created_at__lt=cutoff,
        ).delete()
        if deleted_count:
            logger.info(f'[Revolut] Удалено {deleted_count} старых транзакций')

        logger.info(f'[Revolut] Загружено {len(updated_transactions)} транзакций')
        return updated_transactions

    # ========================================================================
    # SYNC ALL
    # ========================================================================

    def sync_all(self) -> dict:
        """
        Полная синхронизация: счета + транзакции.
        Возвращает dict с результатами.
        """
        result = {
            'accounts': [],
            'transactions': [],
            'error': None,
        }

        try:
            result['accounts'] = self.fetch_accounts()
            result['transactions'] = self.fetch_transactions()

            self.connection.last_synced_at = timezone.now()
            self.connection.last_error = ''
            self.connection.save(update_fields=['last_synced_at', 'last_error', 'updated_at'])

            logger.info(
                f'[Revolut] Синхронизация завершена: '
                f'{len(result["accounts"])} счетов, '
                f'{len(result["transactions"])} транзакций'
            )

        except RevolutAPIError as e:
            result['error'] = str(e)
            self.connection.last_error = str(e)[:500]
            self.connection.save(update_fields=['last_error', 'updated_at'])
            logger.error(f'[Revolut] Ошибка синхронизации: {e}')

        except Exception as e:
            result['error'] = str(e)
            self.connection.last_error = f'Неожиданная ошибка: {str(e)[:400]}'
            self.connection.save(update_fields=['last_error', 'updated_at'])
            logger.exception(f'[Revolut] Неожиданная ошибка синхронизации')

        return result
