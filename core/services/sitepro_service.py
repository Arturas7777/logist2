"""
Сервис для работы с site.pro (b1.lt) Accounting API
====================================================

Основные возможности:
- Аутентификация через username/password → access_token
- Создание/синхронизация клиентов
- Отправка инвойсов (sales.create / orders.create-sale)
- Получение PDF-ссылки на инвойс

API Reference: https://api.sitepro.com/docs/index
"""

import requests
import logging
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone

logger = logging.getLogger(__name__)


class SiteProAPIError(Exception):
    """Ошибка при обращении к site.pro API."""

    def __init__(self, message, status_code=None, response_body=None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class SiteProService:
    """Клиент для site.pro Accounting API."""

    TOKEN_ENDPOINT = '/token'
    RECORD_TOKEN_ENDPOINT = '/Account/RecordToken'

    # Эндпоинты для работы с данными (Custom API)
    SALES_CREATE_ENDPOINT = '/api/sales/create'
    ORDERS_CREATE_SALE_ENDPOINT = '/api/orders/create-sale'
    CLIENT_CREATE_ENDPOINT = '/api/client/create'
    INVOICES_GET_ENDPOINT = '/api/invoices/get'
    ITEMS_CREATE_ENDPOINT = '/api/items/create'

    def __init__(self, connection):
        """
        Args:
            connection: экземпляр SiteProConnection
        """
        self.connection = connection
        self.base_url = connection.base_url
        self._session = requests.Session()

    # ========================================================================
    # TOKEN MANAGEMENT
    # ========================================================================

    def _get_valid_token(self) -> str:
        """
        Возвращает валидный access_token.
        Если текущий истёк — получает новый через username/password.
        """
        if not self.connection.is_token_expired:
            return self.connection.access_token

        logger.info(f'[SitePro] Access token истёк для {self.connection}, обновляем...')
        return self._authenticate()

    def _authenticate(self) -> str:
        """
        Аутентификация через username/password.
        
        Шаг 1: POST /token → получаем access_token
        Шаг 2: POST /Account/RecordToken → регистрируем токен
        """
        url = f'{self.base_url}{self.TOKEN_ENDPOINT}'

        data = {
            'grant_type': 'password',
            'NeedsUserProfile': '0',
            'UserName': self.connection.username,
            'Password': self.connection.password,
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
            error_msg = f'Ошибка аутентификации: {e}'
            if hasattr(e, 'response') and e.response is not None:
                error_msg += f' | Response: {e.response.text[:500]}'
            logger.error(f'[SitePro] {error_msg}')
            self.connection.last_error = error_msg[:500]
            self.connection.save(update_fields=['last_error', 'updated_at'])
            raise SiteProAPIError(error_msg, getattr(e.response, 'status_code', None))

        body = resp.json()
        new_token = body.get('access_token', '')
        expires_in = body.get('expires_in', 3600)

        if not new_token:
            error_msg = f'Пустой access_token в ответе: {body}'
            logger.error(f'[SitePro] {error_msg}')
            raise SiteProAPIError(error_msg)

        # Сохраняем данные пользователя из ответа
        sitepro_user_id = str(body.get('siteprouserid', ''))
        sitepro_company_id = str(body.get('spcoid', ''))

        # Регистрируем токен (шаг 2)
        self._record_token(new_token)

        # Сохраняем токен в БД
        self.connection.access_token = new_token
        self.connection.access_token_expires_at = timezone.now() + timedelta(seconds=expires_in - 60)
        if sitepro_user_id:
            self.connection.sitepro_user_id = sitepro_user_id
        if sitepro_company_id:
            self.connection.sitepro_company_id = sitepro_company_id
        self.connection.last_error = ''
        self.connection.save(update_fields=[
            '_access_token', 'access_token_expires_at',
            'sitepro_user_id', 'sitepro_company_id',
            'last_error', 'updated_at',
        ])

        logger.info(f'[SitePro] Аутентификация успешна, токен истекает через {expires_in}с')
        return new_token

    def _record_token(self, token: str):
        """Регистрирует токен в site.pro (обязательный шаг после получения)."""
        url = f'{self.base_url}{self.RECORD_TOKEN_ENDPOINT}'

        try:
            resp = self._session.post(
                url,
                headers={'Authorization': f'bearer {token}'},
                timeout=30,
            )
            resp.raise_for_status()
            logger.debug(f'[SitePro] Токен зарегистрирован')
        except requests.RequestException as e:
            # Не фатально — логируем предупреждение
            logger.warning(f'[SitePro] Ошибка регистрации токена: {e}')

    def _get_auth_headers(self) -> dict:
        token = self._get_valid_token()
        return {'Authorization': f'bearer {token}'}

    def _api_get(self, endpoint: str, params: dict = None) -> dict | list:
        """Выполняет GET-запрос к site.pro API с авторизацией."""
        url = f'{self.base_url}{endpoint}'
        try:
            resp = self._session.get(
                url,
                headers=self._get_auth_headers(),
                params=params,
                timeout=30,
            )
            # При 401 — пробуем переаутентифицироваться
            if resp.status_code == 401:
                logger.warning('[SitePro] 401 Unauthorized, пробуем переаутентифицироваться...')
                self.connection.access_token_expires_at = None
                self.connection.save(update_fields=['access_token_expires_at'])
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
            logger.error(f'[SitePro] {error_msg}')
            raise SiteProAPIError(error_msg, status, body)

    def _api_post(self, endpoint: str, json_data: dict = None) -> dict | list:
        """Выполняет POST-запрос к site.pro API с авторизацией."""
        url = f'{self.base_url}{endpoint}'
        try:
            headers = self._get_auth_headers()
            headers['Content-Type'] = 'application/json'
            resp = self._session.post(
                url,
                headers=headers,
                json=json_data,
                timeout=30,
            )
            # При 401 — пробуем переаутентифицироваться
            if resp.status_code == 401:
                logger.warning('[SitePro] 401 Unauthorized, пробуем переаутентифицироваться...')
                self.connection.access_token_expires_at = None
                self.connection.save(update_fields=['access_token_expires_at'])
                headers = self._get_auth_headers()
                headers['Content-Type'] = 'application/json'
                resp = self._session.post(
                    url,
                    headers=headers,
                    json=json_data,
                    timeout=30,
                )
            resp.raise_for_status()
            # Некоторые эндпоинты могут возвращать пустой ответ
            if resp.content:
                return resp.json()
            return {}
        except requests.RequestException as e:
            status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            body = None
            if hasattr(e, 'response') and e.response is not None:
                try:
                    body = e.response.json()
                except Exception:
                    body = e.response.text
            error_msg = f'API POST {endpoint} ошибка: {e}'
            logger.error(f'[SitePro] {error_msg}')
            raise SiteProAPIError(error_msg, status, body)

    # ========================================================================
    # TEST CONNECTION
    # ========================================================================

    def test_connection(self) -> dict:
        """
        Проверяет подключение к site.pro.
        Возвращает dict с результатом.
        """
        result = {
            'success': False,
            'user_id': '',
            'company_id': '',
            'error': None,
        }

        try:
            token = self._authenticate()
            result['success'] = bool(token)
            result['user_id'] = self.connection.sitepro_user_id
            result['company_id'] = self.connection.sitepro_company_id
        except SiteProAPIError as e:
            result['error'] = str(e)

        return result

    # ========================================================================
    # CLIENT SYNC
    # ========================================================================

    def create_or_update_client(self, client) -> dict:
        """
        Создает или обновляет клиента в site.pro.
        
        Args:
            client: экземпляр core.Client
            
        Returns:
            dict с результатом из API
        """
        data = {
            'name': client.name or '',
            'code': getattr(client, 'company_code', '') or '',
            'vatCode': getattr(client, 'vat_code', '') or '',
            'address': getattr(client, 'address', '') or '',
            'email': getattr(client, 'email', '') or '',
            'phone': getattr(client, 'phone', '') or '',
            'country': getattr(client, 'country', 'LT') or 'LT',
        }

        logger.info(f'[SitePro] Создание/обновление клиента: {client.name}')

        try:
            result = self._api_post(self.CLIENT_CREATE_ENDPOINT, data)
            logger.info(f'[SitePro] Клиент {client.name} успешно синхронизирован')
            return result
        except SiteProAPIError as e:
            logger.error(f'[SitePro] Ошибка при создании клиента {client.name}: {e}')
            raise

    # ========================================================================
    # INVOICE PUSH
    # ========================================================================

    def push_invoice(self, invoice) -> dict:
        """
        Отправляет инвойс в site.pro как продажу.
        
        Args:
            invoice: экземпляр NewInvoice
            
        Returns:
            dict с результатом (external_id, external_number, etc.)
        """
        from ..models_accounting import SiteProInvoiceSync

        # Проверяем, не был ли инвойс уже отправлен
        existing_sync = SiteProInvoiceSync.objects.filter(
            connection=self.connection,
            invoice=invoice,
            sync_status='SENT',
        ).first()

        if existing_sync and existing_sync.external_id:
            logger.info(
                f'[SitePro] Инвойс {invoice.number} уже отправлен '
                f'(external_id={existing_sync.external_id})'
            )
            return {
                'already_synced': True,
                'external_id': existing_sync.external_id,
                'external_number': existing_sync.external_number,
            }

        # Создаем или получаем запись синхронизации
        sync, _ = SiteProInvoiceSync.objects.get_or_create(
            connection=self.connection,
            invoice=invoice,
            defaults={'sync_status': 'PENDING'},
        )

        # Формируем данные для API
        items_data = self._build_invoice_items(invoice)
        recipient = invoice.recipient

        # Данные получателя (клиент)
        billing_data = {}
        if invoice.recipient_client:
            client = invoice.recipient_client
            billing_data = {
                'billingName': client.name or '',
                'billingCompanyCode': getattr(client, 'company_code', '') or '',
                'billingVatCode': getattr(client, 'vat_code', '') or '',
                'billingAddress': getattr(client, 'address', '') or '',
                'billingCity': getattr(client, 'city', '') or '',
                'billingCountry': getattr(client, 'country', 'LT') or 'LT',
                'billingPostcode': getattr(client, 'postal_code', '') or '',
                'billingEmail': getattr(client, 'email', '') or '',
                'billingIsLegal': bool(getattr(client, 'company_code', '')),
            }
        elif recipient:
            billing_data = {
                'billingName': str(recipient),
            }

        # Серия и номер инвойса
        series = self.connection.invoice_series or ''
        inv_number = invoice.number or ''

        order_data = {
            'orderDate': invoice.date.isoformat() if invoice.date else timezone.now().date().isoformat(),
            'currency': self.connection.default_currency,
            'total': str(invoice.total),
            **billing_data,
            'items': items_data,
        }

        # Добавляем серию и номер если заданы
        if series:
            order_data['customSeries'] = series
        if inv_number:
            order_data['customNumber'] = inv_number

        logger.info(
            f'[SitePro] Отправка инвойса {invoice.number} '
            f'(получатель: {invoice.recipient_name}, сумма: {invoice.total})'
        )

        try:
            result = self._api_post(self.ORDERS_CREATE_SALE_ENDPOINT, order_data)

            # Обновляем запись синхронизации
            sync.external_id = str(result.get('id', '') or result.get('orderId', '') or '')
            sync.external_number = str(result.get('number', '') or result.get('invoiceNumber', '') or '')
            sync.sync_status = 'SENT'
            sync.error_message = ''
            sync.last_synced_at = timezone.now()
            sync.save()

            # Обновляем время синхронизации подключения
            self.connection.last_synced_at = timezone.now()
            self.connection.last_error = ''
            self.connection.save(update_fields=['last_synced_at', 'last_error', 'updated_at'])

            logger.info(
                f'[SitePro] Инвойс {invoice.number} успешно отправлен '
                f'(external_id={sync.external_id})'
            )

            return {
                'success': True,
                'external_id': sync.external_id,
                'external_number': sync.external_number,
                'response': result,
            }

        except SiteProAPIError as e:
            sync.sync_status = 'FAILED'
            sync.error_message = str(e)[:500]
            sync.last_synced_at = timezone.now()
            sync.save()

            self.connection.last_error = str(e)[:500]
            self.connection.save(update_fields=['last_error', 'updated_at'])

            logger.error(f'[SitePro] Ошибка отправки инвойса {invoice.number}: {e}')
            raise

    def _build_invoice_items(self, invoice) -> list:
        """Формирует список позиций инвойса для site.pro API."""
        items = []
        vat_rate = float(self.connection.default_vat_rate)

        for item in invoice.items.all().select_related('car').order_by('order'):
            item_name = item.description or ''
            if item.car:
                item_name = f'{item.description} ({item.car.vin})'

            items.append({
                'itemName': item_name,
                'quantity': str(item.quantity),
                'price': str(item.unit_price),
                'sum': str(item.total_price),
                'vatRate': str(vat_rate),
            })

        return items

    # ========================================================================
    # GET INVOICE PDF
    # ========================================================================

    def get_invoice_pdf_url(self, invoice) -> str:
        """
        Получает ссылку на PDF инвойса из site.pro.
        
        Args:
            invoice: экземпляр NewInvoice
            
        Returns:
            URL на PDF или пустая строка
        """
        from ..models_accounting import SiteProInvoiceSync

        sync = SiteProInvoiceSync.objects.filter(
            connection=self.connection,
            invoice=invoice,
            sync_status__in=['SENT', 'PDF_READY'],
        ).first()

        if not sync or not sync.external_id:
            logger.warning(f'[SitePro] Инвойс {invoice.number} не найден в site.pro')
            return ''

        try:
            params = {'id': sync.external_id}
            result = self._api_get(self.INVOICES_GET_ENDPOINT, params)

            pdf_url = result.get('pdfUrl', '') or result.get('pdf_url', '') or ''

            if pdf_url:
                sync.pdf_url = pdf_url
                sync.sync_status = 'PDF_READY'
                sync.save(update_fields=['pdf_url', 'sync_status', 'updated_at'])
                logger.info(f'[SitePro] PDF для инвойса {invoice.number}: {pdf_url}')

            return pdf_url

        except SiteProAPIError as e:
            logger.error(f'[SitePro] Ошибка получения PDF для инвойса {invoice.number}: {e}')
            return ''

    # ========================================================================
    # BULK OPERATIONS
    # ========================================================================

    def push_invoices(self, invoices) -> dict:
        """
        Отправляет несколько инвойсов в site.pro.
        
        Args:
            invoices: QuerySet или список NewInvoice
            
        Returns:
            dict с результатами: {'sent': int, 'skipped': int, 'failed': int, 'errors': list}
        """
        result = {
            'sent': 0,
            'skipped': 0,
            'failed': 0,
            'errors': [],
        }

        for invoice in invoices:
            try:
                push_result = self.push_invoice(invoice)
                if push_result.get('already_synced'):
                    result['skipped'] += 1
                else:
                    result['sent'] += 1
            except SiteProAPIError as e:
                result['failed'] += 1
                result['errors'].append(f'{invoice.number}: {str(e)[:200]}')

        logger.info(
            f'[SitePro] Bulk push: отправлено {result["sent"]}, '
            f'пропущено {result["skipped"]}, ошибок {result["failed"]}'
        )

        return result
