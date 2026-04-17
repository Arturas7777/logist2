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

import json
import logging

import requests
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
    WAREHOUSES_LIST = '/reference-book/warehouses/list'
    OPERATION_TYPES_LIST = '/reference-book/operation-types/list'
    COUNTRIES_LIST = '/reference-book/countries/list'

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

        API site.pro требует locationId (Tax Residency): 1=Lietuva, 2=EU, 3=3rd.
        Берётся из connection.default_location_id (по умолчанию 1=LT).

        Args:
            client: экземпляр core.Client

        Returns:
            dict с данными созданного клиента
        """
        location_id = self.connection.default_location_id or 1

        data = {
            'name': client.name or '',
            'code': getattr(client, 'company_code', '') or '',
            'vatCode': getattr(client, 'vat_code', '') or '',
            'address': getattr(client, 'address', '') or '',
            'email': getattr(client, 'email', '') or '',
            'phone': getattr(client, 'phone', '') or '',
            'locationId': location_id,
        }

        data = {k: v for k, v in data.items() if v not in (None, '')}

        logger.info(f'[SitePro] Создание клиента: {client.name} (locationId={location_id})')
        result = self._api_post(self.CLIENTS_CREATE, data)
        # Новый API: {'message': 'Data saved...', 'data': {'id': X}, 'code': 200}.
        # Нормализуем — если id нет на верхнем уровне, поднимем его из data.
        if not result.get('id') and isinstance(result.get('data'), dict):
            nested = result['data']
            result = {**result, **{k: v for k, v in nested.items() if k not in result}}
        logger.info(f'[SitePro] Клиент создан: id={result.get("id")}')
        return result

    def get_or_create_client(self, client) -> int:
        """
        Находит или создаёт клиента в site.pro.

        Порядок поиска:
        1. По company_code (точное совпадение) — самый надёжный.
        2. По полному имени (contains).
        3. По первому слову имени (чтобы "S-LINE Sergii Cherksasov (71544)"
           находил существующего "S-LINE" в site.pro).

        Только если ничего не найдено — создаётся новый клиент.

        Returns:
            ID клиента в site.pro (int)
        """
        company_code = (getattr(client, 'company_code', '') or '').strip()
        if company_code:
            existing = self.search_clients(code=company_code)
            if existing:
                logger.debug(f'[SitePro] Клиент найден по code={company_code}: id={existing[0].get("id")}')
                return existing[0].get('id')

        name = (client.name or '').strip()
        if name:
            existing = self.search_clients(name=name)
            if existing:
                logger.debug(f'[SitePro] Клиент найден по полному имени: id={existing[0].get("id")}')
                return existing[0].get('id')

            # Fallback: ищем по первому значимому слову имени (до пробела или скобки).
            # Это закрывает случай когда в Logist2 имя расширено доп. инфой
            # ("S-LINE Sergii Cherksasov (71544)"), а в site.pro компактное "S-LINE".
            import re
            first_token = re.split(r'[\s(]', name, maxsplit=1)[0].strip()
            if first_token and first_token != name and len(first_token) >= 2:
                existing = self.search_clients(name=first_token)
                if existing:
                    logger.info(
                        f'[SitePro] Клиент найден по первому слову {first_token!r}: '
                        f'id={existing[0].get("id")} (полное имя в site.pro: {existing[0].get("name")!r})'
                    )
                    return existing[0].get('id')

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
            # Шаг 1: Находим или создаём клиента (clientId обязателен в new API)
            client_id = None
            if invoice.recipient_client:
                client_id = self.get_or_create_client(invoice.recipient_client)

            if not client_id:
                raise SiteProAPIError(
                    f'Не удалось получить clientId для инвойса {invoice.number}. '
                    f'Проверьте связанного клиента (recipient_client={invoice.recipient_client_id}) '
                    f'и default_location_id в настройках подключения.'
                )

            # Шаг 2: Создаём продажу (sale)
            sale_data = self._build_sale_data(invoice, client_id)
            logger.info(
                f'[SitePro] Отправка инвойса {invoice.number} '
                f'(получатель: {invoice.recipient_name}, сумма: {invoice.total})'
            )
            sale_result = self._api_post(self.SALES_CREATE, sale_data)

            # Новый API возвращает: {'message': 'Data saved...', 'data': {'id': 197}, 'code': 200}
            # Старый API возвращал id/saleId на верхнем уровне — поддерживаем оба формата.
            sale_id = sale_result.get('id') or sale_result.get('saleId')
            sale_number = sale_result.get('number') or sale_result.get('invoiceNumber') or ''

            if not sale_id and isinstance(sale_result.get('data'), dict):
                sale_id = sale_result['data'].get('id') or sale_result['data'].get('saleId')
                sale_number = (sale_number
                               or sale_result['data'].get('number')
                               or sale_result['data'].get('invoiceNumber')
                               or '')

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

            sync.external_id = str(sale_id)
            sync.external_number = str(sale_number)
            sync.sync_status = 'PARTIAL' if items_errors else 'SENT'
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

    def _build_sale_data(self, invoice, client_id: int) -> dict:
        """
        Формирует данные для создания продажи в site.pro.

        Обязательные поля новой версии API:
        - saleDate, currencyCode, warehouseId, operationTypeId, clientId.

        warehouseId / operationTypeId / seriesId тянутся из SiteProConnection.
        Если default_warehouse_id или default_operation_type_id не заданы,
        будет выброшена SiteProAPIError — нужно задать их в админке.

        Args:
            invoice: экземпляр NewInvoice
            client_id: ID клиента в site.pro (обязательный)
        """
        if not self.connection.default_warehouse_id:
            raise SiteProAPIError(
                'default_warehouse_id не задан в настройках подключения site.pro. '
                'Используйте action "Загрузить справочники" в админке.'
            )
        if not self.connection.default_operation_type_id:
            raise SiteProAPIError(
                'default_operation_type_id не задан в настройках подключения site.pro. '
                'Используйте action "Загрузить справочники" в админке.'
            )

        sale_data = {
            'saleDate': (
                invoice.date.strftime('%Y-%m-%d')
                if invoice.date else timezone.now().strftime('%Y-%m-%d')
            ),
            'currencyCode': self.connection.default_currency,
            'warehouseId': self.connection.default_warehouse_id,
            'operationTypeId': self.connection.default_operation_type_id,
            'clientId': client_id,
        }

        if invoice.number:
            sale_data['number'] = invoice.number

        # Серия: шлём seriesId если настроен, иначе текстом через series.
        # site.pro принимает оба формата.
        if self.connection.default_series_id:
            sale_data['seriesId'] = self.connection.default_series_id
        if self.connection.invoice_series:
            sale_data['series'] = self.connection.invoice_series

        # Имя получателя дополнительно (site.pro подставит clientName из clientId,
        # но мы шлём расширенную версию из Logist2, если она длиннее).
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

            if not isinstance(result, dict):
                logger.warning(f'[SitePro] Неожиданный ответ API для PDF инвойса {invoice.number}: {type(result)}')
                return ''

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
        except (TypeError, ValueError, AttributeError) as e:
            logger.error(f'[SitePro] Ошибка обработки ответа PDF для инвойса {invoice.number}: {e}')
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

    def get_warehouses(self) -> list:
        """Получает список складов из site.pro."""
        result = self._api_post(self.WAREHOUSES_LIST, {'page': 1, 'rows': 100})
        return result.get('data', []) if isinstance(result, dict) else []

    def get_operation_types(self) -> list:
        """Получает список типов операций (isSale=True → подходит для инвойсов)."""
        result = self._api_post(self.OPERATION_TYPES_LIST, {'page': 1, 'rows': 100})
        return result.get('data', []) if isinstance(result, dict) else []

    def get_countries(self) -> list:
        """Получает список стран (для locationId клиентов).

        Возвращает ПЕРВУЮ страницу (100 стран). Список длинный, но первая
        страница обычно содержит основные EU-страны. Для полного списка
        используйте list_all_countries().
        """
        result = self._api_post(self.COUNTRIES_LIST, {'page': 1, 'rows': 100})
        return result.get('data', []) if isinstance(result, dict) else []

    def list_all_countries(self) -> list:
        """Paginate through all countries (254+ записей в base справочнике)."""
        return self._paginate_list(self.COUNTRIES_LIST, rows=100)

    # ========================================================================
    # PULL DATA (import from site.pro)
    # ========================================================================

    # site.pro API валидирует `rows` в фиксированном списке значений.
    # Только эти значения допустимы для list-эндпоинтов.
    _VALID_ROWS = (10, 20, 25, 50, 100)

    def _paginate_list(self, endpoint: str, filters: dict = None, max_pages: int = 100,
                       rows: int = 50) -> list:
        """Paginate through a list endpoint collecting all records.

        Args:
            endpoint: API path
            filters: optional jqGrid-style filters dict
            max_pages: safety cap to avoid infinite loops
            rows: page size; must be one of _VALID_ROWS (10/20/25/50/100).
                  Использует 50 по умолчанию — баланс между количеством запросов
                  и размером ответа.
        """
        if rows not in self._VALID_ROWS:
            rows = 50
        all_data = []
        page = 1
        while page <= max_pages:
            payload = {'page': page, 'rows': rows}
            if filters:
                payload['filters'] = filters
            result = self._api_post(endpoint, payload)
            if not isinstance(result, dict):
                break
            data = result.get('data', [])
            all_data.extend(data)
            total_pages = result.get('pages', 1)
            if page >= total_pages:
                break
            page += 1
        return all_data

    def list_all_clients(self) -> list:
        """Fetch all clients from site.pro."""
        return self._paginate_list(self.CLIENTS_LIST)

    def list_all_sales(self, date_from: str = None, date_to: str = None) -> list:
        """Fetch all sales/invoices from site.pro, optionally filtered by date range."""
        rules = []
        if date_from:
            rules.append({'field': 'date', 'op': 'ge', 'data': date_from})
        if date_to:
            rules.append({'field': 'date', 'op': 'le', 'data': date_to})
        filters = {'groupOp': 'AND', 'rules': rules} if rules else None
        return self._paginate_list(self.SALES_LIST, filters)

    def list_sale_items(self, sale_id: int) -> list:
        """Fetch line items for a specific sale.

        Использует пагинацию, потому что API валидирует rows по фиксированному
        списку {10,20,25,50,100}, и у больших продаж может быть > 100 позиций.
        """
        return self._paginate_list(
            self.SALE_ITEMS_LIST,
            filters={
                'groupOp': 'AND',
                'rules': [{'field': 'saleId', 'op': 'eq', 'data': str(sale_id)}],
            },
            rows=100,
        )

    def get_client_balance(self, client_id: int) -> dict:
        """Fetch balance for a specific client in site.pro."""
        result = self._api_post(self.CLIENT_BALANCE, {'clientId': client_id})
        return result if isinstance(result, dict) else {}

    BANK_TRANSACTIONS_LIST = '/bank/transactions/list'

    def list_bank_transactions(self) -> list:
        """Fetch all bank transactions from site.pro (uses pageSize=10, fixed by API)."""
        all_data = []
        page = 1
        while page <= 200:
            result = self._api_post(self.BANK_TRANSACTIONS_LIST, {
                'page': page, 'pageSize': 10,
            })
            if not isinstance(result, dict):
                break
            data = result.get('data', [])
            all_data.extend(data)
            if page >= result.get('pages', 1):
                break
            page += 1
        return all_data

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
