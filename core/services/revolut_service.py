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
import time
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
    EXPENSES_ENDPOINT = '/api/1.0/expenses'

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

    def _api_get_binary(self, endpoint: str, max_retries: int = 3) -> tuple[bytes, str]:
        """
        GET бинарного контента (для скачивания чеков).
        Возвращает (body_bytes, content_type).
        Обрабатывает 429 Too Many Requests с exponential backoff.
        """
        url = f'{self.base_url}{endpoint}'
        attempt = 0
        while True:
            try:
                resp = self._session.get(
                    url,
                    headers=self._get_auth_headers(),
                    timeout=60,
                )
                if resp.status_code == 429 and attempt < max_retries:
                    retry_after = int(resp.headers.get('Retry-After', 2 ** attempt))
                    logger.info(
                        f'[Revolut] 429 Rate limit на {endpoint}, пауза {retry_after}с'
                    )
                    time.sleep(max(retry_after, 1))
                    attempt += 1
                    continue
                resp.raise_for_status()
                return resp.content, resp.headers.get('Content-Type', 'application/octet-stream')
            except requests.RequestException as e:
                status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
                error_msg = f'API GET (binary) {endpoint} ошибка: {e}'
                logger.error(f'[Revolut] {error_msg}')
                raise RevolutAPIError(error_msg, status)

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
                    counterparty = (
                        cp.get('account_name', '')
                        or cp.get('name', '')
                        or cp.get('company_name', '')
                    )
                leg_desc = leg.get('description', '')
                if not description and leg_desc:
                    description = leg_desc
                # Revolut puts sender name in leg description as "Payment from Name"
                if not counterparty and leg_desc:
                    import re
                    pf_match = re.match(r'(?:Payment from|Transfer from)\s+(.+)', leg_desc, re.IGNORECASE)
                    if pf_match:
                        counterparty = pf_match.group(1).strip()

            # Fallback: top-level counterparty
            if not counterparty:
                top_cp = item.get('counterparty', {})
                if isinstance(top_cp, dict):
                    counterparty = (
                        top_cp.get('name', '')
                        or top_cp.get('account_name', '')
                        or top_cp.get('company_name', '')
                    )

            # Fallback: merchant (для карточных платежей)
            if not counterparty:
                merchant = item.get('merchant', {})
                if isinstance(merchant, dict):
                    counterparty = merchant.get('name', '')

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

            # Авто-пропуск служебных операций (комиссии, обмены, налоги)
            if created and tx_type in ('fee', 'exchange', 'tax'):
                type_labels = {'fee': 'Комиссия банка', 'exchange': 'Обмен валют', 'tax': 'Налог'}
                tx.reconciliation_skipped = True
                tx.reconciliation_note = f'Авто-пропуск: {type_labels.get(tx_type, tx_type)}'
                tx.save(update_fields=['reconciliation_skipped', 'reconciliation_note'])
                logger.debug(f'[Revolut] Авто-пропуск: {tx_type} {ext_id}')

            updated_transactions.append(tx)

        logger.info(f'[Revolut] Загружено {len(updated_transactions)} транзакций')
        return updated_transactions

    # ========================================================================
    # EXPENSES (чеки и категории из приложения Revolut)
    # ========================================================================

    def fetch_expenses(self, days: int = 30, limit: int = 500) -> int:
        """
        Тянет expenses из Revolut и привязывает expense_id/категорию к BankTransaction.
        
        Expense в Revolut = карточная транзакция (или transfer) + прикреплённый чек/категория.
        Мы обновляем соответствующую BankTransaction по external_id == expense.transaction_id.
        
        Returns:
            число BankTransaction, у которых обновлён expense_id
        """
        from ..models_banking import BankTransaction

        logger.info(f'[Revolut] Загружаем expenses за {days} дней для {self.connection}')

        from_date = (timezone.now() - timedelta(days=days)).date().isoformat()
        params = {'from': from_date, 'limit': min(limit, 1000)}

        try:
            data = self._api_get(self.EXPENSES_ENDPOINT, params=params)
        except RevolutAPIError as e:
            if e.status_code == 403:
                logger.warning(
                    '[Revolut] Expenses API недоступен (403): возможно, план ниже Grow'
                )
                return 0
            raise

        updated_count = 0
        for item in data or []:
            expense_id = item.get('id', '')
            tx_id = item.get('transaction_id', '')
            if not expense_id or not tx_id:
                continue

            bt = BankTransaction.objects.filter(
                connection=self.connection,
                external_id=tx_id,
            ).first()
            if not bt:
                continue

            update_fields = []

            if bt.expense_id != expense_id:
                bt.expense_id = expense_id
                update_fields.append('expense_id')

            # Категория из splits/labels
            category = ''
            splits = item.get('splits') or []
            if splits:
                cat_obj = (splits[0] or {}).get('category') or {}
                category = cat_obj.get('name', '') or cat_obj.get('code', '')
            if not category:
                labels = item.get('labels') or {}
                if isinstance(labels, dict):
                    first_group = next(iter(labels.values()), None)
                    if isinstance(first_group, list) and first_group:
                        category = str(first_group[0])
                    elif isinstance(first_group, str):
                        category = first_group

            if category and bt.revolut_category != category[:100]:
                bt.revolut_category = category[:100]
                update_fields.append('revolut_category')

            if update_fields:
                bt.save(update_fields=update_fields)
                updated_count += 1

            # Сразу тянем чеки, если есть receipt_ids и нет файла
            receipt_ids = item.get('receipt_ids') or []
            if receipt_ids and not bt.receipt_file:
                if self._download_receipt(bt, expense_id, receipt_ids[0]):
                    # Троттлинг: Revolut лимитирует ~100 req/мин, даём паузу
                    time.sleep(0.6)

        logger.info(f'[Revolut] Expenses: обновлено {updated_count} BankTransaction')
        return updated_count

    def _download_receipt(self, bt, expense_id: str, receipt_id: str) -> bool:
        """
        Скачивает чек из Revolut Expenses API и сохраняет в bt.receipt_file.
        
        Returns:
            True если успешно скачан и сохранён, False при ошибке
        """
        from django.core.files.base import ContentFile
        import mimetypes

        endpoint = f'{self.EXPENSES_ENDPOINT}/{expense_id}/receipts/{receipt_id}/content'
        try:
            body, content_type = self._api_get_binary(endpoint)
        except RevolutAPIError as e:
            logger.warning(
                f'[Revolut] Не удалось скачать чек {receipt_id} для BT {bt.pk}: {e}'
            )
            return False

        if not body:
            return False

        ext = mimetypes.guess_extension((content_type or '').split(';')[0].strip()) or '.bin'
        if ext == '.jpe':
            ext = '.jpg'
        filename = f'revolut_receipt_{bt.external_id[:20]}_{receipt_id[:8]}{ext}'

        bt.receipt_file.save(filename, ContentFile(body), save=False)
        bt.receipt_fetched_at = timezone.now()
        bt.save(update_fields=['receipt_file', 'receipt_fetched_at'])
        logger.info(
            f'[Revolut] Чек сохранён: BT {bt.pk} → {filename} ({len(body)} байт)'
        )
        return True

    def fetch_receipts_for_existing(self, limit: int = 200) -> int:
        """
        Догружает чеки для BankTransaction, у которых уже есть expense_id, но нет файла.
        Полезно после изменения настроек или ручного теста.
        """
        from ..models_banking import BankTransaction

        qs = BankTransaction.objects.filter(
            connection=self.connection,
            receipt_file='',
        ).exclude(expense_id='').order_by('-created_at')[:limit]

        downloaded = 0
        for bt in qs:
            try:
                expense = self._api_get(f'{self.EXPENSES_ENDPOINT}/{bt.expense_id}')
            except RevolutAPIError:
                continue
            receipt_ids = (expense or {}).get('receipt_ids') or []
            if receipt_ids and self._download_receipt(bt, bt.expense_id, receipt_ids[0]):
                downloaded += 1
                time.sleep(0.6)  # throttle для Revolut rate limits

        logger.info(f'[Revolut] Догружено чеков: {downloaded}')
        return downloaded

    # ========================================================================
    # SYNC ALL
    # ========================================================================

    def sync_all(self) -> dict:
        """
        Полная синхронизация: счета + транзакции + expenses (чеки).
        Возвращает dict с результатами.
        """
        result = {
            'accounts': [],
            'transactions': [],
            'expenses_updated': 0,
            'error': None,
        }

        try:
            result['accounts'] = self.fetch_accounts()
            result['transactions'] = self.fetch_transactions()

            try:
                result['expenses_updated'] = self.fetch_expenses()
            except Exception as e:
                logger.warning(f'[Revolut] Expenses sync failed (non-fatal): {e}')

            self.connection.last_synced_at = timezone.now()
            self.connection.last_error = ''
            self.connection.save(update_fields=['last_synced_at', 'last_error', 'updated_at'])

            logger.info(
                f'[Revolut] Синхронизация завершена: '
                f'{len(result["accounts"])} счетов, '
                f'{len(result["transactions"])} транзакций, '
                f'{result["expenses_updated"]} expenses'
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
