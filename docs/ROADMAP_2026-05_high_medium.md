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

**Действия**:

- [ ] Создать `scripts/server_pg_backup.sh` — на сервере:
  - `pg_dump -Fc logist2_db -f /var/backups/logist2/$(date +%F).dump`;
  - удаление файлов старше 30 дней (`find ... -mtime +30 -delete`);
  - короткий ежедневный + еженедельный snapshot (опционально).
- [ ] Положить в `/etc/cron.d/logist2-backup`:
      `30 3 * * * postgres /var/www/.../scripts/server_pg_backup.sh`
- [ ] Healthcheck: cron пишет в лог, скрипт `manage.py` (или просто
      `find` в monitoring) алертит если последний бэкап старше 36 часов.
- [ ] Опционально: rclone-выгрузка в S3/Backblaze (для off-site).
- [ ] `docs/BACKUPS.md` — куда складываются, как восстановить,
      как проверить целостность (`pg_restore --list`).

**DoD**: на сервере крутится cron, в `/var/backups/logist2/` через сутки
лежит файл, в repo `docs/BACKUPS.md` с инструкцией восстановления.

---

### H5. Защита публичных endpoints (фото, tracking)

**Проблема**: `AllowAny` на фото контейнеров и tracking-эндпоинтах.
Throttle 20–30/min только замедляет scraping, не защищает.

**Действия**:

- [ ] Локализовать все `AllowAny` / открытые view: `rg 'AllowAny|permission_classes\s*=\s*\[\]' core/`
      + URL-конфиги.
- [ ] Для фото: signed URL (Django storages → S3 presigned, либо
      собственный HMAC-токен на час). Реализация в `core/views_website.py`
      и `core/serializers_*.py`.
- [ ] Для tracking-страниц: rate limit оставить, но добавить **CAPTCHA**
      (hCaptcha free) на форму, либо доступ только по уникальной ссылке
      из письма клиенту (что фактически signed URL).
- [ ] CSP-заголовки для страниц с фото — запретить hotlinking.
- [ ] Логировать массовые загрузки в Sentry / отдельный лог.

**DoD**: фото невозможно скачать без подписи; tracking-форма не
ботабельна; в `docs/PUBLIC_ENDPOINTS.md` зафиксирована модель угроз.

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

- [ ] `H6a` — `core/models.py` → пакет `core/models/`:
  - `__init__.py` реэкспортирует все классы (чтобы `from core.models import X` работало);
  - `containers.py`, `cars.py`, `clients.py`, `warehouses.py`, `lines.py`,
    `notifications.py`, `vin_blacklist.py`, `tracking.py` и т.д.
  - Все `app_label = 'core'` на месте, миграций не добавляется.
  - Тесты должны пройти **без изменения**.
- [ ] `H6b` — `core/admin_billing.py` → `core/admin/billing/` пакет.
- [ ] `H6c` — `core/views_website.py` → `core/views_website/` пакет.
- [ ] `H6d` — `core/signals.py` → разнести по доменам:
      `signals/lifecycle.py`, `signals/billing.py`, `signals/notifications.py`,
      `signals/banking.py`. Регистрация — в `core/apps.py` (`ready()`).

**DoD**: ни один файл из вышеперечисленных не больше ~700 строк;
`pytest` зелёный; `python manage.py check --deploy` без warnings.

**Риск**: круговые импорты. Лекарство — `apps.get_model()` вместо
прямых импортов в admin/signals.

---

### H7. (резерв под обнаруженные при H1–H6 проблемы)

---

## MEDIUM (7 задач)

### M1. README + CHANGELOG.md

- [ ] README — обновить версии (Django 5.2.14, Python 3.10+),
      пути к `settings/`, корректное расположение `scripts/deploy.ps1`,
      раздел «Setup for development» (с H1+H2).
- [ ] Создать `CHANGELOG.md` в формате [Keep a Changelog](https://keepachangelog.com/).
      Backfill с момента критических изменений (май 2026 — Critical 1+2+3).
- [ ] В `.cursor/rules/git-workflow.mdc` добавить шаг «обновить CHANGELOG»
      в раздел «Заканчиваем работу» (опционально).

**DoD**: новый разработчик по README запускает проект; в CHANGELOG.md
видно, что изменилось между релизами.

---

### M2. CORS: либо подключить `django-cors-headers`, либо убрать переменные

**Проблема**: `env.example` декларирует `CORS_ALLOWED_ORIGINS`, но
`django-cors-headers` не установлен и не подключен → переменные не
работают, дают ложное чувство защиты.

**Действия (выбрать одно)**:

- **A. Подключить**:
  - [ ] `pip install django-cors-headers` → `requirements.txt`;
  - [ ] `INSTALLED_APPS += ['corsheaders']`;
  - [ ] `MIDDLEWARE` — `corsheaders.middleware.CorsMiddleware` **выше** `CommonMiddleware`;
  - [ ] `CORS_ALLOWED_ORIGINS = os.getenv(...).split(',')`;
  - [ ] доку: для каких доменов открыто и почему.
- **B. Снести**:
  - [ ] удалить `CORS_*` из `env.example`;
  - [ ] в README — «CORS не используется, frontend на том же origin».

**DoD**: либо CORS реально работает (curl с другого origin показывает
заголовки), либо переменных нет.

---

### M3. ruff правила + django-upgrade target

**Текущее**:

- `pyproject.toml` ruff `select` минимальный.
- `pre-commit` гоняет `django-upgrade --target-version 5.1` при фактической
  5.2.

**Действия**:

- [ ] Расширить ruff select: `["E", "F", "W", "I", "UP", "B", "C4", "DJ", "PL", "RUF"]`
      (минимум `B` для bugbear, `DJ` для django-специфики).
- [ ] `pre-commit` → `--target-version 5.2`.
- [ ] Прогнать `ruff check . --fix` единожды (отдельным коммитом
      `chore: ruff autofix`), затем формат.
- [ ] CI добавить `ruff check --no-fix` (если ещё не).
- [ ] В `pyproject.toml` зафиксировать `target-version = "py310"`
      (или фактический минимум).

**DoD**: `ruff check .` зелёный; `pre-commit run --all-files` без
правок; django-upgrade не предупреждает.

---

### M4. Periodic CI job с `--migrations`

**Проблема**: тесты гоняются с `DisableMigrations` (быстрее), но это
маскирует расхождения миграций / индексов.

**Действия**:

- [ ] В `.github/workflows/ci.yml` добавить job `tests-with-migrations`,
      schedule `cron: '0 4 * * *'` + on PR-label `run-migrations-ci`.
- [ ] Этот job ставит `pytest` без `--no-migrations` (либо снимает
      `DisableMigrations` через env).
- [ ] При накоплении 200+ миграций — **squashmigrations** до бэкап-точки,
      проверить что мердж миграций совпадает по shape с реальной БД
      (`python manage.py sqlmigrate core 0001` vs `pg_dump --schema-only`).

**DoD**: ночной CI job зелёный; PR с миграцией обязан гонять этот job.

---

### M5. Admin autocomplete_fields / raw_id_fields

**Проблема**: `NewInvoiceAdmin` (и парные) подгружают весь список
`issuer/recipient/cars` в `<select>` — на росте данных страница админки
тормозит / падает.

**Действия**:

- [ ] Аудит: `rg 'ModelAdmin' core/admin*.py` → для каждого FK к
      моделям с ростом (Client, Car, Container) добавить
      `autocomplete_fields = ('client', ...)` или `raw_id_fields`
      для M2M.
- [ ] У target-модели обязательно `search_fields` (иначе autocomplete
      не работает).
- [ ] Проверить `list_filter` — большие FK через `list_filter`
      генерят SQL на каждый рендер; заменить на
      `SimpleListFilter` с лимитом.
- [ ] Замерить до/после: `django-debug-toolbar` или просто
      `time curl /admin/core/newinvoice/`.

**DoD**: рендер `NewInvoiceAdmin.add_view` < 500мс при 10k клиентов
(локально симулируется фикстурой).

---

### M6. Синхронизировать systemd unit path

**Проблема**: в репо `scripts/*.service` указывают `/var/www/logist2`,
реальный путь на сервере `/var/www/www-root/data/www/logist2`.
В случае передислокации или восстановления — путаница.

**Действия**:

- [ ] Привести `scripts/gunicorn.service`, `daphne.service`,
      `celery.service`, `celerybeat.service` к реальному пути.
- [ ] Либо параметризовать через ENV (`PROJECT_DIR=...`) и шаблон
      рендерить через `envsubst` при установке.
- [ ] Скрипт `scripts/install_systemd.sh` — копирует unit-файлы
      в `/etc/systemd/system/`, делает `daemon-reload`, `enable`.
- [ ] Документировать в `docs/DEPLOY.md` (если такого нет — создать).

**DoD**: `diff /etc/systemd/system/gunicorn.service repo/scripts/gunicorn.service`
пустой; на новой машине bootstrap-скрипт сам всё разворачивает.

---

### M7. Structured logging + file handler с ротацией

**Проблема**: логи только в console → systemd journal. Sentry ловит
exceptions, но не info-логи. Для аудита (`SIGNAL` events, billing
operations) нужен поиск по тексту.

**Действия**:

- [ ] В `logist2/settings/prod.py` (а лучше в `base.py` под флагом):
      `RotatingFileHandler` или `TimedRotatingFileHandler` для
      `/var/log/logist2/app.log` (с ротацией 50MB × 10 файлов).
- [ ] Перейти на JSON-формат (`python-json-logger`) — для будущей
      интеграции с Loki/Grafana/CloudWatch.
- [ ] Контекстные поля: `request_id` (middleware), `user_id`,
      `domain` (billing/signal/sync).
- [ ] Sentry: оставить только ERROR+ (info шум).
- [ ] `docs/LOGGING.md` — где какие логи смотреть.

**DoD**: `tail -f /var/log/logist2/app.log | jq` показывает структурные
события; `grep "billing.payment"` находит все платежи за период.

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
