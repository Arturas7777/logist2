# Аудит Logist2 — раунд 3: план улучшений (к реализации)

> Этот документ — результат архитектурного аудита от 2026-06-11.
> Статус: **к реализации полностью** (подтверждено владельцем).
> Проект в режиме тестирования — данные не священны, можно менять смело.
> По мере выполнения отмечать пункты: `[x]`.

## Контекст: что УЖЕ сделано в раундах 1–2 (не повторять)

- Унификация позиций инвойса (`NewInvoice.regenerate_items_from_cars()`); `full_clean()` в `save()` у NewInvoice/Transaction
- Кэш дашборда + инвалидация; SQL-агрегация aged receivables/payables
- Лимит 200 в `car_list_api`; Celery для тяжёлых admin actions
- FSM статусов NewInvoice, Transaction, Car, Container
- Иммутабельный леджер Transaction (LEDGER_FROZEN_FIELDS, запрет удаления COMPLETED/CANCELLED)
- DB-констрейнты на суммы; UniqueConstraint `BankTransaction.matched_transaction`
- Сигнал bank-match заменён `BillingService.create_payment_for_bank_match()`
- Чистка мёртвого кода, git filter-repo (640МБ → 30МБ), сжатие иконок
- Унификация хранения: Container без собственных rate/free_days/days/storage_cost (агрегаты по машинам)
- Балансы выверены `verify_balances --fix`

---

## Топ-10 «делать в первую очередь»

| # | ID | Что | Трудоёмкость |
|---|----|-----|--------------|
| 1 | B1 | Счётчик серий / retry в `generate_number` (NewInvoice, Transaction, AutoTransport) | Низкая |
| 2 | B2 | `CheckConstraint(currency='EUR')` на Transaction/NewInvoice | Низкая |
| 3 | B3 | Redis-лок на `sync_bank_and_reconcile` (+ `sync_sitepro_invoices`) | Низкая |
| 4 | R1 | `SESSION_ENGINE=cached_db` | Очень низкая |
| 5 | R3 | Завершить ротацию ENCRYPTION_KEY, включить `ENCRYPTION_KEY_REQUIRED` | Очень низкая |
| 6 | R2 | `mark_safe` → `format_html` в admin_banking/email/car; bandit-MEDIUM блокирующим | Средняя |
| 7 | B4+A4 | Ревизия `except Exception` в денежных путях + вынос `_create_car_services_if_needed` из сигнала | Средняя |
| 8 | A3 | `create_expense_view` → `BillingService` + тесты | Средняя |
| 9 | T1 | Контрактные тесты `revolut_service` и `sitepro_service` | Средняя |
| 10 | P4+R4 | Clamp `limit` в API, рейт-лимиты, удаление legacy `/api/` | Низкая |

Вторая очередь: A1, A2, A5, P1, P2, P3, R5, R6, R7, T2 — тоже реализовать в рамках этого плана.

---

## 1. Архитектура

### [x] A1. Завершить распил `models_*` и `admin_*`
Пакет `core/models/` сосуществует с топ-левел монолитами: `models_billing.py` (2250 строк), `models_banking.py`, `models_email.py` (551), `models_website.py` (475), `models_accounting.py`, `models_contact.py`, `models_scans.py`, `models_monitoring.py`, `models_invoice_audit.py`. В админке: пакет `core/admin/` + `admin_banking.py` (1136), `admin_website.py`, `admin_accounting.py`, `admin_scans.py`, `admin_filters.py`, `admin_export.py`.

**Решение:** перенести `models_billing.py` → `core/models/billing.py`, `admin_banking.py` → `core/admin/banking.py` и т.д. с реэкспортом из старых путей (python-only, без миграций БД — паттерн `0131_rename_fields_python_only`). На отдельные Django-приложения НЕ дробить.

### [x] A2. Разгрузить «жирную» модель NewInvoice
`get_items_pivot_table()` (~150 строк presentation-кода, строки 663–817 `models_billing.py`) → `core/admin/billing/invoice_display.py`. Зафиксировать правило: модель = инварианты/валидация/FSM; команды с побочными эффектами (платежи, смена серий `change_series`/`_register_cash_payment`/`_reverse_cash_payments`) — только через `BillingService`.

### [x] A3. Бизнес-логика из админки → сервисы
`admin_banking.py::create_expense_view` (строки 665–860) создаёт NewInvoice + InvoiceItem + платёж прямо во вьюхе. В `core/admin/partners.py` — `reset_balances`, `reset_client_balance`, `topup_balance_view` мутируют финансовое состояние напрямую. В `core/admin/car.py::_save_model_inner` — оркестрация bulk-сохранения услуг.

**Решение:** `BillingService.create_expense_from_bank_transaction(bt, ...)` + тонкая вьюха; reset/topup довести до полного делегирования сервису; покрыть сервисные методы тестами.

### [x] A4. Вынести `_create_car_services_if_needed` из сигнала
`core/signals/car.py` (строки 274–396): пересоздание ценообразующих CarService в `post_save` с глотанием исключений (`except Exception: logger.error` без reraise). Сбой = неправильные суммы в будущих инвойсах, молча.

**Решение:** явный вызов сервиса из save-путей (admin, API, lifecycle service), исключения пробрасывать (Sentry-алерт). Сигналы — только для дешёвых event-нотификаций (WS, email enqueue). См. `docs/signals_classification.md` (COMMAND/EVENT). Учесть `threading.local` дедупликацию (`_pricing_local`/`_regen_local` в `car_service.py`) — мина при async.

### [x] A5. Email-домен: вынести логику из вьюх
`core/views/emails.py` (1190 строк): `_resolve_group_addrs`, `_sanitize_html` → в сервис (`email_compose.py` или новый `core/emails/`). Не срочно, но в рамках плана сделать.

## 2. Бизнес-логика

### [x] B1. Гонка в `generate_number`
`NewInvoice.generate_number` (строки 873–879 `models_billing.py`), `Transaction.generate_number` (1920–1946), `AutoTransport` (`models/auto_transport.py:148`): `SELECT FOR UPDATE` лочит только существующую последнюю строку — при пустой серии лока нет, при параллельной вставке коллизия → `IntegrityError` без retry → 500.

**Решение:** таблица счётчиков серий `SeriesCounter(prefix, last_value)` + `UPDATE … RETURNING` (полная сериализация). Альтернатива-минимум: retry-цикл на IntegrityError (3 попытки). Предпочесть счётчик.

### [x] B2. Валютный инвариант балансов
`expected_entity_balance`/`recalculate_entity_balance` (`models_billing.py:1861–1863`) суммируют `amount` без разреза валюты — транзакция в USD тихо смешается с EUR в `Client.balance`, `verify_balances` не поймает.

**Решение:** `CheckConstraint(currency='EUR')` на Transaction и NewInvoice (перед миграцией проверить данные одним запросом); конверсия на границе при матчинге не-EUR BankTransaction.

### [x] B3. Лок от наложения запусков синхронизации банка
`sync_bank_and_reconcile` (`tasks.py:491–550`, beat каждые 30 мин, retry 300с): сценарий «retry + следующий тик» = два параллельных прогона → возможен осиротевший TOPUP (пара TOPUP+PAYMENT создаётся не атомарно относительно конкурента).

**Решение:** распределённый лок `cache.add('lock:sync_bank', ttl=900)` в начале задачи; вторая инстанция — skip с логом. То же для `sync_sitepro_invoices`.

### [x] B4. Ревизия `except Exception` в денежных путях
~70 `except Exception` в `core/services/` и сигналах. Классифицировать: «graceful degrade допустим» (email/TG/WS) vs «деньги — reraise или явное сообщение пользователю + Sentry». Пример проблемы: `views/api.py:608–609` — сбой применения клиентского тарифа после `add_services` логируется, пользователь видит успех, цены неверные.

## 3. Производительность

### [x] P1. Аннотации для `Container.storage_cost`/`days`
После унификации это properties с SQL-запросом на обращение (`core/models/containers.py:176–195`). Добавить в менеджер `with_storage_aggregates()` (annotate Sum/Max), в properties — fallback на аннотацию (паттерн `_storage_daily_rate_ann` у Car). Закрепить в `test_query_budgets.py`.

### [x] P2. Батч-резолвинг каталога в `Car.calculate_total_price`
`models/cars.py:308–336`: цикл по `car_services.all()`, `svc.invoice_price` резолвит каталог псевдо-generic FK (`service_type`+`service_id`) отдельным запросом → тысячи запросов в `recalculate_cars_total_price_task`.

**Решение:** собрать `(service_type, service_id)`, выбрать каталоги 4 запросами `in_bulk`, посчитать в памяти.

### [x] P3. pg_trgm + GIN-индексы для поиска
`search_invoices` (`views/api.py:718–735`) — OR по 8 `icontains` через 6 JOIN; `search_counterparties`, поиск VIN — то же. Миграция: расширение `pg_trgm` + GIN-индексы на `NewInvoice.number/external_number`, `Client.name`, `Car.vin/brand`.

### [x] P4. Clamp `limit` в API
`get_invoice_cars_api` (`views/api.py:292`) и `get_warehouse_cars_api` (`:378`): `limit` из GET без верхней границы. Решение: `limit = min(max(limit, 1), 500)` + `@ratelimit_staff` на оба.

## 4. Надёжность и безопасность

### [x] R1. Сессии: `cache` → `cached_db`
`SESSION_ENGINE = "django.contrib.sessions.backends.cache"` (`base.py:365`) — рестарт Redis = разлогин всех. Сменить на `cached_db` + миграция сессионной таблицы.

### [x] R2. `mark_safe` → `format_html` в админке
`BankTransaction.counterparty_name`/`description` приходят из Revolut API (контрагент сам задаёт имя!), темы писем из Gmail. Пройти display-методы `admin_banking.py`, `core/admin/email.py`, `core/admin/car.py`, заменить mark_safe/f-string на `format_html`/`format_html_join`. После зачистки — bandit-MEDIUM сделать блокирующим в CI (`ci.yml:246–250`).

### [x] R3. Завершить ротацию ENCRYPTION_KEY
`ENCRYPTION_KEY_REQUIRED` по умолчанию False (`base.py:29`) — токены могут шифроваться fallback-ом на SECRET_KEY. Прогнать ротацию на проде (`rotate_encryption_key`, инструкция `docs/ENCRYPTION_KEY.md`) и включить `ENCRYPTION_KEY_REQUIRED=true`.

### [x] R4. Убрать legacy-зеркало `/api/`
`logist2/urls.py:50–65`: `/api/` дублирует `/api/v1/`. Включить логирование обращений к `/api/` → удалить (или 410 Gone).

### [x] R5. Squash миграций до baseline
183 миграции, полная цепочка в CI на каждый PR. Squash до `0001_squashed_*`: на чистой схеме `makemigrations` заново + `replaces`. Прод не трогается (таблицы есть — пометится applied). Аккуратно с `RunPython`-данными.

> Сделано: `0001_squashed_baseline` (replaces всех 178 миграций) + сохранены RunPython-данные (сид категорий расходов из 0112/0141, pg_trgm-индексы из 0186). Прод прошёл `migrate` (0184–0186 применены), после чего старые файлы удалены — осталась одна baseline-миграция.

### [x] R6. Лок зависимостей
`requirements.txt` смешивает пины и открытые диапазоны (`pillow>=12.1.0`, `openai>=1.40.0`, `anthropic>=0.40.0`…). Перейти на pip-tools (`requirements.in` + скомпилированный лок) или `uv lock`.

### [x] R7. Post-deploy smoke + restore-учения
(а) В конец `deploy.ps1` — curl `/health/` + страница админки, ожидание 200/302; (б) задокументировать и провести восстановление дампа в локальную БД по `docs/BACKUPS.md`.

## 5. Тесты

### [x] T1. Контрактные тесты интеграций
Без тестов: `invoice_audit_service.py` (1271 строка), `sitepro_service.py` (961), `revolut_service.py` (553), `dashboard_service.py` (820), `google_drive_sync.py` (981). Контрактные тесты с замоканным HTTP (записанные JSON-ответы как фикстуры). Начать с `revolut_service.sync_all` и `sitepro_service.push_invoice`.

### [x] T2. Конкурентные тесты + ratchet покрытия
(а) Тесты на гонки (двойная оплата, гонка нумерации B1) через `threading` в PG-профиле, `@pytest.mark.integration`; (б) ratchet 32 → 40 → 50; критичные модули 55 → 75, добавить `core.models_billing` в список критичных; (в) при росте тестов — `factory_boy`.

> Сделано: `core/tests/test_concurrency.py` (гонка нумерации SeriesCounter, параллельные платежи без lost update) — на SQLite скипаются, в CI бегут в PG-джобе. Попутно исправлен `logist2.settings.test_migrations`: base.py подменял БД на SQLite под pytest, и «тесты с миграциями» молча бежали на SQLite. Ratchet: core 32 → 38 (факт 40%), критичные 55 → 60 (факт 64%) + добавлен `core.models.billing`. `factory_boy` отложен — текущие фикстуры справляются.

---

## Порядок работы

1. Начать с топ-10 в указанном порядке (1–5 — быстрые победы, 6–10 — средние).
2. Затем вторая очередь: A1, A2, P1–P3, R5–R7, T2, A5.
3. После каждого логического блока: тесты + ruff + коммит.
4. R3 (ротация ключа) и R5 (squash) — выполнять отдельными коммитами с особой осторожностью.
5. В конце: CHANGELOG, деплой по `deploy.ps1`.
