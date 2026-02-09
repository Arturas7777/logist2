"""
Сервис для работы с site.pro (b1.lt) Accounting API
====================================================

Реальный API: https://site.pro/My-Accounting/api/
Аутентификация: заголовок B1-Api-Key

Основные возможности:
- Создание/поиск клиентов (client/clients)
- Создание продаж / инвойсов (sale/sales)
- Добавление позиций (sale/sale-items)
- Получение PDF инвойсов
- Банковские операции (bank/sale-invoice/payment)

Документация: https://site.pro/My-Accounting/doc/api
"""

import requests
import logging
import json
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
    """
    Клиент для site.pro Accounting API.
    
    Аутентификация через B1-Api-Key заголовок.
    Base URL: https://site.pro/My-Accounting/api
    """

    # ─── Эндпоинты ───────────────────────────────────────────────────────
    # Продажи (инвойсы) — через warehouse module
    SALES_CREATE = '/warehouse/sales/create'
    SALES_UPDATE = '/warehouse/sales/update'
    SALES_LIST = '/warehouse/sales/list'
    SALES_DELETE = '/warehouse/sales/delete'
    SALE_ITEMS_CREATE = '/warehouse/sale-items/create'
    SALE_ITEMS_CREATE_SIMPLE = '/warehouse/sale-items/create-simple'
    SALE_ITEMS_LIST = '/warehouse/sale-items/list'
    SALE_ITEMS_UPDATE = '/warehouse/sale-items/update'
    SALE_ITEMS_DELETE = '/warehouse/sale-items/delete'
    SALE_INVOICE_GET = '/warehouse/invoices/get-sale'
    SALE_REPORT_GENERATE = '/warehouse/sale-reports/generate'

    # Клиенты
    CLIENTS_CREATE = '/clients/create'
    CLIENTS_UPDATE = '/clients/update'
    CLIENTS_LIST = '/clients/list'
    CLIENTS_DELETE = '/clients/delete'
    CLIENT_BALANCE = '/client/sales/balance'

    # Банковские операции
    BANK_SALE_PAYMENT = '/bank/sale-invoice/payment'

    # Товары/услуги (справочник)
    ITEMS_CREATE = '/reference-book/items/create'
    ITEMS_LIST = '/reference-book/items/list'

    # Файлы
    FILE_UPLOAD = '/account/file-storage/upload'

    # Справочники
    VAT_RATES_LIST = '/reference-book/vat-rates/list'
    CURRENCIES_LIST = '/reference-book/currencies/list'
    SERIES_LIST = '/reference-book/series/list'

    # E-commerce (альтернативный путь создания)
    ECOMMERCE_ORDERS_CREATE_SALE = '/e-commerce/orders/create-sale'

    def __init__(self, connection):
        """
        Args:
            connection: экземпляр SiteProConnection
        """
        self.connection = connection
        self.base_url = connection.base_url
        self._session = requests.Session()

    # ========================================================================
    # AUTHENTICATION — B1-Api-Key header
    # ========================================================================

    def _get_api_key(self) -> str:
        """Возвращает API ключ для заголовка B1-Api-Key."""
        api_key = self.connection.api_key
        if not api_key:
            raise SiteProAPIError(
                'API ключ не задан. Введите API raktas в настройках подключения site.pro.'
            )
        return api_key

    def _get_headers(self) -> dict:
        """Формирует стандартные заголовки для API запроса."""
        return {
            'B1-Api-Key': self._get_api_key(),
            'Content-Type': 'application/json',
        }

    # ========================================================================
    # HTTP METHODS
    # ========================================================================

    def _api_post(self, endpoint: str, json_data: dict = None) -> dict:
        """
        Выполняет POST-запрос к site.pro Accounting API.
        
        Args:
            endpoint: путь к API (например '/sale/sales/create')
            json_data: данные для отправки
            
        Returns:
            dict с ответом API
        """
        url = f'{self.base_url}{endpoint}'
        payload = json.dumps(json_data or {})

        try:
            resp = self._session.post(
                url,
                headers={
                    **self._get_headers(),
                    'Content-Length': str(len(payload)),
                },
                data=payload,
                timeout=30,
            )
            resp.raise_for_status()

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
                    body = e.response.text[:500] if e.response.text else None
            error_msg = f'API POST {endpoint} ошибка: {e}'
            if body:
                error_msg += f' | Response: {body}'
            logger.error(f'[SitePro] {error_msg}')
            raise SiteProAPIError(error_msg, status, body)

    # ========================================================================
    # TEST CONNECTION
    # ========================================================================

    def test_connection(self) -> dict:
        """
        Проверяет подключение к site.pro.
        Делает простой запрос к /sale/vat-rates/list для проверки.
        
        Returns:
            dict с результатом: success, error, details
        """
        result = {
            'success': False,
            'auth_method': 'B1-Api-Key',
            'error': None,
            'details': {},
        }

        api_key = self.connection.api_key
        if not api_key:
            result['error'] = 'API ключ не задан'
            return result

        try:
            # Попробуем получить список ставок НДС — простой запрос
            data = self._api_post(self.VAT_RATES_LIST, {
                'page': 1,
                'rows': 10,
            })

            result['success'] = True
            result['details'] = {
                'vat_rates_count': len(data.get('data', [])) if isinstance(data, dict) else 0,
                'response_keys': list(data.keys()) if isinstance(data, dict) else [],
            }

            # Обновляем статус подключения
            self.connection.last_error = ''
            self.connection.last_synced_at = timezone.now()
            self.connection.save(update_fields=['last_error', 'last_synced_at', 'updated_at'])

            logger.info(f'[SitePro] Подключение успешно. Ответ: {data}')

        except SiteProAPIError as e:
            result['error'] = str(e)
            self.connection.last_error = str(e)[:500]
            self.connection.save(update_fields=['last_error', 'updated_at'])

        return result

    # ========================================================================
    # CLIENT OPERATIONS
    # ========================================================================

    def search_clients(self, name: str = None, code: str = None) -> list:
        """
        Поиск клиентов в site.pro.
        
        Args:
            name: имя клиента (частичное совпадение)
            code: код компании (точное совпадение)
            
        Returns:
            список клиентов
        """
        rules = []
        if name:
            rules.append({'field': 'name', 'op': 'cn', 'data': name})
        if code:
            rules.append({'field': 'code', 'op': 'eq', 'data': code})

        data = {
            'page': 1,
            'rows': 50,
            'filters': {
                'groupOp': 'AND',
                'rules': rules,
            },
        }

        result = self._api_post(self.CLIENTS_LIST, data)
        return result.get('data', []) if isinstance(result, dict) else []

    def create_client(self, client) -> dict:
        """
        Создаёт клиента в site.pro.
        
        Args:
            client: экземпляр core.Client
            
        Returns:
            dict с данными созданного клиента
        """
        data = {
            'name': client.name or '',
            'code': getattr(client, 'company_code', '') or '',
            'vatCode': getattr(client, 'vat_code', '') or '',
            'address': getattr(client, 'address', '') or '',
            'email': getattr(client, 'email', '') or '',
            'phone': getattr(client, 'phone', '') or '',
        }

        # Удаляем пустые значения
        data = {k: v for k, v in data.items() if v}

        logger.info(f'[SitePro] Создание клиента: {client.name}')
        result = self._api_post(self.CLIENTS_CREATE, data)
        logger.info(f'[SitePro] Клиент создан: {result}')
        return result

    def get_or_create_client(self, client) -> int:
        """
        Находит или создаёт клиента в site.pro.
        
        Returns:
            ID клиента в site.pro
        """
        # Сначала ищем по коду компании
        company_code = getattr(client, 'company_code', '') or ''
        if company_code:
            existing = self.search_clients(code=company_code)
            if existing:
                return existing[0].get('id')

        # Ищем по имени
        existing = self.search_clients(name=client.name)
        if existing:
            return existing[0].get('id')

        # Создаём нового
        result = self.create_client(client)
        return result.get('id')

    # ========================================================================
    # INVOICE / SALE OPERATIONS
    # ========================================================================

    def push_invoice(self, invoice) -> dict:
        """
        Отправляет инвойс в site.pro как продажу (sale).
        
        Процесс:
        1. Находим или создаём клиента
        2. Создаём продажу (sale)
        3. Добавляем позиции (sale-items)
        
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

        try:
            # Шаг 1: Находим или создаём клиента
            client_id = None
            if invoice.recipient_client:
                try:
                    client_id = self.get_or_create_client(invoice.recipient_client)
                except SiteProAPIError as e:
                    logger.warning(f'[SitePro] Ошибка создания клиента, продолжаем без ID: {e}')

            # Шаг 2: Создаём продажу (sale)
            sale_data = self._build_sale_data(invoice, client_id)
            logger.info(
                f'[SitePro] Отправка инвойса {invoice.number} '
                f'(получатель: {invoice.recipient_name}, сумма: {invoice.total})'
            )
            sale_result = self._api_post(self.SALES_CREATE, sale_data)

            sale_id = sale_result.get('id') or sale_result.get('saleId')
            sale_number = sale_result.get('number') or sale_result.get('invoiceNumber') or ''

            if not sale_id:
                raise SiteProAPIError(
                    f'API не вернул ID продажи. Ответ: {sale_result}'
                )

            # Шаг 3: Добавляем позиции
            items_errors = []
            for item_data in self._build_sale_items(invoice, sale_id):
                try:
                    self._api_post(self.SALE_ITEMS_CREATE, item_data)
                except SiteProAPIError as e:
                    items_errors.append(str(e)[:200])
                    logger.error(f'[SitePro] Ошибка создания позиции: {e}')

            # Обновляем запись синхронизации
            sync.external_id = str(sale_id)
            sync.external_number = str(sale_number)
            sync.sync_status = 'SENT'
            sync.error_message = '; '.join(items_errors) if items_errors else ''
            sync.last_synced_at = timezone.now()
            sync.save()

            # Обновляем подключение
            self.connection.last_synced_at = timezone.now()
            self.connection.last_error = ''
            self.connection.save(update_fields=['last_synced_at', 'last_error', 'updated_at'])

            logger.info(
                f'[SitePro] Инвойс {invoice.number} успешно отправлен '
                f'(sale_id={sale_id}, items_errors={len(items_errors)})'
            )

            return {
                'success': True,
                'external_id': str(sale_id),
                'external_number': str(sale_number),
                'items_errors': items_errors,
                'response': sale_result,
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

    def _build_sale_data(self, invoice, client_id: int = None) -> dict:
        """
        Формирует данные для создания продажи в site.pro.
        
        Args:
            invoice: экземпляр NewInvoice
            client_id: ID клиента в site.pro (опционально)
        """
        sale_data = {
            'date': invoice.date.strftime('%Y-%m-%d') if invoice.date else timezone.now().strftime('%Y-%m-%d'),
            'currencyCode': self.connection.default_currency,
        }

        # Номер инвойса
        if invoice.number:
            sale_data['number'] = invoice.number

        # Серия
        if self.connection.invoice_series:
            sale_data['series'] = self.connection.invoice_series

        # Клиент
        if client_id:
            sale_data['clientId'] = client_id

        # Имя получателя
        if invoice.recipient_name:
            sale_data['clientName'] = invoice.recipient_name

        return sale_data

    def _build_sale_items(self, invoice, sale_id: int) -> list:
        """
        Формирует список позиций для добавления к продаже.
        
        Суммы в site.pro API хранятся как есть (не умножать на 100,
        это только для банковских операций).
        
        Args:
            invoice: экземпляр NewInvoice
            sale_id: ID продажи в site.pro
            
        Returns:
            список dict для каждой позиции
        """
        items = []
        vat_rate = float(self.connection.default_vat_rate)

        for item in invoice.items.all().select_related('car').order_by('order'):
            item_name = item.description or ''
            if item.car:
                item_name = f'{item.description} ({item.car.vin})'

            items.append({
                'saleId': sale_id,
                'name': item_name,
                'quantity': float(item.quantity),
                'price': float(item.unit_price),
                'vatPercent': vat_rate,
            })

        return items

    # ========================================================================
    # SEARCH SALES
    # ========================================================================

    def search_sales(self, number: str = None, date_from: str = None, date_to: str = None) -> list:
        """
        Поиск продаж в site.pro.
        
        Args:
            number: номер инвойса
            date_from: дата с (yyyy-MM-dd)
            date_to: дата по (yyyy-MM-dd)
        """
        rules = []
        if number:
            rules.append({'field': 'number', 'op': 'eq', 'data': number})
        if date_from:
            rules.append({'field': 'date', 'op': 'ge', 'data': date_from})
        if date_to:
            rules.append({'field': 'date', 'op': 'le', 'data': date_to})

        data = {
            'page': 1,
            'rows': 50,
            'filters': {
                'groupOp': 'AND',
                'rules': rules,
            },
        }

        result = self._api_post(self.SALES_LIST, data)
        return result.get('data', []) if isinstance(result, dict) else []

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
            result = self._api_post(self.SALE_PDF, {'id': int(sync.external_id)})

            pdf_url = result.get('url', '') or result.get('pdfUrl', '') or ''

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
    # REFERENCE DATA
    # ========================================================================

    def get_vat_rates(self) -> list:
        """Получает список ставок НДС из site.pro."""
        result = self._api_post(self.VAT_RATES_LIST, {'page': 1, 'rows': 100})
        return result.get('data', []) if isinstance(result, dict) else []

    def get_currencies(self) -> list:
        """Получает список валют из site.pro."""
        result = self._api_post(self.CURRENCIES_LIST, {'page': 1, 'rows': 100})
        return result.get('data', []) if isinstance(result, dict) else []

    def get_series(self) -> list:
        """Получает список серий нумерации из site.pro."""
        result = self._api_post(self.SERIES_LIST, {'page': 1, 'rows': 100})
        return result.get('data', []) if isinstance(result, dict) else []

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
