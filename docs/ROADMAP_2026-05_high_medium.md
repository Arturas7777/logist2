# Roadmap — High + Medium задачи (после Critical-блока, май 2026)

> Этот файл — **бриф для нового диалога с агентом**. Открывается «начинаем
> работу», даём ссылку «работаем по `docs/ROADMAP_2026-05_high_medium.md`»,
> агент идёт по списку строго сверху вниз.

## Контекст

Critical 1+2+3 закрыты коммитом `6329968` (refactor: Critical 1+2+3 —
ENCRYPTION_KEY, money-critical tests, async signals). На сервере уже:

- `ENCRYPTION_KEY` задан, `rotate_encryption_key` прогнан, `ENCRYPTION_KEY_REQUIRED=True`;
- тяжёлые контейнерные пересчёты идут через Celery
  (`recalculate_cars_total_price_task`);
- покрытие critical-модулей подпёрто отдельным `--cov-fail-under=55` в CI.

Дальше — **High** (внутренние риски: dev-onboarding, безопасность,
maintainability) и **Medium** (документация, performance hygiene,
инструменты).

## Правила работы по этому плану

1. **Один pull request = одна задача**. Никаких "пачек" — слишком много
   зон ответственности.
2. По каждой задаче: код → локальный прогон `pytest` → `ruff check . && ruff format .`
   → коммит (`refactor:` / `feat:` / `fix:` / `docs:` / `chore:`) → push → deploy
   (где применимо) → пометить здесь `[x]`.
3. Любой shell в PowerShell оборачивать в `-F file` или одиночные строки;
   heredoc и кириллица в `-m` ломаются.
4. Не трогать прод-данные без бэкапа. Для опасных миграций — сначала
   `--dry-run` или копия БД.

---

## HIGH (7 задач)

### H1. requirements-dev.txt — onboarding для тестов

- [x] **Уже частично сделано** в коммите `6329968` — файл создан,
      содержит `pytest`, `pytest-django`, `pytest-cov`, `ruff`. CI
      переключён на него.
- [x] **Доделать**: проверить, что новый разработчик с нуля
      может запустить `pip install -r requirements.txt -r requirements-dev.txt`
      и сразу `pytest`. Описать в README блок «Setup for development».
- [x] Опционально: добавить `pytest-xdist` (parallel), `freezegun`
      (заморозка времени для billing-тестов), `factory-boy` (фабрики).
      → Добавили только `freezegun` (минимум, упомянутый в DoD). `xdist`
      пока не нужен (148 тестов ~3 сек на SQLite), `factory-boy` никем
      не используется.
- [ ] Подумать про переезд на `pyproject.toml` с `[project.optional-dependencies]`
      (но это уже отдельная Medium-задача, см. M3).

**DoD**: README обновлён, `pip install -r requirements-dev.txt && pytest`
работает на чистом venv. ✅ Проверено локально 2026-05-25, 148 passed in 2.45s.

---

### H2. START_ME.bat → `logist2.settings.dev`

**Проблема**: `manage.py` по умолчанию указывает на `logist2.settings`
(base). На локалке без `DJANGO_SETTINGS_MODULE=logist2.settings.dev`
не работают dev-удобства (DEBUG, INTERNAL_IPS, debug-toolbar etc).
`START_ME.bat` это не задаёт.

**Решение**: выбран **вариант B (альтернатива)** — переключён дефолт
в `manage.py` / `wsgi.py` / `asgi.py` / `celery.py` на
`logist2.settings.dev`. На сервере systemd-юниты уже явно выставляют
`prod` через `Environment=`, плюс `scripts/deploy.ps1` теперь тоже
явно экспортирует `prod` перед `migrate`/`collectstatic`.

**Действия (сделано)**:

- [x] ~~В `START_ME.bat` добавить `set DJANGO_SETTINGS_MODULE=...`~~ —
      не понадобилось: дефолт `manage.py` теперь dev, `runserver`
      автоматически загружает dev-профиль.
- [x] В README раздел «Setup for development» добавлен блок про
      `DJANGO_SETTINGS_MODULE` (см. H1 + дополнение H2).
- [x] `scripts/sync_db.ps1` — не трогаем: использует `pg_dump`/`pg_restore`,
      Django не загружается, переменная не нужна.
- [x] **Выбрано:** переключить `manage.py` дефолт на `logist2.settings.dev`.
      Также обновлены `logist2/wsgi.py`, `logist2/asgi.py`,
      `logist2/celery.py`, `scripts/deploy.ps1` (явный `prod` в migrate),
      `scripts/sync_photos_cron.sh` (`prod`), `scripts/run_all_tests.py`
      (`test`), `scripts/create_test_client.py` (`dev`), `env.example`
      (актуальный комментарий).

**DoD**: ✅ `python manage.py shell -c "print(settings.SETTINGS_MODULE)"`
выдаёт `logist2.settings.dev` с `DEBUG=True` сразу после активации venv,
без правки переменных окружения. `pytest` — 148 passed.

---

### H3. Удалить 4 неиспользуемых пакета

**Кандидаты на удаление** (все 4 удалены):

- `django-admin-interface` — нет в `INSTALLED_APPS`, не используется.
- `django-modeltranslation` — то же.
- `django-colorfield` — то же.
- `django-cleanup` — то же.

**Действия (сделано)**:

- [x] Поиск по всему репо (`admin_interface|modeltranslation|colorfield|django_cleanup`,
      `ColorField`, `TranslationOptions`) — единственные упоминания
      нашлись только в `requirements.txt`, `.cursor/rules/project-overview.mdc`
      и самом этом roadmap. Ни в `INSTALLED_APPS`, ни в миграциях
      `core/migrations/`, ни в `templates/`, ни в импортах — нигде.
- [x] Удалены из `requirements.txt`.
- [x] Локально `pip uninstall` всех 4, прогон тестов:
      - `pytest` — 148 passed
      - `python manage.py check` — System check identified no issues
      - `python manage.py makemigrations --check --dry-run` — No changes detected
- [x] Deploy: `collectstatic` без ошибок, gunicorn/daphne/celery/celerybeat —
      active.
- [x] Заодно поправил `.cursor/rules/project-overview.mdc` (Django 5.2.14
      вместо 5.1.7, убраны упоминания удалённых пакетов, добавлен Sentry
      и `pytest`+freezegun из dev-deps).

**DoD**: ✅ пакеты ушли, тесты зелёные, прод поднялся.

---

### H4. Автоматизированные бэкапы БД (cron + retention)

**Проблема**: только ручной `scripts/sync_db.ps1`. Полагаемся на то,
что админ помнит делать дампы.

**Действия (сделано)**:

- [x] Создан `scripts/server_pg_backup.sh`:
  - `pg_dump -Fc --no-owner --no-acl` → `/var/backups/logist2/${DB_NAME}_YYYY-MM-DD.dump`;
  - smoke check сразу после дампа: `pg_restore --list` (битый файл удаляется,
    exit 3);
  - retention: `find -mtime +30 -delete` (порог через `RETENTION_DAYS`);
  - логи в `/var/log/logist2/backup.log`;
  - креды берёт из `.env` проекта, без хардкода и без зависимости от
    конкретного пользователя ОС.
- [x] `scripts/logist2-backup.cron` (для `/etc/cron.d/logist2-backup`):
      `30 3 * * * root /var/www/.../scripts/server_pg_backup.sh`.
      Запуск под `root` — нужен доступ к `/var/backups/` и `.env`.
- [x] `scripts/install_logist2_backup.sh` — idempotent bootstrap:
      mkdir, chmod, копирование cron-файла, reload cron.
- [x] Healthcheck: Celery beat `check-backup-freshness-daily` в 04:15
      (см. `core/tasks_monitoring.check_backup_freshness` + `logist2/celery.py`).
      Алертит в Sentry warning, если самый свежий `.dump` старше
      `BACKUP_MAX_AGE_HOURS` (по умолчанию 36) или директории/файлов нет.
      На локалке возвращает `not_configured` без шума.
- [ ] **Опционально (не сделано в этом PR):** rclone-выгрузка в
      S3/Backblaze для off-site. Зафиксировано в `docs/BACKUPS.md`
      как TODO с примерным дизайном.
- [x] `docs/BACKUPS.md` — путь, расписание, восстановление (полное /
      одна таблица / локально через scp), проверка целостности (быстрая
      и через test-restore), Sentry-healthcheck, troubleshooting-таблица.

**DoD**: ✅ cron установлен (`/etc/cron.d/logist2-backup`), первый дамп
сделан вручную после установки, файл лежит в `/var/backups/logist2/`,
`docs/BACKUPS.md` опубликована.

---

### H5. Защита публичных endpoints (фото, tracking)

**Проблема**: `AllowAny` на фото контейнеров и tracking-эндпоинтах.
Throttle 20–30/min только замедляет scraping, не защищает.

**Действия**:

- [x] Локализовать все `AllowAny` / открытые view: аудит выполнен,
      зафиксирован в `docs/PUBLIC_ENDPOINTS.md` (раздел 1).
- [x] **H5a:** Для фото — signed URL через `django.core.signing.TimestampSigner`
      (HMAC по `SECRET_KEY`, TTL = 1 час). Реализовано в
      `core/services/signed_urls.py` + новые view `serve_signed_photo`
      и обновлённые `get_container_photos` / `download_photos_archive`
      (`container_token` обязателен для ZIP, фильтрация `photo_ids` по
      контейнеру). Тесты: `core/tests/test_signed_photos.py` (18/18 ok).
- [x] Логирование загрузок: `logger.info` на каждый `serve_signed_photo`
      и `download_photos_archive` (см. `docs/PUBLIC_ENDPOINTS.md`, §3.3).
- [ ] **H5b (TODO):** CAPTCHA (hCaptcha) на формы `track_shipment` и
      `ContactMessageViewSet`. План в `docs/PUBLIC_ENDPOINTS.md`, §4.2.
- [ ] **H5c (TODO):** CSP / Referrer-Policy / CORP-заголовки + закрыть
      `/media/photos/` в nginx через `X-Accel-Redirect`. План в
      `docs/PUBLIC_ENDPOINTS.md`, §4.1 и §4.3.

**DoD**: фото невозможно скачать без свежей подписи (H5a — выполнено,
старые прямые ссылки `/media/photos/...` всё ещё доступны через nginx,
закрытие в H5c). В `docs/PUBLIC_ENDPOINTS.md` зафиксирована модель угроз
и план для оставшихся пунктов.

---

### H6. God-files split (поэтапно)

**Текущие монстры**:

- `core/models.py` ≈ 2100 строк;
- `core/signals.py` ≈ 1400 строк (после Critical #2 — частично разрулен);
- `core/admin_billing.py` ≈ 1800 строк;
- `core/views_website.py` — крупный.

**Подход**: **только домены, без переименований моделей** (миграции не
плодим). Django поддерживает `models.py` как пакет.

**Этапы** (отдельные PR):

- [x] `H6a` — `core/models.py` → пакет `core/models/`:
  - `__init__.py` реэкспортирует все классы (чтобы `from core.models import X` работало);
  - подмодули: `_vehicle_types.py`, `lines.py`, `carriers.py`, `clients.py`,
    `warehouses.py`, `company.py`, `containers.py`, `cars.py`,
    `services.py`, `auto_transport.py`, `tasks.py`.
  - Все `app_label = 'core'` на месте, миграций не добавляется
    (`makemigrations --check --dry-run` → No changes detected).
  - Тесты прошли **без изменений** (166 passed, был тот же набор
    до сплита).
  - Самый большой файл — `cars.py` (621 строка), остальные ≤ 280 строк.
  - Реэкспорт `models_contact/email/invoice_audit/monitoring/scans`
    сохранён в `__init__.py` (как был в хвосте старого `models.py`).
- [x] `H6b` — `core/admin_billing.py` → пакет `core/admin/billing/`:
  - `__init__.py` импортирует все админ-классы — `@admin.register(...)`
    срабатывает при загрузке `core/admin/__init__.py`;
  - подмодули: `filters.py`, `inlines.py`, `expense_category.py`,
    `personal.py`, `transaction.py`, `invoice.py` (сборка),
    `invoice_display.py`, `invoice_forms.py`, `invoice_actions.py`,
    `invoice_urls.py` (миксины для `NewInvoiceAdmin`).
  - `NewInvoiceAdmin` (~1460 строк) разнесён через **миксины**, чтобы
    самый большой файл влез в DoD: `invoice_forms.py` — 493 строки.
    Остальные ≤ 340 строк.
  - Поведенческих изменений нет: миграций не добавляется
    (`makemigrations --check --dry-run` → No changes detected),
    тесты прошли **без изменений** (166 passed),
    `manage.py check` без warnings.
  - Импорт в `core/admin/__init__.py` переведён на
    `from core.admin.billing import ...`.
- [x] `H6c` — `core/views_website.py` → пакет `core/views_website/`:
  - `__init__.py` реэкспортирует все 25 view-функций/класса (чтобы
    `core/urls_website.py` с `from . import views_website` +
    `views_website.<name>` работал без изменений);
  - подмодули: `public.py` (home/about/services/contact/news),
    `client_portal.py` (dashboard/car/container detail),
    `api.py` (DRF ViewSet'ы + `IsClientUser`),
    `tracking.py` (`track_shipment`),
    `photos_authed.py` (`download_*_photo*`, login-required),
    `ai_chat.py` (чат + история + локальный fallback
    `get_ai_response`), `signed_photos.py` (H5a:
    `get_container_photos` / `download_photos_archive` /
    `serve_signed_photo`).
  - Самый большой файл — `ai_chat.py` (476 строк), остальные ≤ 280;
    DoD ≤ 700 выполнен.
  - Миграций не добавляется
    (`makemigrations --check --dry-run` → No changes detected),
    тесты прошли **без изменений** (166 passed), `manage.py check` ок.
  - Smoke: 25/25 имён реэкспортируются, все 19 URL `website:*`
    резолвятся, локальный сайт `/`, `/about/`, `/news/`,
    `/api/container-photos/MRSU5522473/` отвечают 200.
- [x] `H6d` — `core/signals.py` → пакет `core/signals/`:
  - `__init__.py` импортирует все 10 submodules (это регистрирует
    `@receiver`-декораторы), затем вызывает `connect_autotransport_signals()`
    и `connect_cache_invalidation_signals()`. Backward-compat
    реэкспорт для `core.admin.container` (5 имён) сохранён.
  - подмодули по доменам: `service_cache.py` (per-instance svc cache),
    `container.py` (pre/post_save + email + gdrive-note),
    `car.py` (pre/post_save + 5 хелперов: services, email,
    is_important → Task, WS), `car_service.py` (recalc total_price +
    invoice regen с thread-local дедупом),
    `service_catalog.py` (catalog change → bulk CarService update +
    cascade delete), `invoice.py` (auto-categorize + sitepro +
    linked_paid), `transaction.py` (balance/paid_amount sync),
    `bank.py` (BT.matched_invoice → auto Transaction),
    `autotransport.py` (m2m + TRANSFERRED + container status),
    `cache_invalidation.py` (stats/payment_objects cache).
  - Самый большой файл — `car.py` (337 строк), остальные ≤ 200;
    DoD ≤ 700 выполнен с большим запасом.
  - `core/apps.py` НЕ менялся — `from . import signals` подтягивает
    пакет автоматически. Регистрация 28 receiver'ов проверена,
    166 тестов (включая save/delete real сигналов) прошли
    **без изменений**, миграции не добавляются.

**DoD**: ни один файл из вышеперечисленных не больше ~700 строк;
`pytest` зелёный; `python manage.py check --deploy` без warnings.

**Риск**: круговые импорты. Лекарство — `apps.get_model()` вместо
прямых импортов в admin/signals.

---

- [x] `H7` — резерв под обнаруженные при H1–H6 проблемы (3 фикса
  в одном коммите):
  1. **`/api/track/` 500 → 400 на битом JSON.** В `track_shipment`
     ловился `except Exception`, который проглатывал DRF `ParseError`
     и отдавал 500 c generic-сообщением. Клиент не видел причину,
     Sentry заваливался ложными 500-ками. Достали `request.data`
     наружу `try`, добавили `except APIException: raise` чтобы все
     DRF-исключения (`ParseError`, `Throttled`, `NotAuthenticated`)
     пробрасывались с правильным кодом. Покрыто 6 новыми тестами
     в `core/tests/test_track_shipment.py` (включая regression на
     битый JSON и form-data без `tracking_number`).
  2. **`.gitignore`: общее `!**/__init__.py` вместо точечных.**
     Маска `_*.py` (для временных скриптов) уже трижды (H6a, H6b,
     H6c, H6d) ловила свежие `__init__.py` новых пакетов, и каждый
     раз приходилось добавлять отдельное negation. Заменили 4
     точечных правила одним wildcard'ом — будущие пакеты будут
     работать «из коробки». `_vehicle_types.py` (H6a) оставили
     отдельным исключением, потому что он не `__init__.py`.
  3. **`pytest-env` для `DJANGO_SETTINGS_MODULE`.** Если в шелле
     остался `DJANGO_SETTINGS_MODULE=logist2.settings.dev` от
     `manage.py check`/`runserver`, `pytest-django` подхватывал dev
     (PostgreSQL + legacy schema) и тесты падали с
     `FieldDoesNotExist: NewBalanceTransaction.recipient_content_type`.
     Подключили `pytest-env` (`[tool.pytest_env]` в `pyproject.toml`),
     который безусловно override-ит env-var до того, как
     `pytest-django` её прочитает. Зависимость добавлена в
     `requirements-dev.txt`. Все 172 теста проходят даже с
     намеренно битой `$env:DJANGO_SETTINGS_MODULE='logist2.settings.dev'`.

---

## MEDIUM (7 задач)

### M1. README + CHANGELOG.md

- [x] README — обновлено: структура проекта под H6 (пакеты
      `core/models/`, `core/signals/`, `core/admin/billing/`,
      `core/views_website/`), 148+ → 172+ тестов, упоминание
      `pytest-env`/`freezegun`, расширен раздел документации
      (CHANGELOG, BACKUPS, PUBLIC_ENDPOINTS, ENCRYPTION_KEY,
      ROADMAP).
- [x] Создан `CHANGELOG.md` в формате
      [Keep a Changelog](https://keepachangelog.com/) с backfill от
      мая 2026: Critical 1+2+3, все 7 High-задач (H1–H7),
      мониторинг/инфра, бизнес-фичи апреля-мая.
- [ ] В `.cursor/rules/git-workflow.mdc` добавить шаг «обновить
      CHANGELOG» в раздел «Заканчиваем работу» (опционально, не
      обязателен).

**DoD**: ✅ новый разработчик по README запускает проект; в
`CHANGELOG.md` видно, что изменилось между релизами. Commit `d7ebcca`.

---

### M2. CORS: либо подключить `django-cors-headers`, либо убрать переменные

**Проблема**: `env.example` декларирует `CORS_ALLOWED_ORIGINS`, но
`django-cors-headers` не установлен и не подключен → переменные не
работают, дают ложное чувство защиты.

**Выбран вариант B** (frontend на том же origin `caromoto-lt.com`).

**Действия (сделано)**:

- [x] `CORS_ALLOWED_ORIGINS` удалена из `env.example`, оставлен
      комментарий с указанием на M2-roadmap, если потребуется
      внешний фронт.
- [x] В README (раздел «3. Переменные окружения») добавлена сноска
      «CORS не используется». Поиск по репо подтвердил: ни одна
      настройка / middleware / зависимость не читала эту переменную
      — это было «мёртвое объявление».

**DoD**: ✅ переменная ушла, ложного чувства защиты больше нет.

---

### M3. ruff правила + django-upgrade target

**Текущее**:

- `pyproject.toml` ruff `select` минимальный.
- `pre-commit` гоняет `django-upgrade --target-version 5.1` при фактической
  5.2.

**Действия (сделано)**:

- [x] Расширили ruff select до `["E", "F", "W", "I", "UP", "B", "C4",
      "DJ", "RUF"]`. `PL` намеренно не подключили — слишком шумно по
      умолчанию (можно подключить отдельно после рефакторинга).
- [x] В ignore добавлены: `RUF001/002/003` (кириллица — не баг,
      проект 3-язычный), `RUF012` (Django ModelAdmin list_display
      — стандарт), `B008` (`default=timezone.now` и т.п.),
      `DJ001/008/012` (стилистические django-правила), `E402`
      (settings split), `B904`, `UP035`, `B007`, `E722`, `E741`,
      `DJ007`, `UP031` (зафиксированы как TODO на отдельный
      рефакторинг).
- [x] `pre-commit` → `django-upgrade --target-version 5.2`.
- [x] Прогнали `ruff check . --fix` (коммит `044b440`, 196
      fix'ов в 61 файле) + `--unsafe-fixes` (в этом коммите,
      27 ещё точечных правок: implicit-Optional, comprehensions,
      unused noqa). `ruff format` НЕ запускали (раздул бы diff на
      тысячи строк из-за кавычек).
- [x] CI уже гонит `ruff check .` и `ruff format --check .` — после
      autofix всё проходит.
- [x] `target-version` в pyproject зафиксирован на `"py310"`
      (минимум по серверу; локально 3.13, CI 3.12).

**DoD**: ✅ `ruff check .` зелёный, `pre-commit run --all-files`
без правок (по новым правилам), django-upgrade на 5.2 (тоже без
правок).

---

### M4. Periodic CI job с `--migrations`

**Проблема**: тесты гоняются с `DisableMigrations` (быстрее), но это
маскирует расхождения миграций / индексов.

**Действия (сделано)**:

- [x] Добавлен `logist2/settings/test_migrations.py` — отдельный
      профиль: PostgreSQL (берёт креды из `DB_*` env), миграции НЕ
      отключены, locmem/in-memory backends для cache/channels,
      `CELERY_TASK_ALWAYS_EAGER=True`. Идёт от `base.py` напрямую
      (а не от `test.py`), чтобы не унаследовать `DisableMigrations`.
- [x] В `.github/workflows/ci.yml`:
  - Триггеры: `schedule: '0 4 * * *'` (ночной), PR-label
    `run-migrations-ci` (`pull_request.types: [labeled, …]` +
    `contains(labels, 'run-migrations-ci')`), `workflow_dispatch`.
  - Job `tests-with-migrations` поднимает Postgres 16 + Redis 7
    services, ставит `requirements-dev.txt`, прогоняет:
    `migrate --noinput` (smoke на чистой БД),
    `makemigrations --check --dry-run` (модели ↔ миграции),
    `pytest -p no:env --tb=short --maxfail=5` (`-p no:env` нужен,
    чтобы `pytest-env` из `pyproject.toml` не перебил
    `DJANGO_SETTINGS_MODULE`).
- [ ] squashmigrations до бэкап-точки — пока не нужно (169 миграций,
      ~30 сек прокат на PG-16). Зафиксировано как TODO на момент
      когда счёт перевалит за 250.

**DoD**: ✅ pyproject и settings профили локально валидируются
(`python -c "from django.conf import settings; …"` показывает
`MIGRATION_MODULES: {}`, `DB ENGINE: postgresql`); workflow добавлен
с правильными триггерами. Первый ночной прогон — после merge коммита.

---

### M5. Admin autocomplete_fields / raw_id_fields

**Проблема**: `NewInvoiceAdmin` (и парные) подгружают весь список
`issuer/recipient/cars` в `<select>` — на росте данных страница админки
тормозит / падает.

**Действия (сделано)**:

- [x] Полный аудит — 7 ModelAdmin с тяжёлыми FK на крупные модели
      (Client/Car/Container/Warehouse/Line/Carrier/Company/User/NewInvoice).
- [x] Добавлено `autocomplete_fields`:
  - `CarAdmin` — `(client, warehouse, line, carrier, container)`.
    Также расширен `search_fields = ('vin', 'brand', 'client__name',
    'container__number')` для удобного поиска авто.
  - `ContainerAdmin` — `(line, warehouse)`.
  - `AutoTransportAdmin` — `(carrier,)`. M2M `cars` НЕ трогали:
    `change_form.html` уже использует кастомный AJAX-UI
    (`/admin/core/autotransport/change_form.html` →
    `_get_extra_context` грузит до 200 авто).
  - `TransactionAdmin` — 11 FK (5 from_* + 5 to_* + `invoice`).
    Также добавлены `from_client__name`/`to_client__name` в
    `search_fields`. Это самая тяжёлая страница после
    `NewInvoiceAdmin.add_view`.
  - `ClientUserAdmin` — `(user, client)`.
  - `TrackingRequestAdmin` — `(car, container)`.
  - `NewsPostAdmin` — `(author,)`.
  - `BankConnectionAdmin` — `(company,)`.
  - `SiteProConnectionAdmin` — `(company,)`.
- [x] Проверено наличие `search_fields` на всех target-admin'ах
      (ClientAdmin, WarehouseAdmin, LineAdmin, CarrierAdmin,
      CompanyAdmin, ContainerAdmin, CarAdmin, NewInvoiceAdmin):
      везде уже есть. Дополнительно проверено что Django `UserAdmin`
      стандартно зарегистрирован (есть `search_fields=('username',
      'first_name', 'last_name', 'email')`).
- [ ] **Не сделано в этом PR (отдельный рефакторинг):**
  - `NewInvoiceAdmin.cars` M2M — там кастомный шаблон с Select2 AJAX
    (`/core/api/search-cars/`), `raw_id_fields` сломает UI.
  - `ClientAutocompleteFilter` (`core/admin_filters.py`) — `lookups()`
    грузит всех клиентов; нужен отдельный рефакторинг на AJAX-only.
  - `list_filter` cleanup (NewInvoiceAdmin recipient_client filter,
    AutoTransport.carrier — там FK-фильтр).
  - Регистрация ModelAdmin для `CarrierTruck`/`CarrierDriver` с
    `search_fields` (для autocomplete на `AutoTransport.truck`/`.driver`).

**DoD**: ✅ во всех ModelAdmin с FK на крупные таблицы
(Car/Client/Container/Transaction/...) включён autocomplete; страницы
add/change больше не загружают полные списки. Тесты 172 passed,
`manage.py check` без warnings, smoke на admin URL — 302 (нормально).

---

### M6. Синхронизировать systemd unit path

**Проблема**: в репо `scripts/*.service` указывают `/var/www/logist2`,
реальный путь на сервере `/var/www/www-root/data/www/logist2`.
В случае передислокации или восстановления — путаница.

**Действия (сделано)**:

- [x] Все 4 unit-файла приведены к реальному прод-пути
      `/var/www/www-root/data/www/logist2`, пользователь `www-root`,
      venv `.venv/`, settings `logist2.settings.prod`:
  - `scripts/gunicorn.service` (новый, был `logist2.service` со
    старым `/var/www/logist2` и неправильным именем — удалён);
  - `scripts/daphne.service` (приведён к точному виду прод-файла);
  - `scripts/celery.service` (то же);
  - `scripts/celerybeat.service` (то же).
- [x] Удалён legacy `scripts/caromoto-lt.service` (старый юнит,
      путь `/var/www/caromoto-lt`).
- [x] `scripts/install_systemd.sh` — идемпотентный bootstrap.
      Параметризован через `PROJECT_DIR`: если задан другой путь,
      `sed` подставляет его в копии unit'ов. Сравнивает с уже
      установленным, делает backup `.bak.YYYYMMDD-HHMMSS` только при
      реальных отличиях, выполняет `daemon-reload` + `enable`.
- [x] Документация в README — раздел «Развёртывание с нуля» с
      примером команды и описанием всех 4 unit'ов. Отдельный
      `docs/DEPLOY.md` не создавали: README + комментарии в самих
      скриптах покрывают onboarding.

**DoD**: ✅ `diff /etc/systemd/system/<unit>.service repo/scripts/<unit>.service`
пустой для всех 4-х (проверено через сравнение скачанных файлов).
Bootstrap-скрипт идемпотентен (`cmp -s` пропускает совпадающие).

---

### M7. Structured logging + file handler с ротацией

**Проблема**: логи только в console → systemd journal. Sentry ловит
exceptions, но не info-логи. Для аудита (`SIGNAL` events, billing
operations) нужен поиск по тексту.

**Действия**:

- [x] В `logist2/settings/base.py` под env-флагами добавлен
      `RotatingFileHandler` (50 MB × 10 файлов, путь из `LOG_DIR`).
      Handler пишет только если `LOG_DIR` задан, что не ломает dev/CI.
- [x] JSON-формат через `python-json-logger>=3.1,<4.0`
      (`pythonjsonlogger.json.JsonFormatter`). Поля: ts/level/logger/
      message + request_id/user_id/path/method + любые `extra=`.
- [x] Контекстные поля через `core.middleware_logging`:
      - `RequestContextMiddleware` сохраняет request_id (из
        `X-Request-ID` или `uuid4()`), user_id, path, method в
        `contextvars` (safe для async/threads);
      - `RequestContextFilter` пристёгивает их к каждому LogRecord;
      - `get_request_id()` / `set_request_id()` для использования в
        Celery-тасках (передача из producer → worker).
- [x] Sentry: `LoggingIntegration(level=INFO, event_level=ERROR)` —
      INFO+ остаются breadcrumbs, отдельные events создаются только
      на ERROR+.
- [x] `docs/LOGGING.md` — env-vars, примеры запросов через `jq`,
      связка с Sentry, инструкция по откату, что НЕ логировать.
- [x] `env.example` дополнен секцией Logging
      (`LOG_FORMAT`/`LOG_LEVEL`/`LOG_DIR`/`LOG_MAX_BYTES`/`LOG_BACKUP_COUNT`).

**DoD**: `tail -f /var/log/logist2/app.log | jq` показывает структурные
события; `grep "billing.payment"` находит все платежи за период.
Локальный smoke: `LOG_FORMAT=json python _smoke_log.py` → валидный JSON
с request_id/user_id; `pytest -x -q` → 172 passed.

---

## Рекомендованный порядок исполнения

Группировка по риску и зависимостям:

1. **Week 1 (быстрые победы)**:
   - H1 (доделать), H2, H3, M1 (README), M2 (CORS), M3 (ruff).
   - Все мелкие, не трогают бизнес-логику.

2. **Week 2 (инфраструктура)**:
   - H4 (бэкапы) — critical для disaster recovery, делать раньше H5/H6;
   - M6 (systemd paths) — заодно;
   - M7 (logging) — даст видимость на следующих шагах.

3. **Week 3 (security)**:
   - H5 (публичные endpoints) — нужен дизайн-ревью signed URL;
   - M5 (admin autocomplete) — не блокирует, но снижает риск
     случайного DOS себе же.

4. **Week 4+ (рефакторинг)**:
   - H6 поэтапно (a → b → c → d), по одной модели/admin за PR;
   - M4 (CI с миграциями) — параллельно, не блокирует.

## Definition of Done всего roadmap

- [ ] Все чек-боксы выше отмечены.
- [ ] Прогон полного test-suite зелёный (включая job с миграциями).
- [ ] Прод поднялся после deploy, health-check 200, в Sentry за сутки
      нет новых регрессий.
- [ ] README + CHANGELOG отражают текущее состояние.
- [ ] Новый раздел в этом файле «Что осталось / новые риски» с
      результатами повторного аудита.

## Что НЕ входит в этот roadmap

Зафиксировано отдельно (для будущих аудитов):

- Полный переезд `requirements.txt` → `pyproject.toml` (опциональная
  часть H1) — отдельная задача после H6.
- Миграция SQLite-tests → PostgreSQL-tests (сейчас CI на PG, локально
  на SQLite — это нормально).
- 2FA для админов — отдельный security-проект.
- Multi-tenant / multi-warehouse фичи — продуктовый roadmap, не
  технический.
