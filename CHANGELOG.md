# Changelog

Все значимые изменения в Logist2 будут документироваться в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект следует своей внутренней схеме версий (см. git-теги).

## [Unreleased]

### Added

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
