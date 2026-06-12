# Changelog

Все значимые изменения в Logist2 будут документироваться в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект следует своей внутренней схеме версий (см. git-теги).

## [Unreleased]

### Added — AI-планировщик дел (2026-06-12)

- **AI-агент на Anthropic** (`docs/AI_AGENT_PLAN.md`): анализ входящей
  почты (кто пишет, зачем, к каким контейнерам/авто относится) →
  предложения дел, вопросы владельцу с постоянной памятью ответов,
  утренний дайджест-план, исполнение дел tool-use циклом («Поручить ИИ»).
- **Модели агента** (миграция 0002): `AgentRun` (запуски + токены +
  стоимость), `AgentAction` (журнал намерений: PROPOSED → APPROVED →
  EXECUTED — единственный путь изменений от агента), `AgentQuestion`,
  `AgentMemory` (вечная память с embedding-retrieval), `AgentPolicy`
  (автономия по типам действий: ASK/AUTO/DISABLED). `Task` расширен
  полями `origin`/`source_email`/`ai_summary`.
- **Страница «Дела + ИИ»** `/admin/tasks-board/`: активные дела,
  предложения агента (принять/отклонить с причиной — причина становится
  правилом памяти), вопросы агента (ответ инлайн → дистилляция в память),
  дайджест, журнал и сводка автономии.
- **Celery**: `analyze_new_emails_task` (каждые 10 мин),
  `morning_digest_task` (будни 07:00), `execute_task_by_agent_task`,
  `distill_question_task`. Всё no-op до `AGENT_ENABLED=True`.
- **Бюджет и безопасность**: дневной лимит LLM-расходов
  (`AGENT_DAILY_BUDGET_USD`), деньги/удаление данных агенту недоступны,
  высокорисковые действия не выполняются автоматически даже в AUTO-режиме.
- Тесты: `core/tests/test_agent.py` (33 теста, LLM замокан).

### Added — аудит раунд 3 (AUDIT_ROUND3, 2026-06-11)

- **Счётчик серий документов** (`SeriesCounter`, миграция 0184): номера
  NewInvoice/Transaction/AutoTransport выдаются атомарным upsert —
  без гонок и `IntegrityError` при параллельном создании (B1).
- **Валютный инвариант** (миграция 0185): `CheckConstraint(currency='EUR')`
  на Transaction/NewInvoice (`NOT VALID` — исторические USD-строки не
  трогаются); проверка совпадения валют при привязке банковской
  операции к инвойсу (B2).
- **Redis-лок** на `sync_bank_and_reconcile` и `sync_sitepro_invoices` —
  параллельные запуски синхронизации больше не накладываются (B3).
- **Контрактные тесты интеграций** Revolut и site.pro
  (`test_revolut_contract.py`, `test_sitepro_contract.py` + мок-фикстуры
  HTTP) (T1).
- **Конкурентные тесты** (`test_concurrency.py`): гонка нумерации,
  параллельные платежи по одному инвойсу (PG-профиль) (T2).
- **pg_trgm + GIN-индексы** (миграция 0186) на `NewInvoice.number/
  external_number`, `Client.name`, `Car.vin/brand` — поиск по `icontains`
  через индекс (P3).
- **Post-deploy smoke** в `deploy.ps1`: после рестарта сервисов
  проверяются `/health/` и `/admin/login/`; проведены restore-учения
  (журнал в `docs/BACKUPS.md`) (R7).

### Changed — аудит раунд 3

- **Распил монолитов**: `models_*.py` → пакет `core/models/`,
  `admin_*.py` → пакет `core/admin/` (старые пути — реэкспорт-шимы) (A1).
- **NewInvoice разгружен**: пивот-таблица позиций → admin-хелпер,
  смена серии и кассовые платежи → `BillingService` (A2);
  `create_expense_view` → `BillingService.create_expense_from_bank_transaction`
  + тесты (A3); `_create_car_services_if_needed` вынесен из сигнала в
  `CarLifecycleService` (A4); резолвинг email-групп и санитизация HTML —
  в `core/services/email_compose.py` (A5).
- **Производительность**: `Container.storage_cost/days` — аннотации
  `with_storage_aggregates()` вместо N+1 (P1); батч-резолвинг каталога
  услуг в `recalculate_cars_total_price_task` (P2); clamp `limit` +
  рейт-лимиты в API (P4).
- **Squash миграций**: вся история (178 файлов) свёрнута в
  `0001_squashed_baseline` с сохранением данных (сид категорий расходов,
  pg_trgm-индексы); старые файлы будут удалены после прохождения прода (R5).
- **Лок зависимостей (pip-tools)**: `requirements.in`/`requirements-dev.in`
  — прямые зависимости, `requirements*.txt` — скомпилированные локи (R6).
- **CI**: bandit-MEDIUM блокирующий (R2); ratchet покрытия core 32 → 38,
  критичные модули 55 → 60 + `core.models.billing` (T2); ruff format
  по всему репозиторию (одноразовый reformat).
- **Сессии**: `SESSION_ENGINE=cached_db` — логины переживают рестарт
  Redis (R1). Включён `ENCRYPTION_KEY_REQUIRED` после ротации ключа (R3).
- **Безопасность**: `mark_safe` → `format_html` в admin-дисплеях (R2);
  legacy-зеркало `/api/` удалено — отвечает 410 Gone, клиенты переведены
  на `/api/v1/` (R4); ревизия `except Exception` в денежных путях (B4).

### Fixed — аудит раунд 3

- **`logist2.settings.test_migrations`**: из-за подмены БД в `base.py`
  под pytest CI-джоба «tests with migrations» молча гоняла тесты на
  SQLite вместо PostgreSQL. Теперь профиль явно возвращает PostgreSQL.
- **Кросс-тестовое загрязнение кэша**: `company:default_id` утаскивал pk
  из предыдущего теста (флаки на PG) — кэш чистится перед каждым тестом.
- **`recalculate_storage`**: удалена ветка пересчёта контейнеров,
  обращавшаяся к несуществующим полям.

### Changed

- **Единая система учёта хранения** (миграция 0183): у `Container` удалены
  собственные поля `rate`/`free_days`/`days`/`storage_cost` — параллельная
  система с ручной ставкой (дефолт 5 €), не связанная с каталогом услуг
  склада и обнулявшая накопленную стоимость при выходе из UNLOADED.
  Хранение контейнера отдельно не биллингуется (решение владельца),
  теперь «Складирование» контейнера — read-only агрегат хранения его
  машин (`storage_cost` = сумма, `days` = максимум по машинам).
  Единственный источник ставок — услуга «Хранение» склада +
  `Warehouse.free_days`.

### Added

- **Ежедневный пересчёт хранения** (`refresh_unloaded_storage_daily`,
  beat 00:30): денормализованные `Car.days/storage_cost/total_price` у
  машин на складе освежаются раз в сутки — `Sum("storage_cost")` на
  дашборде и сортировка «Хран» в админке больше не отстают от реальности.
- **`Car.get_storage_days()`** — единственное место с формулой платных
  дней хранения (день разгрузки/передачи включаются, бесплатные дни
  склада вычитаются). Дубли формулы в `update_days_and_storage`,
  `calculate_storage_cost`, `CarAdmin.days_display` и
  `services_summary_display` заменены вызовом хелпера.
- **Индексы**: `ContainerEmailLink(container, is_read)` (фильтр
  непрочитанных писем в списке контейнеров) и
  `NewInvoice.external_number` (`db_index`, поиск в админке/API) —
  миграция 0182.

### Changed

- **PNG-иконки админки сжаты с 27.7 MB до 0.8 MB**
  (`scripts/compress_icons.py`): ресайз до 640px + квантизация в палитру.
  Раньше открытие карточки инвойса/клиента тянуло картинки по 4-5 MB.
- **Массовые admin-actions смены статуса Car**: пересчёт
  `total_price/days/storage_cost` ушёл в Celery
  (`recalculate_cars_total_price_task`) — HTTP-запрос больше не висит
  на синхронном цикле «SELECT+UPDATE на каждую машину».
- **`ContainerAdmin.save_formset`**: пересчёт цен машин контейнера после
  обновления THS — одним `bulk_update` вместо N×`save()` с полным
  сигнальным каскадом (WS, Celery, email) на каждую машину;
  `bulk_update_container_statuses` — условная агрегация одним SQL вместо
  `exists()/count()`+загрузки машин на каждый контейнер.
- **Кабинет клиента**: список контейнеров постраничный (50/стр.),
  количество машин — `Count` в SQL вместо prefetch всех машин и фото.
- **`find_car_model_image_url`**: частичный подбор картинки модели
  выполняется в БД (prefix-match через `Substr/Length`) вместо загрузки
  всех `CarModelImage` в память на каждое открытие карточки авто.

### Fixed

- **Карточка клиента**: удалён вызов несуществующего
  `client.get_balance_summary()` в `ClientAdmin.change_view` — каждый
  открытый клиент писал ошибку в лог; контекст шёл в шаблон, который его
  не использовал.

- **Иммутабельный леджер транзакций** (`Transaction._validate_ledger_rules`):
  денежные поля проведённой (COMPLETED) транзакции заморожены
  (`LEDGER_FROZEN_FIELDS`: сумма, тип, стороны, инвойс, дата);
  FSM статусов Tx (`COMPLETED → только CANCELLED`, `CANCELLED` —
  терминальный); удаление COMPLETED/CANCELLED запрещено и в модели, и в
  админке (включая bulk delete). Исправление ошибки — отмена + новая
  транзакция либо сторно (`BillingService.refund`).
- **FSM статусов Car/Container** (`ALLOWED_STATUS_TRANSITIONS` в
  `core/models/containers.py`): вперёд и назад — свободно (исправление
  ошибок), но выход из `TRANSFERRED` — только осознанный откат в
  `UNLOADED` (передача финансово значима: останавливается хранение).
  Массовые admin-actions через `queryset.update()` — escape hatch.
- **DB-констрейнты на финансах (миграция 0181)**: `amount >= 0`
  (Transaction), `subtotal/total/paid_amount/discount >= 0` и
  `due_date >= date` (NewInvoice), `quantity > 0`,
  `unit_price/total_price >= 0` (InvoiceItem), уникальность
  `BankTransaction.matched_transaction` (один платёж не может закрывать
  две банковские операции). Python-валидация обходится bulk/raw SQL —
  констрейнты в БД нет.
- **`BillingService.create_payment_for_bank_match()`** — единственная
  точка создания платежа при ручной привязке
  `BankTransaction.matched_invoice` (admin-форма, «Создать расход»,
  массовые actions). Идемпотентна.

- **FSM статусов инвойса** (`NewInvoice.ALLOWED_STATUS_TRANSITIONS` +
  проверка в `save()`, по образцу `AutoTransport`): запрещены бессмысленные
  переходы — `PAID → DRAFT/CANCELLED/OVERDUE`, `CANCELLED → PAID`,
  `LINKED_PAID → OVERDUE/CANCELLED`. Легитимные пути (REFUND-пересчёт
  `PAID → PARTIALLY_PAID/ISSUED`, смена серии, отмена) разрешены.
- **`BillingService.register_incoming_bank_payment()`** — единая точка
  регистрации входящих банковских платежей по исходящим инвойсам: для
  клиентов всегда пара `BALANCE_TOPUP + PAYMENT(BALANCE)` (авансовый счёт
  не уходит в минус), для контрагентов — одиночный `PAYMENT(TRANSFER)`.
  Используется привязкой BT → invoice и командой `auto_reconcile`.

### Changed

- **Сигнал-команда `auto_create_payment_on_bt_match` удалён**
  (`core/signals/bank.py`): платёж при привязке банковской операции к
  инвойсу больше не возникает «сам» из post_save — все точки привязки
  вызывают `create_payment_for_bank_match()` явно. Побочный фикс:
  ветка auto_reconcile «linked_only» (суммы не совпали, требуется ручная
  обработка) больше не получает неожиданный частичный платёж от сигнала;
  массовая привязка в админке перестала создавать одиночный
  PAYMENT(TRANSFER) от клиента (минусовой Client.balance).
- **Сверка балансов на тестовых данных**: `verify_balances --fix` —
  устранено 37 исторических расхождений (`stored != expected` по
  каноническому расчёту из COMPLETED-транзакций) у
  Warehouse/Line/Company.
- **Единый механизм формирования позиций инвойса**:
  `BillingService.create_invoice(cars=...)` теперь делегирует
  `NewInvoice.regenerate_items_from_cars()` (группировка по `short_name`,
  отдельная строка «Хран», услуги по типу выставителя) вместо собственного
  построчного цикла — два расходившихся механизма сведены к одному.
- **Обязательная валидация при сохранении**: `Transaction.save()` и
  `NewInvoice.save()` вызывают `full_clean()` при полных сохранениях
  (без `update_fields`) — проверка сторон транзакции, валюты и защита от
  переплаты работают из кода/shell/Celery, а не только из admin-форм.
- **Admin actions контейнеров переведены на Celery**: массовая загрузка
  фото с Google Drive (`sync_container_photos_gdrive_task`) и повторная
  отправка уведомлений email/Telegram (новая задача
  `resend_container_notifications_task` с принудительной отправкой) больше
  не выполняются синхронно в HTTP-запросе (риск таймаута gunicorn).

### Fixed

- **BT auto-pay без парного TOPUP**: ручная привязка банковской транзакции
  к клиентскому инвойсу создавала одиночный `PAYMENT(TRANSFER)` от клиента,
  уводя `Client.balance` в минус и показывая ложный долг в `total_balance`
  (тот же баг, что чинили в `auto_reconcile` в апреле 2026). Теперь —
  через `register_incoming_bank_payment()`.
- **Устаревший кэш дашборда**: ключи `dashboard:*` теперь инвалидируются
  при изменении `Transaction`/`NewInvoice`/`Car`/`Container`/`Company`
  (`invalidate_dashboard_cache()` в `cache_utils.py`) — раньше после
  платежа дашборд показывал старые цифры до конца TTL (5 мин).

### Removed

- Мёртвый код: `SimpleBalanceMixin` (`models_billing.py`, не использовался
  ни одной моделью) и legacy `find_line_service_by_container_count()`
  (`car_service_manager.py`).
- **503 файла `media/` сняты с git-трекинга** (`git rm --cached`) —
  медиа не должны храниться в репозитории (каталог в `.gitignore`).
- **История git переписана** (`git filter-repo --path media/ --invert-paths`):
  медиа-файлы и дампы удалены из всех прошлых коммитов, force-push в
  `master`, клон на сервере сброшен на новую историю.
- Локальный мусор из корня: `runserver.log` (2.5 MB), 4 дампа/SQL-бэкапа
  (~25 MB), `renumber_out.txt`, `test_db.sqlite3`.

### Performance

- **Дашборд компании**:
  - aged receivables/payables считаются одним SQL с `CASE WHEN` +
    `GROUP BY` вместо Python-цикла по всем неоплаченным инвойсам;
  - кэшируются ранее некэшированные `get_cash_wallet()`,
    `get_recent_transactions()`, `get_recent_invoices()`.
- **`car_list_api`**: лимит 200 машин в выдаче + подсказка «уточните
  поиск» — у оптовых клиентов рендер полного списка блокировал request.
- **`BankTransactionAdmin`**: `show_full_result_count = False` — убран
  второй `COUNT(*)` по всей таблице на каждом changelist.

- **Аудит производительности и устранение узких мест (этапы A–E).**
  - **Админка контейнера**: смена `unload_date` больше не регенерирует
    инвойсы синхронно в HTTP — вынесено в Celery `on_commit` с
    дедупликацией по `car_id`; убран N+1 по `car.newinvoice_set`.
  - **`CarAdmin`**: ставка хранения берётся одним `Subquery` на весь
    список (`_storage_daily_rate_ann`), а не запросом `WarehouseService`
    на каждую строку (было до ~50 SELECT на страницу); `_bulk_updating`
    выставляется до первого `save()`, чтобы первый `car_post_save` не
    ставил лишнюю Celery-задачу пересчёта.
  - **Шаблоны/админка**: `.count()` заменён на `len(prefetch)` в портале
    (`car_detail`, `container_detail`) и в `EmailGroupAdmin`/`ContactAdmin`.
  - **Индексы БД (миграция 0180)**: `ContainerPhoto(container, is_public)`
    (раньше индексов не было вовсе), `CarPhoto(car,is_public)` /
    `(car,-uploaded_at)`, `Car(container,status)` / `is_important`,
    `Container(status,unload_date)` / `labels_printed_at`,
    `BankTransaction(connection,created_at)` / `(state,created_at)`.
  - **Клиентский портал/API**: префетч фото авто и `container_cars` в
    tracking, `select_related('author')` в News API, `.only()` для
    `container_cars` в карточке контейнера.
  - **ZIP-архивы фото** (`photos_authed`, `signed_photos`): сборка через
    `SpooledTemporaryFile` + `FileResponse` вместо `BytesIO`/`getvalue`
    (маленькие архивы в RAM, большие — на диск; снят риск OOM и двойного
    копирования).
  - **`BillingService.create_invoice`**: позиции создаются одним
    `bulk_create` вместо `item.save()` в цикле (раньше каждый `save()`
    пересчитывал итоги всего инвойса — N+1).
  - **Celery inline-fallback** пересчёта цен (`service_catalog`) теперь
    обновляет `days`/`storage_cost` наравне с `total_price` — при
    недоступном брокере поля больше не расходятся с БД.
  - **Кэш галереи** `container_photos:<n>` инвалидируется сигналом при
    изменении/удалении `ContainerPhoto` (загрузка в админке, GDrive sync).
  - **`AutoTransportAdmin`**: `list_per_page=50` + `show_full_result_count=False`.

### Changed

- **Рефакторинг (фазы 0–1): бизнес-логика из админки в сервисы.**
  - **Фаза 0 — сеть безопасности.** Добавлены характеризующие тесты,
    фиксирующие поведение перед рефакторингом: пересчёт балансов
    `Client`/`Company`/`Warehouse`/`Line`/`Carrier`
    (`test_balance_recalc.py`), каскад цены авто и хранения
    (`test_price_cascade.py`), константные бюджеты SQL на горячих
    выборках (`test_query_budgets.py`), генерация позиций инвойса из услуг
    машины (`test_invoice_from_cars.py`).
  - **Фаза 1 — оркестрация услуг авто** вынесена из
    `CarAdmin._save_model_inner` в `core/services/car_admin_service.py`
    (декаплено от `request`: на вход обычный mapping POST). Три приватных
    helper-метода админки удалены.
  - **Каскады контейнера** (синхронизация склада/статуса/даты разгрузки,
    THS) вынесены из `ContainerAdmin._save_model_inner` в
    `core/services/container_lifecycle_service.py`.
  - **Управление каскадными сигналами** (`signals_disabled`, `CAR_SIGNALS`,
    `INVOICE_SIGNALS`) собрано в одном месте —
    `core/services/cascade_control.py` (раньше дублировалось в админке
    контейнера).
  - Новые сервисы покрыты unit-тестами (`test_car_admin_service.py`,
    `test_container_lifecycle.py`). Поведение не изменилось — весь набор
    тестов зелёный.
- **Рефакторинг (фаза 2): единый источник цены — `CarService`.**
  - **Мастер инвойсов** (`get_invoice_cars_api`) теперь берёт стоимость
    услуг линии/перевозчика из агрегатов `CarService`, а не из legacy-полей
    `Car` (`ocean_freight`/`ths`/`delivery_fee`/`transport_kz`). JSON-ключи
    и JS не менялись.
  - **Прекращена запись legacy fee-полей** `Car` (`unload_fee`,
    `delivery_fee`, `loading_fee`, `docs_fee`, `transfer_fee`,
    `transit_declaration`, `export_declaration`, `extra_costs`,
    `complex_fee`, а также `ths`/`markup`/`declaration_fee`/`rate`/
    `free_days` при синхронизации). Источник истины — `CarService`.
    Удалены методы `Car.set_initial_warehouse_values()` и
    `Car.apply_warehouse_defaults()`, мёртвый `Container.sync_cars_after_edit()`.
  - `sync_with_container` / `update_related` / `sync_cars_after_warehouse_change`
    обновляют только живые поля (статус/склад/даты + денормализованные
    `days`/`storage_cost`/`total_price`).
  - Колонки legacy-полей из БД **не удалены** — это отдельный шаг после
    периода наблюдения. Контракт покрыт тестами
    (`test_phase2_legacy_decouple.py`).
- **Рефакторинг (фаза 3): сигналы — «события» vs «команды».**
  - **PR 3.1 — классификация.** Все `@receiver`-обработчики размечены
    как `EVENT` (нотификация/кэш/WS — оставляем сигналами),
    `COMMAND/denorm` (реактивный пересчёт производных данных) или
    `COMMAND/orchestration` (кандидаты на явный вызов). Полная таблица —
    `docs/signals_classification.md`.
  - **WS-нотификация** `car_post_save` вынесена в единственную
    реализацию `car_lifecycle_service.send_car_ws_notification`; дубликат
    в сигнале удалён. В `car_post_save` добавлены маркеры `EVENT`/`COMMAND`.
  - Перенос `COMMAND/orchestration` в явные вызовы с последующим снятием
    сигнал-обёрток (PR 3.2/3.3) и снятие защитных флагов (PR 3.4)
    **отложены** — выполняются по одному обработчику с наблюдением, т.к.
    сигналы стреляют из множества точек записи.
- **Рефакторинг (фаза 4): консистентность балансов.**
  - **Единая каноническая формула** ожидаемого баланса —
    `Transaction.expected_entity_balance()` / `_balance_queryset_for()`:
    для контрагентов (company/warehouse/line/carrier) учитываются только
    транзакции **без** инвойса, для клиента — все. `recalculate_entity_balance`
    переиспользует её.
  - **Исправлен баг ложных расхождений.** `_collect_balance_mismatches`
    (Celery `check_balance_consistency`/`repair_balance_consistency`),
    `BalanceManager.validate_balance_consistency` и
    `check_data_integrity` считали ожидаемый баланс контрагентов по
    **всем** COMPLETED-транзакциям (без фильтра по инвойсу) — это давало
    ложные mismatch'и, а `--fix`/repair мог затереть верный баланс
    инвойсными платежами. Теперь все используют каноническую формулу.
  - **PR 4.3 — команда-ревизор** `python manage.py verify_balances`
    (`--fix`, `--entity`, `--no-invoices`): сверяет `balance` сущностей и
    `paid_amount` инвойсов с расчётом из транзакций; страховка от
    рассинхрона денормализованного `balance`. Покрыто тестами
    (`test_verify_balances.py`).
  - **PR 4.1/4.2 (денормализация `open_fact_debt`/`open_pardp_receivable`/
    `total_balance` в поля) — НЕ делалась осознанно.** Поле `balance` уже
    денормализовано (сигнал `Transaction`), а горячие списки в админке уже
    считаются одним SQL через Subquery-аннотации (`annotate_partner_balance`,
    Client-subquery) — 0 доп. запросов на строку. Денормализация `open_*`
    дала бы выигрыш только на одиночных формах (1–2 aggregate), но
    потребовала бы хрупкой сигнальной машинерии пересчёта на каждое
    изменение инвойса. План предписывает делать это «только когда упрётесь
    в производительность списков», чего сейчас нет.
- **Рефакторинг (фаза 5): косметика / долги «по вкусу».**
  - **Пагинация дашборда клиента.** Личный кабинет (`client_dashboard`)
    больше не грузит все авто клиента со всеми публичными фото — список
    постранично (`Paginator`, 50/стр.), а статистика (всего/в пути/
    выдано) считается одним агрегатом по БД (верна независимо от
    страницы). В шаблон добавлена навигация по страницам.
  - **Осознанно отложено** (по плану — условные / только в окно
    обслуживания):
    - унификация `max_digits` Car/Container → 15: table rewrite с локом
      на большой таблице, только в плановое окно обслуживания;
    - streaming-генерация ZIP (`zipstream-ng`): архивы уже собираются
      через `SpooledTemporaryFile`/`FileResponse` (этап A–E), переход
      нужен только если архивы реально вырастут;
    - распил `models_billing.py` (2000+ строк) на пакет: крупный
      механический рефактор ради читаемости, высокий churn при низкой
      ценности — отдельной задачей с фасадом обратной совместимости.

### Added

- **База картинок моделей авто (`CarModelImage`)**: иллюстрация в карточке
  авто теперь управляется через админку (CORE → «Картинки моделей авто»),
  без ручной заливки PNG на сервер по SSH.
  - При загрузке картинка автоматически нормализуется под единый канвас
    800×450 (16:9), WebP, прозрачный фон, центрирование — все авто в
    карточках выглядят одинаково (`normalize_car_model_image_*` в
    `core/services/photo_optimize.py`).
  - Подбор в карточке: точное `марка+год` → марка без года → частичное
    совпадение марки → старые статические PNG → заглушка.
  - Картинка в карточке авто обёрнута в адаптивную рамку 16:9
    (`object-fit: contain`), корректно отображается на мобильных.
  - Команда `python manage.py import_car_model_icons` — разовый импорт
    существующих `static/icons/car_models/*.png` в БД (идемпотентно).
  - Нормализация обрезает прозрачные поля по альфа-каналу и масштабирует
    авто до заполнения рамки (раньше мелкие исходники оставались мелкими).
  - **Загрузка фото прямо из карточки авто**: при наведении на картинку —
    кнопка «+», выбор файла из проводника, авто-привязка к марке+году
    текущего авто и нормализация (эндпоинт
    `admin/core/car/<id>/upload-model-image/`).
- **Карточка авто (UI)**: поля «Дата разгрузки»/«Дата передачи» вынесены
  отдельной строкой сразу после адреса склада; автодополняемые поля
  «Клиент»/«Склад» приведены к единому виду с остальными полями
  (тема `select2-container--admin-autocomplete`); у картинки убраны фон и
  рамка — сливается со страницей.
- **Персональные Telegram-ссылки для привязки клиента**: у `Client` появился
  `telegram_link_token` и ссылка-приглашение `https://t.me/<bot>?start=<token>`
  в карточке клиента. Клиент жмёт Start → `process_telegram_starts()`
  привязывает его `chat_id` автоматически (Celery-задача
  `process_telegram_starts_task` раз в минуту + команда `telegram_link`).
  Настройка `TELEGRAM_BOT_USERNAME`.
- **Telegram-уведомления о разгрузке**: параллельный email-каналу способ
  оповещения клиентов о планируемой/фактической разгрузке контейнера и
  разгрузке отдельного ТС. Новый `core/services/telegram_service.py`
  (`TelegramNotificationService`), отправка через Telegram Bot API.
  - У `Client` добавлены поля `telegram_chat_id` и `telegram_enabled`.
  - `NotificationLog` получил поле `channel` (`EMAIL` / `TELEGRAM`) —
    дедуп отправок независим по каналам.
  - Celery-задачи `send_planned_notifications_task` /
    `send_unload_notifications_task` / `send_car_unload_notification_task`
    шлют оба канала (сбой одного не влияет на другой).
  - Админка: блок «Telegram-уведомления» у клиента, колонка/фильтр
    «Канал» в логах, действия «📨 Telegram: уведомить…» у контейнера и ТС.
  - Команда `python manage.py telegram_updates` — поиск `chat_id`
    клиентов через getUpdates.
  - Настройки `TELEGRAM_BOT_TOKEN`, `TELEGRAM_NOTIFICATIONS_ENABLED`,
    `TELEGRAM_API_TIMEOUT` (env), `env.example` обновлён.
- **M1**: `CHANGELOG.md` (этот файл) + обновлённый `README.md`
  (актуальный test count, H6-структура core-пакета).
- **M4**: новый CI-job `tests-with-migrations` в `.github/workflows/ci.yml`
  с ночным расписанием — прокатывает все миграции на PostgreSQL и
  проверяет `makemigrations --check`. Новый профиль настроек
  `logist2.settings.test_migrations` (включены миграции, реальный PG).
- **M5**: `autocomplete_fields` для тяжёлых FK в админке —
  `CarAdmin` (client/warehouse/line/carrier/container),
  `ContainerAdmin` (line/warehouse),
  `TransactionAdmin` (11 FK от/к Client/Warehouse/Line/Carrier/Company + invoice),
  `ClientUserAdmin/NewsPostAdmin/TrackingRequestAdmin/AutoTransportAdmin/
  BankConnectionAdmin/SiteProConnectionAdmin`.
  Расширены `search_fields` в `CarAdmin` (`client__name`,
  `container__number`) и `TransactionAdmin` (`from_client__name`,
  `to_client__name`).
- **M6**: `scripts/install_systemd.sh` — идемпотентная установка
  systemd-unit'ов с поддержкой `PROJECT_DIR` и автоматическим бэкапом
  при отличиях.
- **M7**: structured logging:
  - `core/middleware_logging.py` — `RequestContextMiddleware` сохраняет
    `request_id` (из `X-Request-ID` или сгенерированный uuid),
    `user_id`, `path`, `method` в `contextvars` (async-safe);
    `RequestContextFilter` пристёгивает их к каждой LogRecord;
    `get_request_id()/set_request_id()` для корреляции в Celery-таски;
    middleware возвращает `X-Request-ID` в Response header.
  - `RotatingFileHandler` (50 MB × 10) в `logist2/settings/base.py`
    включается при `LOG_DIR=...` env-var; не активен в dev/CI.
  - JSON-формат через `python-json-logger>=3.1` при `LOG_FORMAT=json`.
  - `docs/LOGGING.md` — гайд по env-vars, jq-cookbook, Sentry, откат.
- **scripts/gunicorn.service** — добавлен (раньше был только устаревший
  `logist2.service`); все unit'ы синхронизированы с прод-конфигом
  (paths `/var/www/www-root/data/www/logist2`, OOM-guardrails,
  `EnvironmentFile=.env`).
- **CarrierTruck / CarrierDriver ModelAdmin** (`899903f`): отдельные
  страницы changelist'а (раньше только inline в `CarrierAdmin`).
  `search_fields` с `carrier__name`, `autocomplete_fields=('carrier',)`,
  `list_select_related=('carrier',)`.
- **`/admin/clients-autocomplete/` endpoint** (`e089872`):
  server-side AJAX-поиск клиентов для `ClientAutocompleteFilter`.
  `core/views_admin_autocomplete.py`, `@staff_member_required`,
  Select2-совместимый JSON (`{"results": [{"id", "text"}, ...]}`,
  лимит 20).
- **`RecipientClientAutocompleteFilter`** (`e089872`) для
  `NewInvoice.recipient_client` — параметризованный наследник
  `ClientAutocompleteFilter` (новый class-attr `field_name`).
- **`cars-autocomplete/` endpoint в `NewInvoiceAdmin`** (`1830e5f`):
  server-side поиск машин по VIN / brand / client name (раньше
  Select2 фильтровал локально по топ-200 → машины вне топ-200
  не находились). Лимит 20.
- **Тесты guard'а регенерации позиций.** `RegenerateItemsGuardTest`
  в `core/tests/test_billing.py` (5 тестов): PAID / LINKED_PAID /
  CANCELLED не пересоздаются, DRAFT регенерируется, `force=True`
  обходит guard.
- **CI security-scan job** (`.github/workflows/ci.yml`): `pip-audit`
  (CVE в зависимостях) + `bandit` (SAST по `core`/`logist2`).
  `pip-audit` и `bandit` (high severity) — **блокирующие**; `bandit`
  medium (`mark_safe`) пока advisory (`continue-on-error`) как legacy-долг.

### Changed

- **M3**: ruff `select` расширен до `["E","F","W","I","UP","B","C4","DJ","RUF"]`
  (был только базовый набор). Добавлен продуманный `ignore`-список
  (Cyrillic ambiguous-chars, Django-специфика, `RUF012` для
  `ModelAdmin.list_display`, settings star-imports). `target-version = "py310"`
  + `UP017` в ignore (защита от регрессии `datetime.UTC` на Python 3.10).
- **M3**: `django-upgrade` pre-commit hook — `--target-version=5.2`
  (был `5.1`).
- **M3 (pre-commit)**: ruff-pre-commit получил явный
  `--target-version=py310` (страховка от регрессии после
  hotfix `a91de68`).
- **M7**: Sentry `LoggingIntegration` теперь `level=INFO, event_level=ERROR`
  — INFO/WARNING больше не создают отдельные events, остаются только
  breadcrumbs для контекста ошибок.
- **Git workflow rule** (`.cursor/rules/git-workflow.mdc`):
  добавлен шаг «обновить CHANGELOG» в раздел «Заканчиваем работу».
- **`ClientAutocompleteFilter`** (`e089872`): прокачка ВСЕХ клиентов
  в HTML changelist'а → server-side AJAX через
  `/admin/clients-autocomplete/`. Параметризация `field_name` —
  можно унаследовать для других FK на Client.
- **`NewInvoiceAdmin.cars` Select2** (`1830e5f`): локальный фильтр по
  топ-200 → server-side AJAX (`cars-autocomplete/`). `extra_context["cars"]`
  теперь содержит только уже выбранные машины этого инвойса
  (раньше — 200 свежих + selected merged). Каждая страница change-формы
  ≈30–50 KB легче.
- **`NewInvoiceAdmin.list_filter`** (`e089872`): `"recipient_client"`
  заменён на `RecipientClientAutocompleteFilter` — раньше Django рисовал
  стену ссылок на каждого клиента в правом sidebar.
- **CI: порог покрытия `core/` 30% → 32%** (ratchet по фактически
  измеренному уровню; держим, чтобы новый код не снижал процент).
- **CI: `tests-with-migrations` теперь обязателен на каждом PR.** Убраны
  требование label `run-migrations-ci` и `labeled`-триггер — прогон всей
  цепочки миграций на чистой PostgreSQL ловит регрессии схемы до master,
  а не только ночным job'ом.
- **Унификация путей регенерации инвойсов.** `car_post_save`
  (`core/signals/car.py`) теперь использует общий Celery-путь
  `_deferred_invoice_regeneration` из `core/signals/car_service.py`
  вместо собственной синхронной ветки — единая защита guard'ом и
  единое поведение для всех триггеров (Car / CarService / авто-транспорт).
- **`AutoTransport` → генерация инвойсов только при переходе в `FORMED`.**
  `autotransport_post_save` (`core/signals/autotransport.py`) запускает
  `_queue_or_run_generate_invoices` только когда статус *меняется* на
  `FORMED` (новый `autotransport_pre_save` фиксирует старый статус).
  Раньше — на каждом сохранении уже сформированного транспорта.
- **`float()` → `Decimal()` в денежных полях** (`core/admin/partners.py`):
  legacy-поля склада в admin-change-view считаются через `Decimal`
  (+`InvalidOperation`-guard), без потери точности.
- **CI security-scan переведён в блокирующий** (`pip-audit` + `bandit`
  high) — см. раздел Added.

### Fixed

- **Python 3.10 совместимость** (`a91de68`): откат автоматической
  подмены `datetime.timezone.utc` → `datetime.UTC` (3.11+) в трёх
  файлах (`models_banking.py`, `services/gmail_client.py`,
  `tests/test_email_matcher.py`). Возникло после M3
  `ruff --unsafe-fixes` и уронило `daphne` на проде.
- **Select2 + warehouse_address.js** (`88112ed`): после M5
  (`autocomplete_fields` для warehouse) JS не ловил `change`-event
  от Select2, т.к. слушал нативный `addEventListener('change', ...)`.
  Переподписка через `django.jQuery(...).on('change', ...)` с
  fallback на нативный лиснер.
- **КРИТИЧНО: защита оплаченных инвойсов от перезаписи.**
  `NewInvoice.regenerate_items_from_cars()` теперь no-op для статусов
  PAID / LINKED_PAID / CANCELLED (guard через
  `REGENERATABLE_INVOICE_STATUSES`, параметр `force=True` для обхода).
  Раньше любое сохранение `Car` синхронно перегенерировало ВСЕ его
  инвойсы без фильтра статуса — удаляло позиции и перезаписывало `total`,
  нарушая инвариант «оплачен = total совпадает с paid_amount».
  Дополнительно `car_post_save` запускает регенерацию только при
  изменении ценообразующих полей (warehouse/line/carrier/unload_date)
  или создании машины и фильтрует инвойсы по
  `REGENERATABLE_INVOICE_STATUSES`.
- **Сортировка импортов** в `core/admin/billing/invoice.py` (`ruff I001`).
- **Слабые хеши помечены `usedforsecurity=False`** (bandit high):
  `hashlib.sha1` в `core/services/ai_rag.py` (cache-key эмбеддингов) и
  `hashlib.md5` в `core/templatetags/email_extras.py` (выбор цвета
  аватара) — не криптографическое применение.
- **Обновлены уязвимые зависимости** (`requirements.txt`, по `pip-audit`):
  `cryptography` 45.0.3→46.0.7, `Twisted` 24.11.0→26.4.0,
  `pyOpenSSL` 25.1.0→26.0.0, `urllib3` 2.5.0→2.7.0, `requests`
  2.32.5→2.33.0, `idna` 3.10→3.15, `python-dotenv` 1.0.1→1.2.2,
  `sqlparse` 0.5.3→0.5.4, `pyasn1` 0.6.1→0.6.3, `cffi` 1.17.1→2.0.0
  (зависимость `cryptography`). `pip-audit` — 0 уязвимостей.

### Removed

- **M2**: переменная `CORS_ALLOWED_ORIGINS` из `env.example` —
  фронтенд живёт на том же origin, `django-cors-headers` не
  установлен. Закомментировано как подсказка на будущее.
- **M6**: устаревшие unit-файлы `scripts/logist2.service` (старый
  путь к gunicorn) и `scripts/caromoto-lt.service` (легаси неиспользуемый).
- **`filter_horizontal = ("cars",)` + `class Media` в NewInvoiceAdmin**
  (`1830e5f`): была мёртвая конфигурация (UI давно рисуется
  кастомным шаблоном, а filter_horizontal на change-форме не
  отображался). Заодно убран `SelectBox.js` / `SelectFilter2.js` из Media.
- **`experiment_photos/` из git-индекса** (6 JPG, ~6 MB бинарников) —
  каталог добавлен в `.gitignore`, файлы оставлены на диске.
- **Мёртвый код `invoices_display` / `payments_display`**
  (`core/admin/partners.py`): методы импортировали несуществующие
  модели `Invoice`/`Payment` и не использовались в `fieldsets`.

### Notes

- **squashmigrations** (`25ffc97`): попробовано на 169 миграциях,
  отложено. Django генерирует синтаксически невалидный squashed-файл
  (ссылки `core.migrations.0041_*.func` не валидный Python), требует
  ручного порта ~20 RunPython-функций из 17 файлов. Риск
  data-corruption > выигрыш (~30 сек на свежей установке). В roadmap
  фиксированы триггеры возврата: ≥250 миграций или стабилизация
  RunPython-добавлений. Альтернатива на будущее: fresh-start вместо
  squash в плановое downtime-окно.

---

## [2026-05] — High-задачи roadmap'а после Critical-блока

Все 7 пунктов раздела HIGH в
[`docs/ROADMAP_2026-05_high_medium.md`](docs/ROADMAP_2026-05_high_medium.md)
закрыты. Содержание ниже сгруппировано по разделам Keep a Changelog.

### Added

- **H1 — onboarding для тестов.** `requirements-dev.txt`
  (`pytest`, `pytest-django`, `pytest-cov`, `ruff`, `freezegun`),
  README-раздел «Setup for development», блок про
  `DJANGO_SETTINGS_MODULE`.
- **H4 — автоматизированные бэкапы PostgreSQL.**
  `scripts/server_pg_backup.sh` (cron `30 3 * * *`, retention 30 дней,
  smoke `pg_restore --list`), `scripts/install_logist2_backup.sh`
  (idempotent bootstrap), Celery beat `check-backup-freshness-daily`
  (Sentry warning при freshness > 36 ч), `docs/BACKUPS.md`.
- **H5a — signed URLs для фото контейнеров.**
  `core/services/signed_urls.py` (HMAC через `TimestampSigner`,
  TTL 1 ч), новый view `serve_signed_photo`, обновлены
  `get_container_photos` и `download_photos_archive`
  (`container_token` обязателен для ZIP). Логирование загрузок.
  Тесты `core/tests/test_signed_photos.py` (18/18).
- **H7.3 — `pytest-env` в `requirements-dev.txt`.** Override
  `DJANGO_SETTINGS_MODULE` до того, как `pytest-django` его прочитает.
  Защищает от ситуации, когда в шелле остаётся env-var от
  `runserver`/`manage.py check` и тесты падают на `FieldDoesNotExist`.

### Changed

- **H2 — переключение дефолта на `logist2.settings.dev`.**
  `manage.py`, `wsgi.py`, `asgi.py`, `celery.py` теперь по умолчанию
  загружают dev-профиль. На сервере systemd-юниты (gunicorn, daphne,
  celery, celerybeat) и `scripts/deploy.ps1` явно выставляют `prod`.
  `scripts/sync_photos_cron.sh` — `prod`, `scripts/run_all_tests.py` —
  `test`, `scripts/create_test_client.py` — `dev`.
- **H6a — `core/models.py` → пакет `core/models/`.** 11 подмодулей по
  доменам (`cars.py`, `containers.py`, `clients.py`, `warehouses.py`,
  `carriers.py`, `lines.py`, `company.py`, `services.py`,
  `auto_transport.py`, `tasks.py`, `_vehicle_types.py`).
  `__init__.py` реэкспортирует все классы. Самый большой файл —
  `cars.py` (621 строка), остальные ≤ 280. Миграций не добавлено,
  166 тестов прошли без изменений.
- **H6b — `core/admin_billing.py` → пакет `core/admin/billing/`.**
  10 подмодулей, `NewInvoiceAdmin` (~1460 строк) разнесён через
  миксины. Самый большой файл — `invoice_forms.py` (493 строки).
  Миграций не добавлено, 166 тестов прошли без изменений.
- **H6c — `core/views_website.py` → пакет `core/views_website/`.**
  7 подмодулей (`public.py`, `client_portal.py`, `api.py`,
  `tracking.py`, `photos_authed.py`, `ai_chat.py`, `signed_photos.py`).
  Реэкспорт 25 view-функций/классов. Smoke: все 19 URL `website:*`
  резолвятся, локальный сайт отвечает 200.
- **H6d — `core/signals.py` → пакет `core/signals/`.** 10 submodules
  по доменам, `__init__.py` импортирует их (триггерит
  `@receiver`-декораторы) и явно вызывает
  `connect_autotransport_signals()` + `connect_cache_invalidation_signals()`.
  Backward-compat реэкспорт для `core.admin.container` сохранён.
  Регистрация 28 receiver'ов проверена, 166 тестов прошли без
  изменений.
- **H7.2 — `.gitignore`: общее `!**/__init__.py`.** Заменили 4
  точечных negation одним wildcard'ом — будущие пакеты работают
  «из коробки».

### Removed

- **H3 — удалены 4 неиспользуемых пакета** из `requirements.txt`:
  `django-admin-interface`, `django-modeltranslation`,
  `django-colorfield`, `django-cleanup`. Ни один не был в
  `INSTALLED_APPS` и нигде в импортах. Тесты зелёные, прод поднялся.

### Fixed

- **H7.1 — `/api/track/`: 500 → 400 на битом JSON.** В
  `track_shipment` ловился `except Exception`, который проглатывал
  DRF `ParseError` → клиент видел generic 500, Sentry заваливался
  ложными ошибками. Достали `request.data` наружу `try`, добавили
  `except APIException: raise`. Покрыто 6 новыми тестами в
  `core/tests/test_track_shipment.py`.

### Deferred (TODO в roadmap'е, перенесены в Medium/будущие сессии)

- **H5b — CAPTCHA (hCaptcha) на `track_shipment` и
  `ContactMessageViewSet`.** План в `docs/PUBLIC_ENDPOINTS.md` §4.2.
- **H5c — CSP / Referrer-Policy / CORP-заголовки + закрыть
  `/media/photos/` через `X-Accel-Redirect`.** План в
  `docs/PUBLIC_ENDPOINTS.md` §4.1 и §4.3.
- Опциональный off-site бэкап (rclone в S3/Backblaze). TODO в
  `docs/BACKUPS.md`.

---

## [2026-05] — Critical 1+2+3

Коммит `6329968` — критические фиксы перед roadmap.

### Added

- **ENCRYPTION_KEY** — отдельный Fernet-ключ для шифрования
  Revolut/site.pro credentials в `core/encryption.py`. Поддержка
  `ENCRYPTION_KEY_FALLBACKS` для ротации, management command
  `rotate_encryption_key`, `ENCRYPTION_KEY_REQUIRED=True` для
  fail-fast в проде. `docs/ENCRYPTION_KEY.md`.
- **Money-critical tests** — отдельный `--cov-fail-under=55` в CI
  для critical-модулей (billing, banking, reconciliation).
- **Async signals** — тяжёлые пересчёты `Container.total_price` и
  каскадные обновления `CarService` вынесены в Celery
  (`recalculate_cars_total_price_task`). Защита от signal-storm
  при массовом импорте.

---

## [2026-05] — Мониторинг и инфраструктура

### Added

- **Dashboard системного мониторинга в админке**
  (`/admin/system-monitor/`): CPU, RAM, диск, процессы, статус
  systemd-сервисов. Используется `psutil`. Setup-скрипт для
  установки сервиса-сборщика метрик на сервере (`scripts/`).
- **Sentry для error monitoring** — `sentry-sdk[django,celery]`,
  переменные `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`,
  `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_PROFILES_SAMPLE_RATE`,
  `SENTRY_SEND_PII`.
- **Healthcheck endpoint** (P0 infrastructure hardening).
- **Admin action для регенерации Revolut JWT** + мониторинг
  состояния JWT-assertion.
- **Защита `certs/`, `.env`, `media/`** от удаления при
  `deploy.ps1 -Force`. `.gitignore` для `certs/`, `*.pem`, `*.cer`.

### Fixed

- **Gunicorn OOM recovery** + закалка VPS (overcommit, swap,
  systemd OOMScoreAdjust).

---

## [2026-04 → 2026-05] — Бизнес-фичи

### Added

- **AI-обработка сканов титулов и Dock Receipt**
  (`core/services/scan_extractor.py`, Claude Vision). VIN-валидация:
  check digit, NHTSA, cross-check с make/year. Обработка обратного
  кейса — VIN-опечатка в dock receipt. Подсветка различий, review с
  кандидатами. Auto-downgrade JPEG-рендера под лимит Claude Vision
  (5 MB).
- **Печать наклеек для контейнеров** (форматы Forpus), отметка
  «наклейки напечатаны» на контейнерах.
- **Gmail API интеграция — переписка по контейнерам**:
  - Phase 1 — чтение писем, привязка к контейнерам по теме/VIN,
    панель «Переписка» в карточке контейнера, дедупликация по
    содержимому.
  - Phase 2 — отправка/ответы из карточек Container, Car,
    AutoTransport. Composer с chip-полями (Кому/Cc/Bcc),
    группы адресатов, автокомплит контактов, подписи (text/HTML).
  - VIN-матчинг писем, M2M связь Email ↔ Container/Car.
  - Двусторонний sync «прочитано» Gmail ↔ карточки.
  - Beat-задача `rematch_container_emails` и polling 30s на фронте.
  - Фильтры Gmail-ингеста по ключевым фразам
    (`EmailIngestFilter`).
- **Поле «Номер букинга»** на контейнере.
- **Пометка «Важно»** на машине с автогенерацией задач и
  блокировкой статуса/автовоза.
- **AVBLC/PARBLC invoice series** + смена серии, поддержка BLC для
  входящих инвойсов (кассовые платежи поставщикам).
- **site.pro/Revolut/Paysera integration** — autoreconciliation,
  auto bank sync каждые 30 мин (Celery Beat),
  Revolut receipt downloads с throttle.
- **Personal cash wallet** — учёт наличных, expense tracking,
  скрипт топ-апа кошелька (`/admin/cash-income/`), управление
  банковскими картами с переводами и корректировкой баланса.
- **Linked invoices** (real BLC + official) с auto PAID sync.
- **Receipt uploads, expense analytics**, unified design system.
- **Bulk delete invoices** с транзакциями, recalculate all balances
  action.
- **Auto-compress uploaded photos** до 2560 px / JPEG q=85
  (`resize_photos` command, in-place downscaling).
- **Google Drive API v3** для folder listing и file download
  (вместо HTML-парсинга с обрезкой).
- **Тариф клиента работает как минимум**, а не как жёсткая
  фиксация. Распределяется только по услугам склада.
- **Фильтр клиентов** по состоянию баланса (долг/нулевой/переплата),
  показ долга по открытым инвойсам в карточке.
- **Audit-driven improvements**: производительность, UX,
  financial integrity (cleanup session — renumber FACT, KRE type,
  related_client; auto-payment signal, INCBLC series).
- **Команда `bookkeep_vs_bank`** + Celery аудит бизнес-правил.

### Changed

- **deploy.ps1** переведён на `git pull` (вместо `tar + scp`).
  Encoding fix для em dash. `chown` только key dirs, не весь репо.
- **AutoTransport** → автоматический переход
  `LOADED → DELIVERED → TRANSFERRED` (массовый, при transferred
  всех машин).
- **Container.status → TRANSFERRED** автоматически, когда все
  машины transferred.
- **Performance — устранение N+1** в admin/signals,
  новые индексы (perf indexes), annotate `total_balance` в admin
  querysets.

### Fixed

- Критичные баги в админке и сервисах
  (`with_balance_info`-фильтр клиентов по долгу).
- BLC invoice numbering padding (6 digits для AV/PARDP unification).
- `bulk delete invoices`: CREDIT_NOTE skip in
  `recalculate_paid_amount` / `update_status`.
- Mobile responsiveness (topbar grid, burger menu, login logo
  overflow, padding, scrollbars).
- Invoice audit: `skip_ai_comparison` не должен синкать PDF-позиции;
  `'NoneType' object is not subscriptable`.
- BALANCE_TOPUP: запрет `from_*` + скрипт починки испорченных
  TOPUP, парный TOPUP при auto_reconcile.

---

## Соглашения

- **fix:** исправление бага.
- **feat:** новая функциональность.
- **refactor:** рефакторинг без изменения поведения.
- **docs:** документация.
- **chore:** инфраструктура, конфиги, зависимости.
- **perf:** оптимизация производительности без изменения поведения.
- **style:** UI/CSS, без логики.

Подробности процесса — `.cursor/rules/git-workflow.mdc`.
