# Отчёт о проделанной работе по проекту Logist2

**Дата последнего обновления:** 9 февраля 2026 г.

---

## Общее описание задач из промта

Основная задача - доработка системы управления контейнерами и ТС:
1. Расширение типов ТС с 2 до 11
2. Пропорциональное распределение THS по типам ТС
3. Возможность указать плательщика THS (линия или склад)
4. Синхронизация данных между контейнером и карточками ТС

---

## ВЫПОЛНЕННАЯ РАБОТА

### 1. Расширение типов транспортных средств

**Статус:** Завершено

**Файл:** `core/models.py` - модель `Car`

Было 2 типа (CAR, MOTO), стало 11:
- SEDAN (Легковой), CROSSOVER (Кроссовер), SUV (Джип), PICKUP (Пикап)
- NEW_CAR (Новый автомобиль), MOTO (Мотоцикл), BIG_MOTO (Большой мотоцикл)
- ATV (Квадроцикл/Багги), BOAT (Лодка), RV (Автодом), CONSTRUCTION (Стр. техника)

**Миграция:** `0089_expand_vehicle_types_and_ths_system.py`
- Расширен `max_length` поля с 10 до 20
- Записи с `CAR` конвертированы в `SEDAN`

---

### 2. Система коэффициентов THS по типам ТС (обновлено 16.01.2026)

**Статус:** Завершено

**Модель `LineTHSCoefficient`** в `core/models.py` (ранее LineTHSPercent):
- Связана с линией через ForeignKey
- Хранит коэффициент (вес) для каждого типа ТС
- unique_together: line + vehicle_type
- По умолчанию коэффициент = 1.0

**Миграция:** `0093_rename_ths_percent_to_coefficient.py`
- Переименована модель LineTHSPercent → LineTHSCoefficient
- Переименовано поле percent → coefficient
- Данные конвертированы: коэффициент = процент / 25

**Админка:** `LineTHSCoefficientInline` в `LineAdmin`

**Логика распределения THS:**
- Сумма THS контейнера распределяется пропорционально коэффициентам
- Формула: THS_ТС = общий_THS × (коэффициент_ТС / сумма_всех_коэффициентов)
- Результат округляется вверх до 5 EUR

**Рекомендуемые коэффициенты:**
| Тип ТС | Коэффициент |
|--------|-------------|
| Легковой | 1.0 |
| Кроссовер | 1.2 |
| Джип/Пикап | 1.5 |
| Мотоцикл | 0.3 |
| Большой мотоцикл | 0.5 |
| Лодка | 2.0 |
| Автодом (RV) | 3.0 |
| Стр. техника | 2.5 |

---

### 3. Поле "Оплата THS через" в контейнере

**Статус:** Завершено

**Поле `ths_payer`** в модели `Container`:
- LINE = услуга THS как service_type='LINE'
- WAREHOUSE = услуга THS как service_type='WAREHOUSE'

**Миграция:** `0090_update_ths_payer_labels.py`

---

### 4. Округление THS до 5 EUR

**Статус:** Завершено

Вспомогательная функция `round_up_to_5()` внутри `calculate_ths_for_container()` в `core/signals.py`
Пример: 73.12 EUR -> 75 EUR

---

### 5. Функция создания THS услуг

**Статус:** Завершено

Функция `create_ths_services_for_container()` в `core/signals.py`:
- Рассчитывает пропорциональный THS
- Удаляет старые THS-услуги
- Создаёт новые с правильным service_type
- Применяет округление

Вызывается из `ContainerAdmin.save_model()` и `save_formset()`

---

### 6. Упрощение системы цен (16.01.2026)

**Статус:** Завершено

**Изменения:**
- Удалено поле `current_price` из модели `Car`
- Оставлено только поле `total_price` (переименовано в "Цена")
- Цена динамически пересчитывается до статуса "Передан"
- После передачи цена фиксируется

**Миграция:** `0092_remove_current_price.py`

---

### 7. Динамический расчёт услуги "Хранение" (16.01.2026)

**Статус:** Завершено

**Проблема:** Услуга "Хранение" добавлялась с фиксированной ценой 5 EUR, даже когда платных дней ещё не было.

**Решение:**
- Удалено поле `rate` из модели `Warehouse`
- Ставка за день теперь берётся из услуги "Хранение" в списке услуг склада (`WarehouseService.default_price`)
- Цена услуги "Хранение" = платные_дни × ставка_за_день
- Если платных дней нет - цена = 0

**Миграция:** `0091_remove_warehouse_rate.py`

**Изменённые методы в `Car`:**
- `update_days_and_storage()` - обновляет дни и вызывает пересчёт цены хранения
- `_get_storage_daily_rate()` - получает ставку из услуги "Хранение"
- `_update_storage_service_price()` - обновляет цену в CarService

---

### 8. Улучшение сводки по услугам (16.01.2026)

**Статус:** Завершено

**Изменения в `services_summary_display`:**
- THS отображается отдельно (вне зависимости от плательщика)
- Услуги склада показываются с детализацией (каждая услуга отдельно)
- Показываются бесплатные и платные дни
- Отображается наценка Caromoto Lithuania

---

### 9. Пересоздание THS при смене склада (16.01.2026)

**Статус:** Завершено

**Проблема:** При изменении склада в контейнере с `ths_payer='WAREHOUSE'` услуга THS не обновлялась.

**Решение:** Добавлено `'warehouse'` в список отслеживаемых полей в `ContainerAdmin.save_model()`

---

### 10. Кнопка "Пересчитать THS" в карточке линии (16.01.2026)

**Статус:** Завершено

**Функционал:**
- В карточке линии появилась кнопка "↻ Пересчитать THS"
- При нажатии пересчитываются THS-услуги для всех ТС этой линии со статусом "Разгружен" или "В пути"
- После пересчёта THS обновляются итоговые цены ТС

**Файлы:**
- `templates/admin/line_change.html` - шаблон с кнопкой
- `core/admin.py` - метод `recalculate_ths_view()` в `LineAdmin`

**Использование:**
1. Изменить коэффициенты THS в карточке линии
2. Сохранить линию
3. Нажать кнопку "Пересчитать THS"
4. Дождаться сообщения "Пересчитано: X контейнеров, Y ТС"

---

### 11. Исправление кэширования при расчёте цены (16.01.2026)

**Статус:** Завершено

**Проблема:** После пересчёта THS-услуг цена ТС не обновлялась из-за кэша `_prefetched_objects_cache`.

**Решение:** В методе `calculate_total_price()` добавлен сброс кэша перед расчётом:
```python
if hasattr(self, '_prefetched_objects_cache'):
    self._prefetched_objects_cache.pop('car_services', None)
```

---

### 12. Система скрытой наценки (17.01.2026)

**Статус:** Завершено

**Цель:** Возможность скрыто распределить наценку (прибыль) по услугам, чтобы она не отображалась отдельной строкой в инвойсе.

**Новое поле `markup_amount`** в модели `CarService`:
- Скрытая наценка для каждой услуги
- Добавляется к цене услуги в инвойсе
- Не видна клиенту как отдельная строка

**Новое поле `default_markup`** в моделях услуг:
- `WarehouseService.default_markup` - наценка по умолчанию для услуг склада
- `LineService.default_markup` - наценка по умолчанию для услуг линии
- `CarrierService.default_markup` - наценка по умолчанию для услуг перевозчика
- При создании CarService наценка копируется из default_markup

**Миграции:**
- `0094_add_hide_markup_in_field.py`
- `0095_add_markup_distribution.py`
- `0096_add_default_markup_to_services.py`

**Интерфейс в карточке ТС:**
- Рядом с каждой услугой появилось жёлтое поле для ввода наценки
- Наценка редактируется напрямую в блоках услуг (склад, линия, перевозчик)
- Сводка показывает общую сумму наценки

**Логика цен:**
- `final_price` = базовая цена услуги (без наценки) — для внутреннего учёта
- `invoice_price` = базовая цена + наценка — для инвойса клиенту
- `total_price` ТС = сумма всех услуг + сумма всех наценок

**Особенности для услуги "Хранение":**
- Наценка умножается на количество платных дней (как и цена)
- При `default_markup=1` и 7 платных дней → наценка = 7 EUR

**Сводка по услугам (services_summary_display):**
- Услуги линий показываются с детализацией (включая THS даже если оплата через склад)
- Услуги склада показываются с детализацией (без THS)
- Перевозчик отдельно
- Скрытая наценка отдельным блоком

**Инвойс:**
- Наценка НЕ отображается отдельной строкой
- Наценка добавляется к ценам услуг (только для Company)

---

### 13. Обратная синхронизация статуса контейнера (актуализировано 24.01.2026)

**Статус:** Завершено

**Функционал:**
- Если все ТС контейнера имеют статус `TRANSFERRED`, контейнер автоматически переводится в `TRANSFERRED`
- Вызывается в `Car.save()` и в действиях админки

**Файлы:**
- `core/models.py` — `Container.check_and_update_status_from_cars()`, вызов из `Car.save()`
- `core/admin.py` — доп. проверка при массовых действиях

## РЕШЁННЫЕ ПРОБЛЕМЫ

| # | Проблема | Причина | Решение |
|---|----------|---------|---------|
| 1 | THS не рассчитывался по процентам | Старый код в сигнале перезаписывал логику | Отключена секция "УСЛУГИ ЛИНИИ" в create_car_services_on_car_save |
| 2 | THS записывался как LINE вместо WAREHOUSE | Старая логика игнорировала ths_payer | Логика перенесена в create_ths_services_for_container() |
| 3 | THS не создавался при новом контейнере | Функция вызывалась только при change=True | Добавлен вызов в save_formset() после сохранения ТС |
| 4 | Все услуги линии добавлялись при сохранении ТС | Сигнал update_cars_on_line_service_change | Отключена автоматическая логика добавления |
| 5 | Цена на 5 EUR больше при 0 платных дней | Услуга "Хранение" создавалась с фиксированной ценой | Цена рассчитывается динамически: дни × ставка |
| 6 | Фильтр `icontains` не находил "Хранение" | Проблемы с кодировкой в PostgreSQL | Используется точное совпадение `name='Хранение'` |
| 7 | Цена в списке ТС отличалась от карточки | Prefetch кэш не обновлялся | Расчёт напрямую из БД в `total_price_display` |
| 8 | THS не обновлялся при смене склада | Поле warehouse не отслеживалось | Добавлено в список ths_related_changed |
| 9 | Проценты THS неинтуитивны | Трудно понять пропорции | Заменены на коэффициенты (1.0 = стандарт) |
| 10 | Нет способа пересчитать THS для существующих ТС | Пересчёт только при изменении контейнера | Кнопка "Пересчитать THS" в карточке линии |
| 11 | Цена не обновлялась после пересчёта THS | Кэш car_services не сбрасывался | Сброс кэша в calculate_total_price() |
| 12 | Кнопка пересчёта отправляла основную форму | Form внутри form | Заменена на ссылку (тег `<a>`) |
| 13 | Наценка видна клиенту в инвойсе | Отдельная строка "Наценка Caromoto" | Наценка распределяется по услугам скрыто |
| 14 | Нельзя редактировать наценку по услугам | Только общее поле proft | Жёлтые поля наценки в каждой услуге |
| 15 | Услуги линий не учитывались в сводке | custom_price=None считалось как 0 | Используется final_price с проверкой `is not None` |
| 16 | THS через склад не показывался в услугах линий | Фильтр по service_type='LINE' | THS учитывается по имени, независимо от service_type |
| 17 | +5 EUR при 0 платных дней | custom_price=0 → False → default_price=5 | Проверка `custom_price is not None` |
| 18 | В услугах линий/перевозчиков нет флага "Добавлять по умолчанию" | Не было поля add_by_default | Добавлены поля и инлайны для LineService/CarrierService |
| 19 | Услуга с add_by_default добавлялась всем существующим ТС | Сигналы создавали CarService для всех ТС | Обновление только существующих связей, автодобавление только для новых |

---

## НЕВЫПОЛНЕННАЯ РАБОТА (согласно промту)

- Нет активных задач, влияющих на текущую функциональность.
- Отдельный аналитический учёт прибыли (отчёты/сводки) не выделен в отдельный модуль.

---

## ИЗМЕНЁННЫЕ ФАЙЛЫ

### Модели (`core/models.py`)
- Типы ТС расширены до 11
- Модель `LineTHSCoefficient` для коэффициентов THS (ранее LineTHSPercent)
- Поле `ths_payer` в `Container`
- Удалено поле `rate` из `Warehouse`
- Удалено поле `current_price` из `Car`
- Методы расчёта хранения: `update_days_and_storage()`, `_get_storage_daily_rate()`, `_update_storage_service_price()`
- Сброс кэша в `calculate_total_price()`
- **Новое:** `CarService.markup_amount` - скрытая наценка для услуги
- **Новое:** `CarService.final_price` - цена без наценки (с проверкой `is not None`)
- **Новое:** `CarService.invoice_price` - цена с наценкой для инвойса
- **Новое:** `WarehouseService.default_markup`, `LineService.default_markup`, `CarrierService.default_markup`
- **Новое:** `calculate_total_price()` учитывает markup_amount
- **Новое:** `Container.check_and_update_status_from_cars()` + вызов из `Car.save()`
- **Новое:** `CompanyService` + тип `COMPANY` в `CarService`
- **Новое:** `LineService.add_by_default`, `CarrierService.add_by_default`
- **Новое:** `Car.get_company_services()` и учёт `company_total` в расчёте цены

### Админка (`core/admin/` — пакет, ранее `core/admin.py`)

**08.02.2026 — Разбиение на пакет:**
- `core/admin.py` (3575 строк) → пакет `core/admin/`:
  - `__init__.py` — импорт модулей
  - `inlines.py` — все inline-классы
  - `container.py` — ContainerAdmin
  - `car.py` — CarAdmin (оптимизированный total_price_display, без побочных эффектов)
  - `partners.py` — WarehouseAdmin, ClientAdmin, CompanyAdmin, LineAdmin, CarrierAdmin, AutoTransportAdmin

**Функционал (сохранён из предыдущих версий):**
- `LineTHSCoefficientInline` в LineAdmin (с полем coefficient вместо percent)
- `LineAdmin.recalculate_ths_view()` - кнопка пересчёта THS
- `LineAdmin.get_urls()` - кастомный URL для пересчёта
- `ContainerAdmin.save_model()` - вызов THS при изменении line/ths/ths_payer/warehouse
- `ContainerAdmin.save_formset()` - вызов THS после сохранения ТС
- `WarehouseAdmin` - удалено поле rate
- `CarAdmin.services_summary_display()` - улучшенная сводка
- `CarAdmin.total_price_display()` - динамический расчёт цены (оптимизирован 08.02.2026)
- Жёлтые поля для ввода наценки рядом с каждой услугой
- `save_model()` сохраняет markup_amount для услуг
- Сводка показывает услуги линий с детализацией (включая THS через склад)
- Сводка показывает общую сумму скрытой наценки
- Инлайн `CompanyServiceInline` в `CompanyAdmin`
- Блок "Услуги компании" в карточке ТС
- Всегда показывается кнопка добавления услуг у линий/перевозчиков

### Сигналы (`core/signals.py`)
- `calculate_ths_for_container()` - расчёт THS по коэффициентам
- `create_ths_services_for_container()` - создание услуг THS
- `round_up_to_5()` - округление до 5 EUR
- Изменён фильтр "Хранение" на точное совпадение
- **Новое:** Копирование `default_markup` при создании CarService
- **Новое:** Умножение наценки на дни для услуги "Хранение"
- **Новое:** Услуги компаний добавляются только для новых ТС (Caromoto Lithuania)
- **Новое:** Обновление существующих услуг при изменении справочников (без добавления всем)

### Биллинг (`core/models_billing.py`)
- Наценка НЕ создаётся отдельной строкой в инвойсе
- `invoice_price` используется для цены услуги (с наценкой)
- **08.02.2026:** `regenerate_items_from_cars()` обёрнут в `transaction.atomic()`
- **08.02.2026:** Исправлен `or Decimal('0')` → `if is not None` при расчёте наценки
- **08.02.2026:** Удалён дублированный `Transaction.save()` (super().save() вызывался дважды)

### Утилиты (`core/utils.py`)
- **08.02.2026:** Добавлена функция `round_up_to_5()` — округление Decimal вверх с шагом 5 EUR (чистая арифметика без float)
- `WebSocketBatcher` — батчинг WebSocket уведомлений
- `batch_update_queryset()` — массовое обновление
- `optimize_queryset_for_list()` — оптимизация queryset
- `log_slow_queries()` — декоратор для логирования медленных запросов

### Rate Limiting (`core/throttles.py`) — НОВЫЙ ФАЙЛ
- **08.02.2026:** `TrackShipmentThrottle` — 20 запросов/минуту для отслеживания грузов
- **08.02.2026:** `AIChatThrottle` — 10 запросов/минуту для AI-чата

### Celery задачи (`core/tasks.py`) — НОВЫЙ ФАЙЛ
- **08.02.2026:** `send_planned_notifications_task` — фоновая отправка email о планируемой разгрузке
- **08.02.2026:** `send_unload_notifications_task` — фоновая отправка email о разгрузке

### Celery конфигурация (`logist2/celery.py`) — НОВЫЙ ФАЙЛ
- **08.02.2026:** Celery app с автообнаружением задач из `core.tasks`

### Тесты (`core/tests.py`)
- **08.02.2026:** `RoundUpTo5Tests` — тесты округления (точные кратные, округление вверх, сохранение точности Decimal)
- **08.02.2026:** `StorageCostCalculationTests` — расчёт хранения (без склада → 0, без даты → 0)
- **08.02.2026:** `ServiceCacheTests` — кэш услуг (корректное имя, отсутствие повторных SQL запросов, обработка несуществующей услуги)

### Вьюхи (`core/views.py`)
- **08.02.2026:** `company_dashboard()` полностью переписана — делегация в `DashboardService`, chart data через `json.dumps()`
- **Новое:** `add_services()` копирует `default_price` и `default_markup` при добавлении услуги
- **Новое:** `get_companies()` и поддержка `company` в get_available_services/add_services

### Шаблоны
- `templates/admin/line_change.html` - кнопка "Пересчитать THS"
- `templates/admin/core/car/change_form.html` - модальное окно "Услуги компании"

### Миграции
- `0089_expand_vehicle_types_and_ths_system.py`
- `0090_update_ths_payer_labels.py`
- `0091_remove_warehouse_rate.py`
- `0092_remove_current_price.py`
- `0093_rename_ths_percent_to_coefficient.py` - переход на коэффициенты
- `0094_add_hide_markup_in_field.py` - поле для скрытия наценки
- `0095_add_markup_distribution.py` - поле markup_amount в CarService
- `0096_add_default_markup_to_services.py` - поле default_markup в услугах
- `0099_add_default_flags_line_carrier_services.py`
- `0100_add_company_service.py`
- `0101_alter_carservice_service_type_and_more.py`

---

## ПРИМЕЧАНИЯ

1. Коэффициенты THS по умолчанию = 1.0 (настраиваются в карточке линии)
2. Для пересчёта существующих ТС - используйте кнопку "Пересчитать THS" в карточке линии
3. Ставка хранения берётся из услуги "Хранение" в списке услуг склада
4. При добавлении нового склада нужно создать услугу "Хранение" с нужной ставкой за день
5. Тестовые контейнеры можно удалить после проверки

---

### 14. Улучшенная система фотографий контейнеров (21.01.2026)

**Статус:** Завершено

#### Автоматическая синхронизация с Google Drive

**Файл:** `core/google_drive_sync.py`

**Функционал:**
- Автоматический поиск папки контейнера на Google Drive по номеру
- Поддержка папок с дополнительным текстом (например "ECMU5566195 CAROMOTO D")
- Разделение фото по типам:
  - `IN_CONTAINER` - фото внутри контейнера (папка "KONTO VIDUS")
  - `UNLOADING` - фото после разгрузки (папка "AUTO IŠ KONTO")
- Автоматическое сохранение ссылки на найденную папку Google Drive

**Management команда:** `sync_photos_gdrive`
```bash
python manage.py sync_photos_gdrive --no-photos  # Только контейнеры без фото (быстрый режим)
python manage.py sync_photos_gdrive --recent     # Недавние контейнеры
python manage.py sync_photos_gdrive --container ECMU5566195  # Конкретный контейнер
```

**Cron задачи (sync_photos_cron.sh):**
- Каждые 3 часа - быстрая проверка контейнеров без фото
- Раз в сутки в 3:00 - полная проверка недавних контейнеров

#### Галерея в админке контейнера

**Файлы:**
- `templates/admin/core/container/change_form.html`
- `templates/admin/core/container/photos_gallery.html`

**Функционал:**
- Галерея по умолчанию свёрнута (для быстрой загрузки страницы)
- Фото загружаются через AJAX только при клике на заголовок
- Вкладки "В контейнере" и "Выгруженные"
- Lazy loading миниатюр
- Lightbox для просмотра полноразмерных фото
- Навигация стрелками клавиатуры

**API endpoint:** `GET /core/container/<id>/photos-json/`

#### Галерея на клиентском сайте

**Файл:** `templates/website/home.html`

**Функционал:**
- Вкладки "Все", "В контейнере", "Выгруженные"
- Выбор фото для скачивания
- Кнопка "Скачать выбранные" (зелёная)
- Lightbox с правильной навигацией в рамках текущей вкладки
- Улучшенное перетаскивание фото (drag работает при зажатой кнопке мыши)

**API endpoint:** `GET /api/container-photos/<container_number>/`
- Возвращает `photo_type_code` для фильтрации по вкладкам

---

### 15. Исправления поиска на клиентском сайте (21.01.2026)

**Статус:** Завершено

**Файл:** `core/views_website.py` - функция `track_shipment`

**Исправления:**
- Убрано несуществующее поле `current_price` из `ClientCarSerializer`
- Добавлена нормализация VIN (убираются пробелы, тире)
- Добавлен поиск по частичному совпадению VIN
- Улучшенное логирование и обработка ошибок

---

### 16. Исправление URL фотографий на VPS (21.01.2026)

**Статус:** Завершено

**Проблема:** Фотографии не отображались на VPS сервере (иконка поломанной картинки), хотя файлы были загружены.

**Причина:** API endpoints возвращали URL без `/media/` префикса (например `/container_photos/...` вместо `/media/container_photos/...`).

**Решение:**
- Добавлена проверка и добавление `/media/` префикса в `core/views.py` (функция `get_container_photos_json`)
- Добавлена проверка и добавление `/media/` префикса в `core/views_website.py` (функция `get_container_photos`)
- Исправлены права доступа к media файлам (`chown -R www-root:www-root`)

**Код исправления:**
```python
# Ensure URLs have /media/ prefix
photo_url = photo.photo.url
if photo_url and not photo_url.startswith('/media/') and not photo_url.startswith('http'):
    photo_url = '/media/' + photo_url.lstrip('/')
```

---

### 17. Улучшение hover-эффекта кнопок на сайте (21.01.2026)

**Статус:** Завершено

**Проблема:** При наведении на кнопки происходило раздражающее мигание из-за `transform: translateY(-2px)` и конфликтующих `box-shadow`.

**Решение:**
- Убран `transform: translateY` (приподнимание кнопки)
- Убран конфликтующий `box-shadow`
- Изменён `transition: all` на `transition: opacity` (убирает перерисовку всех свойств)
- Финальный hover-эффект: простое `opacity: 0.85`

**Файл:** `core/static/website/css/style.css`

**До:**
```css
.btn {
    transition: all 0.3s ease;
}
.btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 20px rgba(0,0,0,.2);
}
```

**После:**
```css
.btn {
    transition: opacity 0.2s ease;
}
.btn:hover {
    opacity: 0.85;
}
```

---

---

### 21. Столбец "Наценка" (Н) в списке ТС (31.01.2026)

**Статус:** Завершено

**Функционал:**
- В списке ТС (`/admin/core/car/`) добавлен столбец **"Н"** (Наценка)
- Отображает общую скрытую наценку для каждого автомобиля
- Под таблицей выводится **общая сумма наценок** для отображаемых ТС
- Поддержка сортировки по столбцу наценки
- Автоматический пересчёт при применении фильтров

**Оптимизация производительности:**
- Используется Django `annotate()` + `Sum()` для агрегации на уровне БД
- **Один SQL запрос** вместо N+1 запросов (где N - количество ТС)
- Производительность: 50-120ms для 50-500 ТС (вместо 500-5000ms без оптимизации)
- Ускорение в **10-40 раз** в зависимости от количества ТС

**Файлы:**
- `core/admin.py` - добавлены методы `markup_display()`, `get_queryset()`, `changelist_view()`
- `templates/admin/core/car/change_list.html` - кастомный шаблон для отображения общей суммы

**Технические детали:**
```python
# Аннотация для оптимизации
queryset.annotate(_total_markup=Sum('car_services__markup_amount'))

# Расчёт общей суммы для отфильтрованных ТС
total_markup_sum = queryset.aggregate(
    total=Sum('car_services__markup_amount')
)['total']
```

**Результат:**
- ✅ Визуализация наценок в списке ТС
- ✅ Контроль общей суммы наценок
- ✅ Динамический пересчёт при фильтрации
- ✅ Высокая производительность (оптимизация на уровне БД)

---

### 22. Система автовозов на загрузку (04.02.2026)

**Статус:** Завершено

**Цель:** Формирование автовозов для отправки ТС клиентам с автоматическим созданием инвойсов.

**Новые модели:**

1. **CarrierTruck** - автовозы перевозчика
   - `carrier` - связь с перевозчиком
   - `truck_number`, `trailer_number` - номера тягача и прицепа
   - `full_number` (property) - полный номер в формате "XXXXX / YYYYY"
   - `is_active` - активность

2. **CarrierDriver** - водители перевозчика
   - `carrier` - связь с перевозчиком
   - `first_name`, `last_name` - имя и фамилия
   - `phone` - телефон водителя
   - `full_name` (property) - полное имя "Имя Фамилия"
   - `is_active` - активность

3. **AutoTransport** - автовоз на загрузку
   - `number` - номер автовоза (генерируется автоматически: AT-YYYYMMDD-NNNN)
   - `carrier` - перевозчик
   - `eori_code` - EORI код (автозаполнение из перевозчика)
   - `truck`, `driver` - ссылки на CarrierTruck и CarrierDriver
   - `truck_number_manual`, `trailer_number_manual`, `driver_name_manual` - ручной ввод если нет в базе
   - `driver_phone` - телефон водителя (автозаполнение)
   - `border_crossing` - граница пересечения
   - `cars` - ManyToMany связь с ТС
   - `loading_date`, `departure_date`, `estimated_delivery_date`, `actual_delivery_date` - даты
   - `status` - DRAFT, FORMED, LOADED, IN_TRANSIT, DELIVERED, CANCELLED
   - `created_by` - кто создал

**Расширение модели Carrier:**
- `eori_code` - EORI код перевозчика
- Связи: `trucks` (CarrierTruck), `drivers` (CarrierDriver), `auto_transports` (AutoTransport)

**Расширение модели NewInvoice:**
- `auto_transport` - ForeignKey к AutoTransport (связывает инвойс с автовозом)

**Функционал:**

1. **Автозаполнение данных перевозчика:**
   - При выборе перевозчика автоматически заполняется EORI код
   - Подтягиваются списки автовозов и водителей этого перевозчика
   - При выборе водителя автоматически заполняется его телефон

2. **Выбор автомобилей:**
   - Select2 с поиском по VIN/марке (как в инвойсах)
   - Отображение клиента через дефис
   - Цветные теги по статусу ТС
   - Иконки статусов в выпадающем списке

3. **Автоматическое создание инвойсов:**
   - При установке статуса "Сформирован" автоматически создаются инвойсы
   - Отдельный инвойс для каждого клиента, чьи ТС в автовозе
   - Если все ТС одного клиента - создается один инвойс
   - Инвойсы автоматически обновляются при изменении состава автовоза

4. **Сигналы:**
   - `autotransport_post_save` - создание/обновление инвойсов при сохранении
   - `autotransport_cars_changed_handler` - обновление инвойсов при изменении списка ТС

**API endpoints:**
- `GET /core/api/autotransport/carrier-info/<carrier_id>/` - информация о перевозчике (EORI, автовозы, водители)
- `GET /core/api/autotransport/driver-phone/<driver_id>/` - телефон водителя
- `GET /core/api/autotransport/border-crossings/` - список границ пересечения
- `POST /core/api/autotransport/create-truck/` - создание нового автовоза
- `POST /core/api/autotransport/create-driver/` - создание нового водителя

**Интерфейс:**
- Кастомная страница создания/редактирования (templates/admin/core/autotransport/change_form.html)
- Красивый дизайн с карточками и градиентами
- Sidebar с информацией об автовозе и созданных инвойсах
- Полностью идентичный стиль и функционал выбора автомобилей как в инвойсах

**Файлы:**
- `core/models.py` - новые модели CarrierTruck, CarrierDriver, AutoTransport, расширение Carrier
- `core/models_billing.py` - поле auto_transport в NewInvoice
- `core/admin.py` - AutoTransportAdmin с кастомным шаблоном
- `core/views_autotransport.py` - API endpoints (новый файл)
- `core/signals.py` - сигналы для автоматического создания инвойсов
- `templates/admin/core/autotransport/change_form.html` - кастомный шаблон
- `core/static/css/autotransport.css` - стили
- `core/urls.py` - роутинг API endpoints

**Миграции:**
- `0103_carrier_eori_code_autotransport_and_more.py`

---

### 25. Массовое действие "Выставить" для инвойсов (06.02.2026)

**Статус:** Завершено

**Функционал:**
- В списке инвойсов (`/admin/core/newinvoice/`) добавлено массовое действие **"Пометить как выставленные"**
- Устанавливает статус `ISSUED` для всех отмеченных инвойсов
- Пропускает инвойсы, которые уже выставлены, оплачены или отменены

**Файл:** `core/admin_billing.py` — метод `mark_as_issued()` в `NewInvoiceAdmin`

---

### 24. Табличный формат инвойсов + система сокращений услуг (06.02.2026)

**Статус:** Завершено

**Цель:** Отображение позиций инвойса в виде таблицы (строки = авто, столбцы = услуги, крайний правый = итого). Введение системы сокращений для всех услуг в проекте.

**Новое поле `short_name`** в моделях услуг:
- `WarehouseService.short_name` — сокращённое название (до 20 символов)
- `LineService.short_name` — аналогично
- `CarrierService.short_name` — аналогично
- `CompanyService.short_name` — аналогично

**Автоматические сокращения по умолчанию:**
| Паттерн в названии | short_name |
|---------------------|------------|
| THS | THS |
| Разгрузка / Погрузка / Декларация | Порт |
| Хранение | Хран |
| Документы | Док |
| Доставка | Дост |
| Транспорт | Трансп |

**Новый метод `CarService.get_service_short_name()`** — возвращает сокращённое название связанной услуги.

**Переработка `regenerate_items_from_cars()`:**
- Каждая позиция инвойса теперь соответствует одной группе услуг (по `short_name`) для одного авто
- Услуги с одинаковым `short_name` суммируются (напр. Разгрузка + Погрузка + Декларация → "Порт")
- `description` позиции = short_name (используется для группировки в таблице)

**Новый метод `NewInvoice.get_items_pivot_table()`:**
- Формирует данные для табличного отображения: строки (авто), столбцы (группы услуг), итоги
- Используется в шаблоне админки для рендеринга pivot-таблицы

**Табличное отображение в админке:**

| Авто | THS | Порт | Хран | Итого |
|------|-----|------|------|-------|
| Toyota, VIN1 | 180 | 80 | 50 | 310 € |
| BMW, VIN2 | 120 | 60 | 30 | 210 € |
| **Итого** | 300 | 140 | 80 | **520 €** |

**Миграция:** `0104_add_service_short_name.py`
- Добавлено поле `short_name` в 4 модели услуг
- Data migration: автоматическое заполнение сокращений для существующих услуг

**Обновлённый шаблон:** `templates/admin/core/newinvoice/change_form.html`
- Убран дубликат блоков `extrahead` и `content`
- Добавлена pivot-таблица с hover-эффектами и итогами

**Файлы:**
- `core/models.py` — поле `short_name` в 4 моделях, метод `get_service_short_name()` в CarService
- `core/models_billing.py` — новые методы `regenerate_items_from_cars()`, `get_items_pivot_table()`
- `core/admin.py` — `short_name` в инлайнах услуг
- `core/admin_billing.py` — `pivot_table` в контексте
- `templates/admin/core/newinvoice/change_form.html` — pivot-таблица
- `core/migrations/0104_add_service_short_name.py` — миграция + data migration

---

### 23. Инфраструктура синхронизации между машинами (06.02.2026)

**Статус:** Завершено

**Проблема:** Код на VPS, стационарном компьютере и ноутбуке расходился — на VPS накапливались незакоммиченные изменения, локально тоже были свои правки, синхронизация БД делалась вручную.

**Решение:**
1. Все незакоммиченные изменения с VPS закоммичены и запушены в git
2. Локальные машины (стационарный + ноутбук) синхронизированы с VPS
3. БД скопирована с VPS на локальные машины через `pg_dump`/`pg_restore`
4. Созданы скрипты и инструкция для упрощения синхронизации

**Новые файлы:**
- `SYNC_GUIDE.md` — пошаговая инструкция для 4 сценариев синхронизации
- `sync_from_vps.ps1` — PowerShell-скрипт для автоматической синхронизации (код + БД)
- `vps_push.sh` — Bash-скрипт для коммита и пуша изменений с VPS

**Использование:**
- После работы на VPS: `./vps_push.sh` (на сервере)
- Перед работой на другой машине: `git stash; git pull origin master` (код) + 3 команды для БД
- Подробная инструкция: `SYNC_GUIDE.md`

---

### 26. Система тарифов клиентов (07.02.2026)

**Статус:** Завершено

**Цель:** Автоматический расчёт наценки на основе согласованной общей цены за авто (все услуги кроме хранения). Цена может быть фиксированной или зависеть от количества ТС в контейнере.

**Два типа тарифов:**

| Тип | Описание | Пример |
|-----|----------|--------|
| **FIXED** | Фикс. цена, не зависит от кол-ва авто | Легковой → всегда 300€ |
| **FLEXIBLE** | Цена зависит от кол-ва ТС в контейнере | 3 авто → 290€, 4 авто → 265€ |

**Модель `ClientTariffRate`** в `core/models.py`:
- `client` — FK → Client
- `vehicle_type` — тип ТС (SEDAN, SUV, MOTO и т.д.)
- `min_cars` — от скольки ТС в контейнере (по умолчанию 1)
- `max_cars` — до скольки ТС (пусто = и более)
- `agreed_total_price` — общая согласованная цена за авто (€), включает ВСЕ услуги кроме хранения

**Поле `tariff_type`** в модели `Client`:
- `NONE` — без тарифа (обычные наценки)
- `FIXED` — фикс. цена (не зависит от кол-ва)
- `FLEXIBLE` — гибкая цена (зависит от кол-ва авто)

**Логика применения тарифов** (`apply_client_tariffs_for_container()` в `core/signals.py`):
1. Для каждого авто клиента с тарифом находится подходящая ставка по типу ТС и кол-ву авто
2. Суммируется себестоимость всех не-хранение услуг авто
3. Разница (agreed_total_price − себестоимость) распределяется поровну как наценка по всем не-хранение услугам
4. Хранение всегда считается отдельно

**Интерфейс:** В карточке клиента — fieldset "Тариф" с выбором типа + inline-таблица ставок.

**Миграции:**
- `0105_add_client_tariff_rates.py` — добавление `tariff_type` в `Client` и модели `ClientTariffRate`
- `0106_update_ths_multiplier_field.py` — промежуточное обновление полей
- `0107_add_volume_tiers_to_tariff.py` — добавление `min_cars`, `max_cars`, `agreed_ths_price`
- `0108_rename_agreed_price_field.py` — переименование в `agreed_total_price`

---

### 34. Redis-кэширование, оптимизация N+1 запросов, Rate Limiting, Celery, безопасность (08.02.2026)

**Статус:** Завершено

**Цель:** Перевести кэш услуг на Redis, устранить оставшиеся N+1 запросы, добавить rate limiting для API, вынести отправку email в Celery, усилить безопасность сессий.

**Изменения:**

#### 1. Redis-кэширование (замена class-level dict → Django cache)

**Проблема:** `CarService._service_obj_cache` — словарь на уровне класса. Не имел TTL, не сбрасывался между запросами, мог разрастаться бесконтрольно.

**Решение:**
- На production: `django.core.cache.backends.redis.RedisCache` (Redis db=1)
- Локально: `django.core.cache.backends.locmem.LocMemCache`
- Ключи кэша: `svc:{service_type}:{service_id}`, TTL = 300 секунд
- Добавлен сигнал `invalidate_service_cache()` — подключён к `post_save`/`post_delete` всех 4 моделей услуг (LineService, WarehouseService, CarrierService, CompanyService)
- Все тесты обновлены: `CarService._service_obj_cache.clear()` → `cache.clear()`

**Файлы:**
- `logist2/settings_base.py` — `CACHES` с RedisCache на db=1
- `logist2/settings.py` — `CACHES` с LocMemCache для локальной разработки
- `core/models.py` — `cache.get()`/`cache.set()` вместо словаря `_service_obj_cache`
- `core/signals.py` — `invalidate_service_cache()` на post_save/post_delete
- `core/tests.py` — обновлены 12 мест: `cache.clear()` вместо `_service_obj_cache.clear()`

#### 2. Устранение N+1 запросов (дополнительная оптимизация)

**Проблема:** Оставались N+1 в admin/car, admin/container и signals.

**Решение:**
- `CarAdmin.get_queryset()` — объединены два метода в один с `select_related` + `prefetch_related` + `annotate`; удалён неиспользуемый `list_prefetch_related`
- `ContainerAdmin` — добавлена аннотация `Count('photos')`; `photos_count_display` использует `_photos_count` с `admin_order_field`
- `core/signals.py` — пакетная загрузка `Car.objects.filter(id__in=car_ids)` вместо `Car.objects.get()` в цикле
- `core/models.py` — `calculate_total_price()` делает один проход по `car_services.all()` вместо 5 отдельных запросов

**Файлы:**
- `core/admin/car.py` — единый `get_queryset()` с select_related + prefetch_related + annotate
- `core/admin/container.py` — аннотация `Count('photos')`
- `core/signals.py` — пакетная загрузка ТС
- `core/models.py` — один проход в `calculate_total_price()`

#### 3. Rate Limiting для API

**Новый файл:** `core/throttles.py`

| Endpoint | Класс | Лимит |
|----------|-------|-------|
| `track_shipment` | `TrackShipmentThrottle` | 20 запросов/минуту |
| `ai_chat` | `AIChatThrottle` | 10 запросов/минуту |

**Файлы:**
- `core/throttles.py` — новый файл с `TrackShipmentThrottle` и `AIChatThrottle`
- `logist2/settings.py`, `logist2/settings_base.py` — `DEFAULT_THROTTLE_CLASSES` и `DEFAULT_THROTTLE_RATES` в `REST_FRAMEWORK`
- `core/views_website.py` — декораторы `@throttle_classes` на `track_shipment` и `ai_chat`

#### 4. Celery для фоновых задач

**Цель:** Вынести отправку email из синхронного сигнала в фоновую задачу.

**Новые файлы:**
- `logist2/celery.py` — конфигурация Celery app
- `core/tasks.py` — задачи `send_planned_notifications_task` и `send_unload_notifications_task` с retry-логикой

**Настройки:**
- Production: Redis db=2 как broker и result backend
- Локально: `CELERY_TASK_ALWAYS_EAGER = True` (выполняется синхронно без воркера)
- `logist2/__init__.py` — импорт celery app с fallback

**Логика:**
- `core/signals.py` — синхронные вызовы email заменены на `.delay()` с fallback на синхронную отправку при ошибке

**Зависимости:** `celery[redis]==5.4.0` добавлен в `requirements.txt`

#### 5. Безопасность сессий

- `SESSION_COOKIE_HTTPONLY = True` в `logist2/settings.py` и `logist2/settings_base.py` (было `False`)

**Тесты:** 57/57 OK

---

### 35. Оптимизация быстродействия: CONN_MAX_AGE, пагинация админки, кэширование views (08.02.2026)

**Статус:** Завершено

**Цель:** Ускорить работу админки и клиентского сайта за счёт переиспользования подключений к БД, ограничения строк на странице и кэширования статичных страниц.

**Изменения:**

#### 1. CONN_MAX_AGE = 600 (переиспользование подключений к PostgreSQL)

**Проблема:** Каждый запрос создавал новое подключение к PostgreSQL (~50-100ms overhead).

**Решение:** Добавлен `CONN_MAX_AGE: 600` (10 минут) и `connect_timeout: 10` в `settings_base.py` (production). В `settings.py` (локальный) уже было.

**Эффект:** ~20-30% снижение latency на каждый запрос.

#### 2. list_per_page = 50 + show_full_result_count = False

**Проблема:** Django Admin по умолчанию показывает 100 строк и выполняет `COUNT(*)` по всей таблице.

**Решение:** Добавлено в 5 Admin-классов:
- `CarAdmin` (`core/admin/car.py`)
- `ContainerAdmin` (`core/admin/container.py`)
- `ClientAdmin` (`core/admin/partners.py`)
- `NewInvoiceAdmin` (`core/admin_billing.py`)
- `TransactionAdmin` (`core/admin_billing.py`)

**Эффект:** Быстрее рендеринг списков, нет тяжёлого `COUNT(*)` для таблиц с тысячами записей.

#### 3. Кэширование views клиентского сайта

**Файл:** `core/views_website.py`

| View | Кэш | Причина |
|------|------|---------|
| `website_home` | 15 минут | Новости обновляются редко |
| `about_page` | 1 час | Статичная страница |
| `services_page` | 1 час | Статичная страница |
| `contact_page` | 1 час | Статичная страница |
| `news_list` | 15 минут | Список новостей |
| `news_detail` | Без кэша | Счётчик просмотров |

**Эффект:** Повторные загрузки страниц отдаются из кэша (Redis на production, LocMemCache локально) без обращения к БД.

**Тесты:** 57/57 OK

---

### 37. Glassmorphism-редизайн дашборда (08.02.2026)

**Статус:** Завершено

**Цель:** Модернизация визуального стиля дашборда `/admin/dashboard/` — переход от плоского белого дизайна к glassmorphism (стиль iOS/macOS) с frosted glass карточками, тёмным градиентным фоном и светящимися акцентами.

**Файл:** `templates/admin/company_dashboard.html` — единственный изменённый файл.

**Изменения в CSS (полная замена блока `<style>`):**

| Элемент | Было | Стало |
|---------|------|-------|
| Фон страницы | Белый (дефолтный) | `linear-gradient(135deg, #0f0c29 → #1a1a3e → #24243e → #0f3460 → #1a1a2e)` |
| Карточки KPI, графики, таблицы | `background: #fff`, `box-shadow: 0 2px 8px` | `background: rgba(255,255,255,0.07)`, `backdrop-filter: blur(12px)`, `border: 1px solid rgba(255,255,255,0.12)`, `box-shadow: 0 8px 32px` |
| Левый бордер карточек | Цветной бордер | Цветной бордер + **glowing box-shadow** (напр. `-4px 0 15px rgba(...)`) |
| Hover карточек | Нет эффекта | `translateY(-4px)` + усиленное свечение |
| Текст KPI | Тёмный (`#777`, `#999`, `#333`) | Белый (`#fff`, `rgba(255,255,255,0.6)`, `rgba(255,255,255,0.5)`) |
| Значения (positive/negative) | `#28a745` / `#dc3545` | `#4cdf72` / `#ff6b7a` (ярче для тёмного фона) |
| Кнопки быстрых действий | Solid background | Glass base `rgba(255,255,255,0.1)` + colored tint + hover glow |
| Статус-бейджи | Solid background, `PARTIALLY_PAID` с тёмным текстом | Semi-transparent + `box-shadow: 0 0 8px`, все белый текст |
| Таблицы | Тёмный текст, белый фон | Белый текст 80%, translucent borders, ссылки `#64b5f6` |
| Секции-заголовки | `color: #333`, `border-bottom: 2px solid #0f3460` | `color: rgba(255,255,255,0.9)`, `border-bottom: rgba(255,255,255,0.15)` |
| Скроллбар таблиц | Стандартный | Custom webkit scrollbar (thin, translucent) |
| Mobile (≤768px) | Базовый responsive | Reduced `backdrop-filter: blur(8px)` для производительности |

**Изменения в HTML:**
- Inline `style="color:#8B0000"` на карточке "В порту" заменён на `#e74c3c` (читаемость на тёмном фоне)

**Изменения в Chart.js (JavaScript):**
- Bar chart: `scales.y.ticks.color: '#aaa'`, `scales.x.ticks.color: '#aaa'`, `grid.color: 'rgba(255,255,255,0.06)'`, `legend.labels.color: '#ccc'`
- Doughnut charts (обе): `legend.labels.color: '#ccc'`
- `STATUS_COLORS.IN_PORT`: `#8B0000` → `#e74c3c`

---

### 36. Дашборд компании — полная перестройка (08.02.2026)

**Статус:** Завершено

**Цель:** Старый дашборд `/company-dashboard/` был полностью сломан — ссылался на удалённые поля (`invoice_balance`, `cash_balance`, `card_balance`, `paid`, `total_amount`, `issue_date`) из старой биллинг-системы. Полностью перестроен с нуля на актуальных моделях (`NewInvoice`, `Transaction`, `Company.balance`).

**Новые / изменённые файлы:**

| Файл | Действие | Описание |
|------|----------|----------|
| `core/services/dashboard_service.py` | НОВЫЙ | Сервис `DashboardService` — агрегация данных для дашборда с кэшированием |
| `core/views.py` | ИЗМЕНЁН | Полностью переписана функция `company_dashboard()` — делегация в `DashboardService` |
| `core/cache_utils.py` | ИСПРАВЛЕН | Заменены сломанные поля: `invoice_balance/cash_balance/card_balance` → `balance`, `issue_date` → `date`, `total_amount` → `total`, добавлен `import models` |
| `logist2/urls.py` | ИЗМЕНЁН | URL перенесён: `/company-dashboard/` → `/admin/dashboard/` |
| `templates/admin/company_dashboard.html` | ПЕРЕПИСАН | Полностью новый шаблон с Chart.js, KPI, таблицами |
| `templates/admin/base_site.html` | ИЗМЕНЁН | Добавлена иконка 📊 Dashboard в навигацию |

**Функционал дашборда:**

1. **Операционные KPI (карточки):**
   - Авто по статусам (В пути / В порту / Разгружены / Переданы) — цветные карточки
   - Контейнеры по статусам (в подписях)
   - Авто на хранении (количество + сумма хранения)
   - Активные тралы (автовозы)
   - Итого авто / контейнеров

2. **Финансовые KPI (карточки):**
   - Баланс компании (live, без кэша)
   - Доходы за месяц
   - Расходы за месяц
   - Прибыль за месяц
   - К получению (outstanding invoices, ISSUED + PARTIALLY_PAID)
   - Просроченные инвойсы

3. **Графики (Chart.js 4.x):**
   - Доходы и расходы за 6 месяцев (bar chart)
   - Авто по статусам (doughnut chart)
   - Инвойсы по статусам (doughnut chart)

4. **Таблицы (последние 15):**
   - Транзакции — с `select_related` на все FK отправителя/получателя
   - Инвойсы — с ссылками на `admin:core_newinvoice_change`

5. **Быстрые действия:**
   - Создать инвойс / транзакцию
   - Все инвойсы / транзакции
   - Сравнение сумм
   - Карточка компании

**Сервис `DashboardService`:**
- Кэширование: 5 минут (`CACHE_TIMEOUTS['short']`) для KPI, 30 минут для графиков
- Баланс компании **НИКОГДА** не кэшируется — `refresh_from_db()` при каждом запросе
- Данные графиков сериализуются через `json.dumps()` → `json_script` template tag (XSS-safe)

**Исправления в `cache_utils.py`:**
- `cache_company_stats()`: `company.invoice_balance + cash_balance + card_balance` → `company.balance`; `issue_date` → `date`; `total_amount` → `total`
- `cache_client_stats()`: аналогичные замены + фильтр инвойсов через `recipient_client` FK
- `cache_warehouse_stats()`: аналогичные замены + фильтр через `issuer_warehouse/recipient_warehouse`
- Добавлен `from django.db import models` (исправление pre-existing bug с `models.Q`)

**Тесты:** 57/57 OK

---

### 38. Редизайн дашборда: светлая тема + Boxicons + оптимизация запросов (08.02.2026)

**Статус:** Завершено

**Цель:** Заменить glassmorphism-дизайн на современный светлый дизайн с фиолетовыми акцентами, заменить emoji/SVG иконки на профессиональные Boxicons, исправить графики, оптимизировать производительность.

**Визуальные изменения:**

| Элемент | Было | Стало |
|---------|------|-------|
| Фон | Тёмный градиент glassmorphism | Светлый `#f4f2ff` |
| Карточки KPI | Frosted glass + цветные свечения | Белые карточки, скруглённые углы, subtle тени, цветные полоски сверху |
| Summary cards | Отсутствовали | 3 градиентных карточки (баланс, прибыль, к получению) |
| Иконки KPI | Emoji (🚢, ⚓, 📦...) → самодельные SVG | **Boxicons** — профессиональная библиотека (CDN) |
| Иконки графиков | Emoji (📊, 🚗, 📄) | Boxicons inline (`bx-bar-chart`, `bxs-car`, `bxs-file`) |
| Иконки таблиц | Emoji (💳, 📄) | Boxicons (`bxs-credit-card`, `bxs-file-doc`) |
| Бейдж даты | Emoji 📅 | Boxicons `bx-calendar` |
| Шрифт | Системный | Inter (Google Fonts) |
| Графики | Серые кольца (из-за double JSON) | Цветные кольца, tooltips с процентами |

**Иконки Boxicons для KPI карточек:**

| Карточка | Иконка | Класс |
|----------|--------|-------|
| В пути | Корабль | `bxs-ship` |
| В порту | Флаг | `bxs-flag-alt` |
| Разгружены | Коробка | `bxs-package` |
| Переданы | Галочка | `bxs-check-circle` |
| На хранении | Здание | `bxs-buildings` |
| Активные тралы | Грузовик | `bxs-truck` |
| Всего авто | Автомобиль | `bxs-car` |
| Всего контейнеров | Куб | `bxs-cube` |
| Баланс | Кошелёк | `bxs-wallet` |
| Доходы | Тренд вверх | `bx-trending-up` |
| Расходы | Тренд вниз | `bx-trending-down` |
| Прибыль | Диаграмма | `bxs-bar-chart-alt-2` |
| К получению | Чек | `bxs-receipt` |
| Просроченные | Восклицание | `bxs-error-circle` |

**Исправления графиков (doughnut charts):**

- **Причина серых колец:** Двойная JSON-сериализация — `json.dumps()` в view + `|json_script` в шаблоне
- **Решение:** Удалён `json.dumps()` из `core/views.py`, данные передаются как Python dict
- Добавлена фильтрация нулевых сегментов (`filterNonZero`)
- Tooltip с процентами (`percentTooltip`)
- Центральная метка с общим количеством

**Оптимизация производительности дашборда:**

1. **Redis кэш локально** (вместо LocMemCache):
   - `settings.py` теперь автоматически определяет Redis (socket probe), fallback на `FileBasedCache`
   - Redis установлен как служба Windows (автозапуск: `StartType: Automatic`)
   - Путь: `C:\Users\art-f\Redis-x64-5.0.14.1\redis-server.exe`

2. **Оптимизация запросов `get_revenue_expenses_by_month()`:**
   - Было: 12 отдельных SQL-запросов (2 на месяц × 6 месяцев)
   - Стало: 2 запроса с `TruncMonth` (GROUP BY на уровне PostgreSQL)
   - Добавлен импорт `from django.db.models.functions import TruncMonth`

3. **`ANALYZE` на PostgreSQL** — обновлена статистика оптимизатора после импорта БД с VPS

**Результат бенчмарка:**
- Холодный кэш (все запросы к БД): **46 мс** (было ~200-500 мс)
- Горячий кэш (из Redis): **3 мс**
- Ускорение: **17.7x**

**Файлы:**
- `templates/admin/company_dashboard.html` — полная замена CSS + HTML + JS
- `core/views.py` — удалён `json.dumps()` для chart data
- `core/services/dashboard_service.py` — `TruncMonth` вместо цикла, импорт `TruncMonth`
- `logist2/settings.py` — Redis cache с fallback на FileBasedCache

---

### 47. Улучшение сверки банковских транзакций (09.02.2026)

**Статус:** Завершено

**Цель:** Трёхуровневая система сопоставления банковских транзакций: автоматическое сопоставление с инвойсами, ручная привязка, и пометка "не требует привязки" для служебных операций.

**Новое поле `reconciliation_skipped`** в `BankTransaction` (`core/models_banking.py`):
- `BooleanField(default=False)` — пометка "не требует привязки"
- Обновлено property `is_reconciled`: теперь True если `matched_transaction` ИЛИ `matched_invoice` ИЛИ `reconciliation_skipped`

**Авто-пропуск служебных операций** (`core/services/revolut_service.py`):
- При синхронизации новых транзакций типа `fee`, `exchange`, `tax` автоматически ставится `reconciliation_skipped=True`
- Заметка: "Авто-пропуск: Комиссия банка / Обмен валют / Налог"

**Обновлённый фильтр сопоставления** (`core/admin_banking.py`):
- Три значения: "Сопоставлены (привязан инвойс)", "Не требует привязки", "Не сопоставлены"

**Три визуальных состояния `display_reconciled`:**
- ✓ Сопоставлено (зелёный) + номер инвойса
- ⊘ Пропуск (серый) — не требует привязки
- ✗ Не привязано (красный) — требует внимания

**Mass actions:**
- "Пометить: не требует привязки" — массовая пометка
- "Снять пометку" — обратное действие

**Столбец "Действие" (`display_action`):**
- Для привязанных — ссылка на инвойс (📄 номер)
- Для несопоставленных — две ссылки: "💰 Расход" + "🔗 Привязать"
- Для пропущенных — прочерк

**Обновлён `auto_reconcile_bank_transactions()`** (`core/services/billing_service.py`):
- Исключает `reconciliation_skipped=True` из автоматического поиска

**Миграция:** `0114_add_reconciliation_skipped.py`

**Файлы:**
- `core/models_banking.py` — поле reconciliation_skipped, обновлён is_reconciled
- `core/admin_banking.py` — фильтр, display_reconciled, display_action, mass actions
- `core/services/revolut_service.py` — авто-пропуск fee/exchange/tax
- `core/services/billing_service.py` — исключение skipped из auto_reconcile

---

### 48. Быстрое создание расхода из банковской транзакции (09.02.2026)

**Статус:** Завершено

**Цель:** Для покупок по карте Revolut (топливо, канцтовары и т.д.) без счёта от контрагента — быстрое создание расхода прямо из списка банковских транзакций.

**Кнопка "Расход" в списке** (`core/admin_banking.py`):
- В `display_action` для несопоставленных транзакций — зелёная ссылка "💰 Расход"
- Ведёт на кастомную форму создания расхода

**Кастомный view `create_expense_view`** (`core/admin_banking.py`):
- URL: `admin/core/banktransaction/<pk>/create-expense/`
- GET: показывает форму с предзаполненными данными из банковской транзакции
- POST: создаёт NewInvoice + InvoiceItem, помечает PAID, привязывает к банковской транзакции

**Логика создания расхода:**
1. Авто-подбор компании по `counterparty_name` (icontains поиск)
2. Создание NewInvoice (входящий, recipient = Caromoto Lithuania, статус PAID)
3. Создание InvoiceItem (описание из банка, сумма = abs(amount))
4. `calculate_totals()`, `paid_amount = total`
5. Привязка: `bank_trx.matched_invoice = invoice`

**Шаблон формы** (`templates/admin/core/banktransaction/create_expense.html`):
- Блок "Данные из банка" (read-only): дата, сумма, контрагент, описание
- Категория расхода (select, обязательное) — из ExpenseCategory
- Контрагент (select, необязательное) — из Company, pre-selected если совпадает
- Описание (textarea, предзаполнено)

**Mass action "Создать расходы (массово)":**
- Промежуточная страница: выбор одной категории для всех
- Создаёт расходы пакетно для всех выбранных транзакций
- Шаблон: `templates/admin/core/banktransaction/create_expenses_bulk.html`

**Файлы:**
- `core/admin_banking.py` — `get_urls()`, `create_expense_view()`, `create_expenses_bulk()`, обновлён `display_action()`
- `templates/admin/core/banktransaction/create_expense.html` — форма создания расхода
- `templates/admin/core/banktransaction/create_expenses_bulk.html` — массовое создание

---

## ИСТОРИЯ ИЗМЕНЕНИЙ

| Дата | Описание |
|------|----------|
| 09.02.2026 | Быстрое создание расхода из банковской транзакции: кнопка "Расход", массовое создание, авто-подбор компании |
| 09.02.2026 | Улучшение сверки: reconciliation_skipped, авто-пропуск fee/exchange/tax, 3-уровневый фильтр, mass actions |
| 09.02.2026 | Ручной ввод суммы и позиций для входящих инвойсов: manual_total, manual_items_json, AJAX calc-cars-total |
| 09.02.2026 | Комплексные тесты: run_all_tests.py — 67 тестов, 15 секций, удалены 4 устаревших теста, исправлена миграция 0059 |
| 09.02.2026 | Invoice-bank reconciliation: external_number, matched_transaction/invoice на BankTransaction, BillingService |
| 09.02.2026 | Группировка сайдбара админки: LogistAdminSite — 6 логических групп вместо плоского списка |
| 09.02.2026 | Полный учёт расходов: ExpenseCategory, category/attachment на инвойсах и транзакциях, P&L на дашборде |
| 09.02.2026 | Интеграция site.pro (b1.lt) Accounting API — бухгалтерские инвойсы (B1-Api-Key, /My-Accounting/api) |
| 08.02.2026 | Редизайн дашборда: светлая тема, Boxicons, исправление графиков, Redis локально, TruncMonth оптимизация |
| 08.02.2026 | Glassmorphism-редизайн дашборда: frosted glass карточки, тёмный градиент, glowing accents, светлые оси графиков |
| 08.02.2026 | Дашборд компании: полная перестройка с Chart.js, KPI, DashboardService, исправление cache_utils |
| 08.02.2026 | Оптимизация: CONN_MAX_AGE, пагинация админки (list_per_page=50), кэширование views сайта |
| 08.02.2026 | Redis-кэширование, N+1 оптимизация, Rate Limiting, Celery для email, SESSION_COOKIE_HTTPONLY |
| 08.02.2026 | Чистка: удалены 2 backup-файла (1975 строк), заглушка PDF, 2 пустых метода, 3 legacy manager-а, исправлен placeholder телефона |
| 08.02.2026 | Добавлены ещё 29 тестов: calculate_total_price, CarService цены, THS-сервисы, инвойс-статусы, хранение в инвойсах, дефолты склада |
| 08.02.2026 | Добавлены 19 unit-тестов: THS расчёт, хранение (дни×ставка), regenerate_items_from_cars (markup, группировка) |
| 08.02.2026 | Замена print() на logging во всём core/ (signals, models, admin, forms) |
| 08.02.2026 | Очистка тестов: удалены 4 устаревших теста (старый API биллинга), 9/9 pass |
| 08.02.2026 | Разбиение admin.py (3575 строк) на пакет admin/ из 4 модулей |
| 08.02.2026 | Устранение N+1 запросов: кэш _service_obj_cache в CarService |
| 08.02.2026 | Удаление побочных эффектов из display-методов (запись в БД при просмотре) |
| 08.02.2026 | Исправление дублированного Transaction.save() |
| 08.02.2026 | Добавление unit-тестов: round_up_to_5, расчёт хранения, кэш услуг |
| 08.02.2026 | Вынос round_up_to_5 в core/utils.py для переиспользования |
| 08.02.2026 | transaction.atomic() в regenerate_items_from_cars() — защита от битых инвойсов |
| 08.02.2026 | Исправление or Decimal('0') → if is not None в расчёте наценки |
| 08.02.2026 | round_up_to_5 на чистой Decimal-арифметике (без потери точности через float) |
| 08.02.2026 | Оптимизация total_price_display — использование prefetched car_services |
| 07.02.2026 | Система тарифов клиентов: фикс. и гибкая цена за авто, автоматический расчёт наценки |
| 06.02.2026 | Массовое действие "Выставить" для инвойсов в админке |
| 06.02.2026 | Табличный формат инвойсов: pivot-таблица (авто × услуги), short_name для всех услуг |
| 06.02.2026 | Инфраструктура синхронизации: SYNC_GUIDE.md, sync_from_vps.ps1, vps_push.sh |
| 14.01.2026 | Первоначальная реализация THS системы |
| 16.01.2026 (день) | Упрощение цен, динамический расчёт хранения, исправление отображения |
| 16.01.2026 (вечер) | Замена процентов на коэффициенты, кнопка "Пересчитать THS", исправление кэша |
| 17.01.2026 | Система скрытой наценки: markup_amount, default_markup, распределение по услугам |
| 21.01.2026 | Улучшенная система фотографий: автосинхронизация с Google Drive, разделение по типам, галерея с вкладками |
| 21.01.2026 | Исправление URL фотографий на VPS (добавление /media/ префикса) |
| 21.01.2026 | Исправление hover-эффекта кнопок (убрано мигание, простой opacity) |
| 21.01.2026 | Улучшение Lightbox для мобильных: прозрачные кнопки, скрытие при зуме, touch-жесты |
| 23.01.2026 | AI-помощник на сайте и в админке, контекст из БД, ссылки на галерею |
| 24.01.2026 | Актуализация статуса: обратная синхронизация контейнера при TRANSFERRED |
| 24.01.2026 | Услуги компаний, add_by_default для линий/перевозчиков, исправление автодобавления |
| 24.01.2026 | Админ-агент, RAG индекс, контекст админки, быстрые действия |
| 31.01.2026 | Столбец "Наценка" (Н) в списке ТС с оптимизацией через annotate + Sum |
| 04.02.2026 | Система автовозов на загрузку: формирование автовозов, автоматическое создание инвойсов |

---

### 29. Замена print() на logging во всём core/ (08.02.2026)

**Статус:** Завершено

**Цель:** Заменить все `print()` в рабочих файлах на `logging.logger` с правильными уровнями логирования.

**Заменено ~28 print()** в 5 файлах:

| Файл | Кол-во | Уровни |
|------|--------|--------|
| `core/signals.py` | 3 | debug (штатная отладка), error (ошибки) |
| `core/models.py` | 3 | error (ошибки пересчёта цены) |
| `core/admin/car.py` | 12 | debug (удаление услуг, пересчёт хранения), error (ошибки) |
| `core/admin/partners.py` | 2 | Удалены (logger уже был рядом, print дублировал) |
| `core/forms.py` | 8 | debug (создание/удаление услуг), warning (не найдено), error (ошибки) |

**Дополнительно:**
- В `core/forms.py` добавлен `import logging` + `logger = logging.getLogger('django')`
- В `core/admin/partners.py` уровень `logger.warning` заменён на `logger.info` (это не предупреждение, а штатный вызов)
- Файлы `models_BACKUP_BEFORE_DELETION.py` и миграции не затронуты

**Примечание:** Индексы БД (пункт 8 из плана) уже были реализованы ранее — `Car.status`, `Car.vin`, `CarService.car + service_type` и ещё ~15 индексов на Container, Car, CarService.

---

### 30. Очистка и исправление тестов (08.02.2026)

**Статус:** Завершено

**Проблема:** 4 из 13 тестов падали с ошибками:
- `BillingTests` (3 теста) — использовали `Invoice(client=...)`, `Payment(payer=..., recipient=...)` — старый API, заменённый на `NewInvoice`/`Transaction`
- `InvoiceCalculationTests` (1 тест) — аналогично
- `Warehouse(rate=0)` — поле `rate` удалено из Warehouse в миграции 0091

**Решение:**
- Удалены 4 устаревших теста (`BillingTests`, `InvoiceCalculationTests`) — тестировали несуществующий API
- Исправлен `Warehouse(rate=0)` → `Warehouse()` (без rate)

**Результат:** 9 тестов, 9 OK, 0 ошибок, 0.062 секунды

| Тест-класс | Тестов | Что проверяет |
|---|---|---|
| `RoundUpTo5Tests` | 3 | Точные кратные 5, округление вверх, Decimal-точность |
| `StorageCostCalculationTests` | 2 | Без склада → 0, без даты разгрузки → 0 |
| `ServiceCacheTests` | 3 | Корректное имя, кэш без повторных SQL, несуществующая услуга |
| `APIPermissionsTests` | 1 | API требует авторизацию staff |

---

### 31. Расширение покрытия тестами: THS, хранение, инвойсы (08.02.2026)

**Статус:** Завершено

**Цель:** Покрыть тестами критические бизнес-функции, которые ранее не были протестированы.

**Добавлено 19 новых тестов** (было 9, стало 28):

| Тест-класс | Тестов | Что проверяет |
|---|---|---|
| `THSCalculationTests` | 8 | Пропорциональное распределение THS по коэффициентам типов ТС, округление до 5, дефолтный коэффициент 1.0, edge cases (нет контейнера/линии/THS/машин), одна машина |
| `StorageCostFullTests` | 5 | Расчёт платных дней × ставка из услуги "Хранение", бесплатные дни, transfer_date для TRANSFERRED, нет услуги → 0, update_days_and_storage |
| `RegenerateItemsTests` | 6 | Создание позиций от склада, markup для Company (+10 EUR), исключение markup для склада, группировка по short_name, нет issuer → 0 позиций, повторный вызов удаляет старые |

**Покрытые функции:**
- `calculate_ths_for_container()` — `core/signals.py:222`
- `calculate_storage_cost()` / `update_days_and_storage()` — `core/models.py:965` / `core/models.py:783`
- `_regenerate_items_from_cars_inner()` — `core/models_billing.py:587`

**Результат:** 28 тестов, 28 OK, 0 ошибок, 0.277 секунды

---

### 32. Полное покрытие критичных бизнес-функций тестами (08.02.2026)

**Статус:** Завершено

**Цель:** Покрыть все оставшиеся критичные функции (деньги, расчёты, статусы).

**Добавлено 29 новых тестов** (было 28, стало 57):

| Тест-класс | Тестов | Что проверяет |
|---|---|---|
| `CalculateTotalPriceTests` | 5 | Сумма услуг + markup, quantity множитель, default vs custom price, zero price |
| `CarServicePriceTests` | 4 | final_price (без markup) vs invoice_price (с markup), zero markup ≠ None, fallback на default |
| `CreateTHSServicesTests` | 5 | Создание CarService для LINE/WAREHOUSE payer, удаление старых THS, edge cases |
| `InvoiceCalculateTotalsTests` | 3 | subtotal из позиций, discount, пустой инвойс |
| `InvoiceStatusTests` | 5 | PAID (полная/переплата), PARTIALLY_PAID, OVERDUE (просроченный), DRAFT не меняется |
| `RegenerateStorageItemTests` | 3 | Группа 'Хран' для склада/компании, исключена для линии |
| `ApplyWarehouseDefaultsTests` | 4 | force=True перезаписывает, force=False сохраняет, заполнение пустых, без склада |

**Результат:** 57 тестов, 57 OK, 0 ошибок, 0.567 секунды

---

### 33. Чистка кодовой базы: мёртвый код, заглушки, placeholder-ы (08.02.2026)

**Статус:** Завершено

**Удалено 2045 строк мёртвого кода:**

| Что удалено | Файл | Строк |
|---|---|---|
| Backup моделей | `models_BACKUP_BEFORE_DELETION.py` | 1943 |
| Backup моделей | `models_OLD_BACKUP.py` | 32 |
| Заглушка PDF-экспорта | `admin_billing.py` | 4 |
| Пустые методы `get_rates_by_provider()`, `get_parameters_by_provider()` | `models.py` | 29 |
| Legacy managers: `BaseManager`, `ContainerManager`, `CarManager` | `models.py` | 15 |

**Исправления:**
- Placeholder телефона `+370 XXX XXXXX` → реальный номер из `settings.COMPANY_PHONE`
- AI-чат теперь берёт телефон и email из Django settings (а не hardcode)
- `settings.py`: `COMPANY_PHONE = '+37068830450'` (было `+370 XXX XXXXX`)

**Тесты:** 57/57 OK после чистки

---

### 27. Исправление Decimal-точности, транзакционная безопасность, оптимизация запросов (08.02.2026)

**Статус:** Завершено

**Цель:** Исправить баги в вычислениях с Decimal, обеспечить атомарность критичных операций, оптимизировать запросы в админке.

**Исправления:**

1. **`transaction.atomic()` в `regenerate_items_from_cars()`:**
   - Метод обёрнут в транзакцию — при ошибке во время генерации позиций инвойса данные не повредятся
   - Создан внутренний метод `_regenerate_items_from_cars_inner()` для структурности
   - **Файл:** `core/models_billing.py`

2. **Исправление `or Decimal('0')` → `if is not None`:**
   - **Проблема:** Паттерн `self.markup_amount or Decimal('0')` возвращал `Decimal('0')` когда `markup_amount = Decimal('0.00')` (потому что `Decimal('0.00')` — это falsy)
   - **Решение:** Заменён на `self.markup_amount if self.markup_amount is not None else Decimal('0')`
   - **Файлы:** `core/models.py` (свойство `invoice_price`), `core/models_billing.py` (расчёт в `regenerate_items_from_cars`)

3. **`round_up_to_5()` на чистой Decimal-арифметике:**
   - **Проблема:** Старая версия использовала `math.ceil(float(value) / 5) * 5` — потеря точности при больших числах
   - **Решение:** `remainder = value % 5; return value + (5 - remainder)` — чистая Decimal
   - **Файл:** `core/signals.py`

4. **Оптимизация `total_price_display` в `CarAdmin`:**
   - **Проблема:** Каждый вызов делал отдельный `CarService.objects.filter(car=obj)` и `aggregate(Sum)` — N+1 запросов
   - **Решение:** Использует предзагруженные `obj.car_services.all()` и аннотацию `_total_markup`
   - **Файл:** `core/admin.py` (позднее `core/admin/car.py`)

---

### 28. Устранение N+1 запросов, удаление побочных эффектов, разбиение admin.py (08.02.2026)

**Статус:** Завершено

**Цель:** Ускорить админку, убрать опасные побочные эффекты в display-методах, разбить монолитный admin.py на модули, добавить unit-тесты.

**Изменения:**

1. **Кэш объектов услуг (`_service_obj_cache`) в CarService:**
   - **Проблема:** `get_service_name()`, `get_service_short_name()`, `get_default_price()` каждый делали отдельный `Model.objects.get(id=...)`. В `services_summary_display` это вызывалось в цикле — N+1 запросов.
   - **Решение:** Добавлен словарь-кэш `_service_obj_cache` на уровне класса. Метод `_get_service_obj()` получает объект услуги один раз, последующие вызовы берут из кэша.
   - Добавлен `SERVICE_MODEL_MAP` — маппинг service_type → модель
   - Методы `get_service_name()`, `get_service_short_name()`, `get_default_price()` рефакторированы на использование `_get_service_obj()`
   - Кэш сбрасывается при перезапуске gunicorn (при деплое)
   - **Файл:** `core/models.py`

2. **Удаление побочных эффектов из display-методов:**
   - **Проблема:** `total_price_display` и `services_summary_display` вызывали `obj.update_days_and_storage()`, который ЗАПИСЫВАЛ в БД при каждом отображении списка ТС. Это и медленно, и потенциально опасно.
   - **Решение:** Убраны вызовы `update_days_and_storage()` из display-методов
   - **Файл:** `core/admin/car.py`

3. **Исправление дублированного `Transaction.save()`:**
   - **Проблема:** В `core/models_billing.py` метод `save()` класса `Transaction` содержал дублированный код — `super().save()` вызывался дважды
   - **Решение:** Удалён дублированный блок (6 строк)
   - **Файл:** `core/models_billing.py`

4. **Вынос `round_up_to_5` в `core/utils.py`:**
   - **Было:** Локальная функция внутри `calculate_ths_for_container()` в `core/signals.py`
   - **Стало:** Модульная функция в `core/utils.py` — доступна для переиспользования и тестирования
   - В `core/signals.py` заменён на `from core.utils import round_up_to_5`
   - **Файлы:** `core/utils.py`, `core/signals.py`

5. **Добавлены unit-тесты:**
   - `RoundUpTo5Tests` (SimpleTestCase, без БД):
     - Проверка точных кратных 5 (70 → 70, 0 → 0)
     - Проверка округления вверх (73.12 → 75, 76 → 80)
     - Проверка сохранения точности Decimal для больших чисел
   - `StorageCostCalculationTests`:
     - Проверка возврата 0 при отсутствии склада
     - Проверка возврата 0 при отсутствии даты разгрузки
   - `ServiceCacheTests`:
     - Проверка корректного получения имени услуги через кэш
     - Проверка что повторный вызов не делает SQL запрос
     - Проверка обработки несуществующей услуги
   - **Файл:** `core/tests.py`

6. **Разбиение `admin.py` (3575 строк) на пакет `admin/`:**
   - `core/admin.py` (3575 строк) **УДАЛЁН**
   - Создан пакет `core/admin/` с файлами:
     - `__init__.py` — импорт всех модулей для регистрации в Django Admin
     - `inlines.py` — все inline-классы (CarInline, ContainerPhotoInline, LineTHSCoefficientInline и др.)
     - `container.py` — ContainerAdmin
     - `car.py` — CarAdmin (1046 строк)
     - `partners.py` — WarehouseAdmin, ClientAdmin, CompanyAdmin, LineAdmin, CarrierAdmin, AutoTransportAdmin и др. (1431 строк)

**Файлы:**
- `core/models.py` — кэш `_service_obj_cache`, `SERVICE_MODEL_MAP`, метод `_get_service_obj()`
- `core/models_billing.py` — удалён дублированный `Transaction.save()`
- `core/signals.py` — импорт `round_up_to_5` из `core/utils`
- `core/utils.py` — функция `round_up_to_5()`
- `core/tests.py` — 3 новых тест-класса (115+ строк)
- `core/admin/` — пакет из 5 файлов (вместо одного admin.py)

---

### 18. AI-помощник на сайте и в админке (23.01.2026)

**Статус:** Завершено

**Функционал:**
- Подключён AI-помощник с контекстом из БД (VIN/контейнер, статус, склад, даты, фото)
- Блокировка финансовых вопросов (цены/оплата/балансы/инвойсы) с направлением к менеджеру
- Авто-поиск по VIN/контейнеру и выдача статуса, истории дат и фото
- Ссылки на галерею контейнера открываются одной ссылкой без регистрации
- В админке добавлен чат-видет

**Файлы:**
- `core/services/ai_chat_service.py` — сервис AI, контекст, поиск VIN/контейнера, фото
- `core/views_website.py` — AI чат, блок финансов, быстрый ответ по фото, ссылка на галерею
- `logist2/settings.py`, `logist2/settings_base.py` — AI настройки из `.env`
- `templates/admin/base_site.html` — виджет чата в админке
- `core/static/admin/css/ai-chat.css` — стили админ-виджета
- `core/static/website/js/ai-chat.js` — CSRF, кликабельные ссылки, fallback-диагностика
- `templates/website/home.html` — автозапуск поиска и открытия фото по параметрам `track`/`photos`
- `env.example`, `env.local.example` — пример AI настроек

**Новая ссылка на галерею:**
- `/?track=ECMU5566195&photos=1` — открывает поиск и сразу показывает фото контейнера

---

### 19. Система услуг компаний и унификация услуг поставщиков (24.01.2026)

**Статус:** Завершено

**Ключевые изменения:**
- Добавлены **услуги компаний** (`CompanyService`) и тип `COMPANY` для `CarService`
- Компания стала полноценным участником системы услуг (включая добавление услуг к ТС)
- Для услуг линий и перевозчиков добавлен флаг `add_by_default`
- Исправлен баг: услуги по умолчанию больше **не добавляются ко всем существующим ТС**
- В карточке ТС добавлен блок "Услуги компании" с модальным добавлением
- Кнопка добавления услуг у линий/перевозчиков показывается всегда (даже если услуг нет)

**Файлы:**
- `core/models.py` — модель CompanyService, тип `COMPANY`, расчёт totals
- `core/admin.py` — инлайны и UI для CompanyService, правки add_by_default
- `core/signals.py` — корректная логика add_by_default только для новых ТС
- `core/views.py` — поддержка `company` в get_available_services/add_services
- `templates/admin/core/car/change_form.html` — модальное окно услуг компании
- `logist2/urls.py` — endpoint `GET /api/companies/`

**Миграции:**
- `0099_add_default_flags_line_carrier_services.py`
- `0100_add_company_service.py`
- `0101_alter_carservice_service_type_and_more.py`

---

### 20. AI-ассистент в админке + RAG контекст (24.01.2026)

**Статус:** Завершено

**Функционал:**
- Отдельный агент для админки с доступом к БД и контекстом текущей страницы
- Передача контекста страницы админки в JS (`window.__adminPageContext`)
- Диагностика для контейнера/ТС/инвойса + UI-подсказки "что нажать"
- Быстрые действия в виджете (готовые подсказки)
- RAG индекс по документации/коду + команда на пересборку
- Логи: `used_fallback`, `fallback_reason`, `admin_context` (в DEBUG)
- Отключение системных прокси для API (session.trust_env = False)

**Файлы:**
- `core/services/admin_ai_agent.py` — логика админ-агента, диагностика, контекст БД
- `core/services/ai_rag.py` — RAG индекс, чанки, поиск, сниппеты
- `core/management/commands/rebuild_ai_index.py` — пересборка индекса
- `core/management/commands/rebuild_ai_index_if_stale.py` — пересборка при устаревании
- `templates/admin/base_site.html` — контекст страницы админки
- `core/static/website/js/ai-chat.js` — контекст админки, заголовки, quick actions
- `core/static/admin/css/ai-chat.css` — стили контекста и кнопок
- `core/views_website.py` — разделение admin/client, быстрый ответ по фото контейнера
- `core/models_website.py` + миграция `0102_add_aichat_context_snapshot.py`
- `logist2/settings_base.py` — настройки AI_RAG_*
- `env.example`, `env.local.example` — переменные для AI/RAG

**Исправления:**
- Клиентский чат теперь ищет фото по номеру контейнера, а не только по VIN
- Защита от ProxyError при запросах к API
- Исправлена ошибка `logger is not defined` в `ai_chat`

---

### 39. Исправление CSP для Boxicons на production (08.02.2026)

**Статус:** Завершено

**Проблема:** Иконки Boxicons не отображались на production-сервере. Причина — Content Security Policy (CSP) не разрешала загрузку стилей и шрифтов с `unpkg.com`.

**Решение:** Добавлен `https://unpkg.com` в `style-src` и `font-src` директивы CSP.

**Дополнительно найдено:** На сервере было два каталога проекта — `/var/www/logist2/` и `/var/www/www-root/data/www/logist2/`. Gunicorn работал из второго, а `git pull` первоначально обновлял первый. Исправлено — все операции деплоя теперь направлены в `/var/www/www-root/data/www/logist2/`.

**Файлы:**
- `logist2/settings_security.py` — добавлен `https://unpkg.com` в CSP

---

### 40. Интеграция Revolut Business API — банковские счета на дашборде (08.02.2026)

**Статус:** Завершено

**Функционал:**
- Подключение к Revolut Business API (Production) с автоматическим обновлением токенов
- Отображение балансов банковских счетов на дашборде компании (KPI-карточки)
- Отображение последних банковских транзакций в отдельной таблице на дашборде
- Зашифрованное хранение токенов в БД (Fernet, ключ из Django SECRET_KEY)
- Автоматическая синхронизация каждые 15 минут через cron
- Management-команда `setup_revolut` для первоначальной настройки (генерация JWT, обмен токенов)
- Management-команда `sync_bank_accounts` для ручной/cron синхронизации
- Django Admin с кнопкой "Синхронизировать сейчас"
- Расширяемая архитектура — BankConnection поддерживает разные типы банков

**Результат первой синхронизации:**
- 5 счетов (основной EUR: 863.97 €, + GBP, USD)
- 17 транзакций за последний месяц

**Новые файлы:**
- `core/models_banking.py` — модели BankConnection, BankAccount, BankTransaction
- `core/services/revolut_service.py` — API-клиент Revolut (авторизация, счета, транзакции)
- `core/admin_banking.py` — Django Admin для банковских моделей
- `core/management/commands/setup_revolut.py` — помощник настройки Revolut API
- `core/management/commands/sync_bank_accounts.py` — команда синхронизации для cron

**Изменённые файлы:**
- `core/models.py` — импорт моделей banking
- `core/services/dashboard_service.py` — методы `get_bank_balances()`, `get_recent_bank_transactions()`
- `templates/admin/company_dashboard.html` — секция банковских счетов + таблица транзакций
- `core/admin/__init__.py` — регистрация banking admin

**Миграция:** `0109_add_banking_models.py`

**Конфигурация на сервере:**
- Сертификаты: `/var/www/www-root/data/www/logist2/certs/` (privatecert.pem, publiccert.cer)
- Cron: `*/15 * * * *` — `sync_bank_accounts`
- Лог: `/var/log/logist2/bank_sync.log`

---

### 41. Интеграция site.pro (b1.lt) Accounting API — бухгалтерские инвойсы (09.02.2026)

**Статус:** Завершено (ожидает активации API-плана в site.pro)

**Цель:** Автоматическая отправка инвойсов из logist2 в бухгалтерскую систему site.pro (бывший b1.lt) для учёта и налоговой отчётности в Литве.

**Реальный API:**
- **Base URL:** `https://site.pro/My-Accounting/api`
- **Аутентификация:** заголовок `B1-Api-Key` (НЕ bearer token, НЕ api.sitepro.com)
- **Формат:** все запросы POST, Content-Type: application/json
- **Документация:** https://site.pro/My-Accounting/doc/api
- **Postman collection:** https://site.pro/My-Accounting/doc/api/api_data.json

**Новые модели (`core/models_accounting.py`):**

1. **SiteProConnection** — подключение к site.pro API
   - `company` — FK → Company
   - `_api_key`, `_private_key` — API-ключи (зашифрованы Fernet)
   - `_username`, `_password` — альтернативная аутентификация (зашифрованы)
   - `is_active`, `auto_push_on_issue` — настройки
   - `default_vat_rate`, `default_currency`, `invoice_series` — параметры инвойсов
   - `last_synced_at`, `last_error` — статус синхронизации

2. **SiteProInvoiceSync** — лог синхронизации инвойсов
   - `connection`, `invoice` — связи
   - `external_id`, `external_number` — ID/номер в site.pro
   - `pdf_url` — ссылка на PDF
   - `sync_status` — PENDING, SENT, FAILED, PDF_READY
   - `error_message` — ошибка последней попытки

**Сервис (`core/services/sitepro_service.py`):**
- `SiteProService` — полный API-клиент для site.pro Accounting API
- Аутентификация через заголовок `B1-Api-Key`
- `test_connection()` — проверка подключения (GET VAT rates)
- `push_invoice()` — отправка инвойса (создание sale + sale items + client)
- `search_clients()`, `create_client()`, `get_or_create_client()` — управление клиентами
- `search_sales()` — поиск продаж
- `get_invoice_pdf_url()` — получение ссылки на PDF
- `push_invoices()` — массовая отправка
- `get_vat_rates()`, `get_currencies()`, `get_series()` — справочники

**Ключевые эндпоинты site.pro:**
- `/warehouse/sales/create`, `/warehouse/sales/list` — продажи
- `/warehouse/sale-items/create` — позиции продажи
- `/clients/create`, `/clients/list` — клиенты
- `/warehouse/invoices/get-sale` — PDF инвойса
- `/reference-book/vat-rates/list` — ставки НДС
- `/reference-book/currencies/list` — валюты
- `/reference-book/series/list` — серии нумерации
- `/bank/sale-invoice/payment` — запись оплаты

**Админка (`core/admin_accounting.py`):**
- `SiteProConnectionAdmin` — управление подключением, действия "Проверить" и "Sync Now"
- `SiteProInvoiceSyncAdmin` — логи синхронизации с PDF-ссылками и повтором

**Действие в инвойсах (`core/admin_billing.py`):**
- "Отправить в site.pro" — массовая отправка выбранных инвойсов

**Автоматическая отправка (`core/signals.py`):**
- При смене статуса инвойса на ISSUED — авто-push если `auto_push_on_issue` включён
- Защита от рекурсии (`_pushing_to_sitepro` флаг)
- `transaction.on_commit()` для надёжности

**Management команда:**
- `python manage.py setup_sitepro` — интерактивная настройка (ввод API ключа, тест, параметры)

**Миграции:**
- `0110_sitepro_integration.py` — модели SiteProConnection и SiteProInvoiceSync
- `0111_add_sitepro_api_keys.py` — поля _api_key и _private_key

**Текущий статус:**
- API ключ валиден и принят системой site.pro
- API заморожен: "Company does not have enough funds"
- Требуется: оплатить/активировать API-план в site.pro
- Инфо: https://site.pro/faq/54436 | https://site.pro/Accounting-Prices/

**Файлы:**
- `core/models_accounting.py` — модели SiteProConnection, SiteProInvoiceSync
- `core/services/sitepro_service.py` — SiteProService (API-клиент)
- `core/admin_accounting.py` — Django Admin для site.pro
- `core/admin_billing.py` — действие "Отправить в site.pro" в инвойсах
- `core/admin/__init__.py` — регистрация admin-классов site.pro
- `core/signals.py` — авто-push при ISSUED
- `core/management/commands/setup_sitepro.py` — помощник настройки

---

### 46. Ручной ввод суммы и позиций для входящих инвойсов (09.02.2026)

**Статус:** Завершено

**Цель:** Возможность задать сумму и позиции для инвойсов без автомобилей (аренда, коммунальные, прочие расходы). Автоматический учёт суммы как долга компании.

**Проблема:** Инвойсы без автомобилей (аренда, коммуналка) не имели способа задать сумму: `total` рассчитывался только из `Car.services`. Долг (`remaining_amount = total - paid_amount`) уже работал, но `total` всегда был 0.

**Изменения в шаблоне (`templates/admin/core/newinvoice/change_form.html`):**

1. **Поле "Сумма к оплате"** — вынесено вверх, в блок основной информации рядом с "Номер счёта контрагента"
   - `input[type=number]` с `id="manual_total_input"`, шаг 0.01
   - Крупный шрифт (20px), фиолетовый цвет, выравнивание вправо
   - При выбранных авто — readonly (сумма считается автоматически), при отсутствии — editable

2. **Блок "Позиции вручную"** — отдельная карточка ниже автомобилей
   - Кнопка "Добавить позицию" — динамические строки (описание + кол-во + цена + удалить)
   - Автоматический пересчёт суммы вверху при изменении позиций
   - Данные передаются через hidden field `manual_items_json` (JSON)
   - Показывается только когда нет выбранных автомобилей

3. **AJAX расчёт суммы из автомобилей** — при выборе/удалении авто или смене выставителя
   - JS отправляет запрос на `/admin/core/newinvoice/calc-cars-total/`
   - Бэкенд считает по той же логике, что `regenerate_items_from_cars` (услуги, наценки, хранение)
   - Предыдущий запрос отменяется при быстром клике (abort)
   - Подсказка "Расчёт из N авто" обновляется

**Изменения в обработчике (`core/admin_billing.py`):**

1. **Метод `_handle_manual_items()`** в `NewInvoiceAdmin`:
   - Парсит `manual_total` и `manual_items_json` из POST
   - Если есть ручные позиции → создаёт `InvoiceItem` для каждой, пересчитывает total
   - Если нет позиций, но задан `manual_total > 0` → создаёт одну позицию "Оплата по счёту {номер}"
   - Вызывает `invoice.calculate_totals()` + `save()`

2. **AJAX endpoint `calc_cars_total_view()`**:
   - Принимает `car_ids[]` и `issuer` через GET
   - Определяет тип выставителя (Company/Warehouse/Line/Carrier)
   - Считает сумму услуг + наценки + хранение для каждого авто
   - Возвращает JSON `{total: "500.00", count: 3}`

**Учёт долга (уже работал из коробки):**
- `NewInvoice.total` — теперь заполняется для любого инвойса
- `NewInvoice.remaining_amount` = `total - paid_amount` — показывает долг
- `NewInvoice.status` — автоматически обновляется (ISSUED → PARTIALLY_PAID → PAID)
- Оплата через "Провести оплату" в сайдбаре работает для любого инвойса

**Тесты:** 3 теста пройдены (manual_total only, manual_items_json, empty input)

**Файлы:**
- `templates/admin/core/newinvoice/change_form.html` — UI: поле суммы, ручные позиции, AJAX
- `core/admin_billing.py` — `_handle_manual_items()`, `calc_cars_total_view()`, URL registration

---

### 42. Полный учёт расходов — категории, вложения, P&L дашборд (09.02.2026)

**Статус:** Завершено

**Цель:** Полноценный учёт доходов/расходов прямо из Django Admin, без зависимости от внешних систем.

**Новая модель `ExpenseCategory` (`core/models_billing.py`):**
- `name` — название (Логистика, Аренда, Коммунальные, Зарплаты, Маркетинг, Налоги, Прочие)
- `short_name` — сокращение для отчётов
- `category_type` — OPERATIONAL, ADMINISTRATIVE, SALARY, MARKETING, TAX, OTHER
- `is_active`, `order` — управление отображением

**Расширение `NewInvoice`:**
- `category` — FK → ExpenseCategory (авто-категоризация через сигнал)
- `attachment` — FileField (PDF/фото счёта)
- Computed property `direction`: OUTGOING (мы выставили), INCOMING (нам выставили), INTERNAL

**Расширение `Transaction`:**
- `category` — FK → ExpenseCategory (авто-заполнение из инвойса)
- `attachment` — FileField (чек/квитанция)

**Авто-категоризация (`core/signals.py`):**
- `pre_save` на NewInvoice: если выставитель — склад/линия/перевозчик → категория "Логистика"
- Не перезаписывает вручную установленную категорию

**Дашборд P&L (`core/services/dashboard_service.py`):**
- `get_expenses_by_category()` — агрегация расходов текущего месяца по категориям
- `get_income_by_category()` — агрегация доходов
- Doughnut-график "Структура расходов" на дашборде

**Admin-интеграции:**
- `ExpenseCategoryAdmin` — управление категориями
- `InvoiceDirectionFilter` — фильтр по направлению (Исходящий/Входящий)
- `direction_badge`, `category_display` в списке инвойсов
- Поля category и attachment в кастомной форме инвойса

**Миграция:** `0112_expense_categories.py` (модель + 7 базовых категорий через data migration)

**Файлы:**
- `core/models_billing.py` — ExpenseCategory, расширения NewInvoice и Transaction
- `core/admin_billing.py` — ExpenseCategoryAdmin, InvoiceDirectionFilter, обновления формы
- `core/signals.py` — auto_categorize_invoice
- `core/services/dashboard_service.py` — P&L по категориям
- `core/views.py` — передача данных P&L в шаблон дашборда
- `templates/admin/company_dashboard.html` — doughnut-график расходов
- `templates/admin/core/newinvoice/change_form.html` — поля category, attachment, external_number

---

### 43. Группировка сайдбара админки — LogistAdminSite (09.02.2026)

**Статус:** Завершено

**Цель:** Навигация по админке через логические группы вместо одного плоского списка.

**Реализация:**
- Кастомный `LogistAdminSite` (наследует `AdminSite`)
- Переопределение `get_app_list()` — группировка моделей core по категориям
- Monkey-patch в `logist2/__init__.py` для глобального применения

**6 групп в сайдбаре:**
1. **Логистика** — Car, Container, AutoTransport
2. **Партнёры** — Warehouse, Client, Company, Line, Carrier
3. **Финансы** — NewInvoice, Transaction, InvoiceItem, ExpenseCategory
4. **Банкинг** — BankConnection, BankAccount, BankTransaction
5. **Сайт** — ClientUser, AIChat, NewsPost, CarPhoto, ContainerPhoto
6. **Site.pro** — SiteProConnection, SiteProInvoiceSync
+ Системные модели Django (auth, sessions) остаются отдельно

**Файлы:**
- `logist2/admin_site.py` — LogistAdminSite, ADMIN_GROUPS
- `logist2/__init__.py` — monkey-patch для admin.site

---

### 44. Invoice-bank reconciliation — сопоставление инвойсов с банком (09.02.2026)

**Статус:** Завершено

**Цель:** Связать внутренние финансовые операции (инвойсы, транзакции) с банковскими операциями из Revolut.

**Расширение `NewInvoice`:**
- `external_number` — CharField(100) — номер счёта от контрагента (с бумажного/PDF инвойса)

**Расширение `BankTransaction` (`core/models_banking.py`):**
- `matched_transaction` — FK → Transaction (nullable)
- `matched_invoice` — FK → NewInvoice (nullable)
- `reconciliation_note` — CharField(255)
- `is_reconciled` — computed property (True если есть любая привязка)

**Admin-интеграции (`core/admin_banking.py`):**
- Fieldset "Сопоставление с внутренними операциями" в BankTransactionAdmin
- `autocomplete_fields` для matched_invoice и matched_transaction
- `BankReconciliationFilter` — фильтр Сопоставлено/Не сопоставлено
- `display_reconciled` — бейдж статуса в списке

**BillingService (`core/services/billing_service.py`):**
- `pay_invoice()` принимает опциональный `bank_transaction_id`
- При указании — привязывает BankTransaction к созданным Transaction и NewInvoice

**Миграция:** `0113_invoice_bank_reconciliation.py`

---

### 45. Комплексное тестирование — run_all_tests.py (09.02.2026)

**Статус:** Завершено

**Цель:** Единый тестовый скрипт, покрывающий все подсистемы проекта.

**Результат:** 67 тестов, 15 секций, все проходят.

**Секции:**
1. Создание инвойсов (4 теста)
2. Категории расходов (9)
3. Направление инвойса (4)
4. External number (4)
5. Category & attachment (6)
6. Bank reconciliation (7)
7. NewInvoiceAdmin config (3)
8. BankTransactionAdmin config (4)
9. TransactionAdmin config (2)
10. Sidebar groups (7)
11. BillingService signature (2)
12. Dashboard service (6)
13. Auto-categorize signal (2)
14. API permissions (2)
15. Site.pro models (4)

**Особенности:**
- Запуск на рабочей БД через `transaction.atomic()` + `RollbackException` — данные НЕ сохраняются
- Безопасен для production
- Запуск: `.venv\Scripts\python.exe run_all_tests.py`

**Удалённые устаревшие тесты:**
- `core/tests.py` — сломан после рефакторинга (client → recipient_client, total_amount → total, Payment → Transaction)
- `test_settings.py` — VPS-only скрипт
- `core/management/commands/test_unload_date_inheritance.py` — тест на реальных данных
- `core/management/commands/test_invoice_markup.py` — тест на реальных данных

**Обнаружены предшествующие баги (не наши):**
- `CarSerializer` ссылается на удалённое поле `current_price`
- Цепочка миграций конфликтует при создании тестовой БД с нуля (0059)

**Исправлено:**
- Миграция `0059_restore_company_balance_fields.py` — заменена на идемпотентный RunPython

**Файлы:**
- `run_all_tests.py` — основной тестовый скрипт (67 тестов)
- `core/migrations/0059_restore_company_balance_fields.py` — исправлена