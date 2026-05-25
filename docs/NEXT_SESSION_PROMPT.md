# Промт для следующей сессии — Medium-задачи

Скопируй текст ниже в новый чат с агентом.

---

## Сообщение №1 (старт)

```
начинаем работу
```

(Агент сам сделает `git pull`, `sync_db.ps1`, `migrate`, `runserver` —
это прописано в `.cursor/rules/git-workflow.mdc`.)

---

## Сообщение №2 (задача)

```
Продолжаем работу по docs/ROADMAP_2026-05_high_medium.md.

Все HIGH-задачи закрыты:
- H1, H2, H3, H4 — закрыты ранее.
- H5a (signed URLs для фото) — закрыт, работает в проде. Документация
  в docs/PUBLIC_ENDPOINTS.md.
- H5b (CAPTCHA на /api/track/ и /api/contact/) — ОТЛОЖЕН. План в
  docs/PUBLIC_ENDPOINTS.md §4.2.
- H5c (CSP / Referrer-Policy / CORP + закрытие /media/photos/
  через X-Accel-Redirect) — ОТЛОЖЕН. План в
  docs/PUBLIC_ENDPOINTS.md §4.1 и §4.3.
- H6a/b/c/d — все god-files разбиты на пакеты:
    core/models.py        → core/models/        (H6a)
    core/admin_billing.py → core/admin/billing/ (H6b)
    core/views_website.py → core/views_website/ (H6c)
    core/signals.py       → core/signals/       (H6d)
- H7 — 3 мелких фикса (battered JSON 500→400, .gitignore wildcard
  __init__.py, pytest-env для override DJANGO_SETTINGS_MODULE).

Теперь идём в MEDIUM (7 задач). Рекомендованный порядок:

1. M1 — README + CHANGELOG.md.
   Обновить README под текущее состояние (Django 5.2.14, новая
   пакетная структура core/models, core/signals, core/admin/billing,
   core/views_website, шаги setup-for-development, pytest-env).
   Создать CHANGELOG.md в формате Keep a Changelog с backfill от
   мая 2026 (Critical 1+2+3, всё H1-H7).

2. M2 — CORS: подключить django-cors-headers или удалить переменные
   из env.example. Сейчас CORS_ALLOWED_ORIGINS декларируется, но
   middleware не подключен — ложное чувство защиты. Спроси у меня
   перед началом: A) подключить или B) удалить (фронт на том же
   origin caromoto-lt.com, скорее всего B).

3. M3 — ruff правила + django-upgrade target.
   Расширить ruff select до ["E", "F", "W", "I", "UP", "B", "C4",
   "DJ", "PL", "RUF"]. django-upgrade --target-version 5.1 → 5.2.
   Прогнать ruff check . --fix ОДНИМ коммитом "chore: ruff autofix".
   ВАЖНО: НЕ применять ruff format глобально — он переделает кавычки
   и раздует diff на тысячи строк. Только --fix по конкретным правилам.

4. M6 — Синхронизировать systemd unit paths.
   В repo scripts/*.service путь /var/www/logist2, реальный —
   /var/www/www-root/data/www/logist2. Привести в соответствие
   (или параметризовать через ENV). Создать scripts/install_systemd.sh.

5. M5 — Admin autocomplete_fields / raw_id_fields.
   Аудит ModelAdmin'ов: для FK к Client/Car/Container добавить
   autocomplete_fields (требует search_fields на target-модели).
   Замерить до/после на NewInvoiceAdmin.add_view.

6. M4 — Periodic CI job с миграциями.
   Сначала проверь, есть ли вообще .github/workflows/ — если нет,
   M4 превращается в "настроить CI с нуля + добавить два job'а:
   tests (с DisableMigrations, быстрый) и tests-with-migrations
   (ночной + по label run-migrations-ci)".

7. M7 — Structured logging + file handler с ротацией.
   RotatingFileHandler в /var/log/logist2/app.log, JSON-формат
   (python-json-logger), контекст request_id / user_id / domain,
   docs/LOGGING.md. Самая большая M-задача, последняя.

Альтернатива: если приоритет безопасности выше, можно сначала
вернуться к H5c часть 1 (закрыть /media/photos/ в nginx через
X-Accel-Redirect) или H5b (hCaptcha). Реши со мной перед началом.

Правила работы:
- 1 PR = 1 задача. Никаких сборных коммитов (исключение — M3 ruff
  autofix может быть отдельным "chore" коммитом перед задачей).
- НЕ использовать ruff format на существующих файлах целиком.
  Он переделывает кавычки и раздувает diff на 700+ строк. На новых
  файлах — ок. На существующих — только точечный
  `ruff check --fix --select <правило>`.
- Перед коммитом: pytest локально (должно быть 172 passed),
  manage.py check, manage.py makemigrations --check --dry-run.
- После push — scripts\deploy.ps1 и production smoke
  (homepage 200 + endpoint, специфичный для задачи).
- Если меняется UX — открой https://caromoto-lt.com/?track=MRSU5522473&photos=1
  и проверь живьём.
- В конце каждой задачи: пометь её [x] в roadmap, обнови CHANGELOG
  (после M1), предложи следующий шаг.
```

---

## Текущее состояние проекта (для контекста агента)

### Структура кода после H6

- `core/models/` — пакет (был `core/models.py` ~2100 строк):
  - подмодули: `_vehicle_types.py`, `lines.py`, `carriers.py`,
    `clients.py`, `warehouses.py`, `company.py`, `containers.py`,
    `cars.py`, `services.py`, `auto_transport.py`, `tasks.py`.
  - `__init__.py` реэкспортирует всё для совместимости
    `from core.models import X`.
- `core/admin/billing/` — пакет (был `core/admin_billing.py` ~1800 строк):
  - 10 подмодулей, `NewInvoiceAdmin` разнесён через миксины.
- `core/views_website/` — пакет (был `core/views_website.py` ~1140 строк):
  - 7 подмодулей: public, client_portal, api, tracking,
    photos_authed, ai_chat, signed_photos.
- `core/signals/` — пакет (был `core/signals.py` ~1550 строк):
  - 10 подмодулей по доменам (service_cache, container, car,
    car_service, service_catalog, invoice, transaction, bank,
    autotransport, cache_invalidation).
  - `__init__.py` импортирует подмодули (триггерит `@receiver`) и
    явно вызывает `connect_autotransport_signals()` +
    `connect_cache_invalidation_signals()` (m2m + dispatch_uid).

### Тесты

- **172 теста** (был 166 до H7, +6 для /api/track/).
- Прогон ~3 сек на SQLite (DisableMigrations).
- pytest-env override-ит `DJANGO_SETTINGS_MODULE=logist2.settings.test`
  даже если в шелле стоит `=logist2.settings.dev`. Если падает с
  `FieldDoesNotExist: NewBalanceTransaction.recipient_content_type`
  — значит pytest-env не подключился, проверь `[tool.pytest_env]`
  в `pyproject.toml`.

### Полезные напоминания

- **Прод**: `root@176.118.198.78:/var/www/www-root/data/www/logist2`,
  venv в `.venv`, settings = `logist2.settings.prod`.
  Деплой: `.\scripts\deploy.ps1`.
- **Локально**: venv в `.venv`, settings = `logist2.settings.dev`
  (manage.py подставляет по умолчанию).
- **БД локально**: postgres `arturas:arturas@localhost/logist2_db`.
  Синхронизация с прода: `.\scripts\sync_db.ps1`.
- **Backup PG на сервере**: `/var/backups/logist2/`, cron 03:30 UTC,
  retention 30 дней. Документ — `docs/BACKUPS.md`.
- **Sentry**: healthcheck `check_backup_freshness` в Celery beat 04:15.
- **Throttle**: глобальный `AnonRateThrottle=30/min` в
  `logist2/settings/base.py` применяется ко ВСЕМ DRF views.
- **CI**: проверить наличие `.github/workflows/` перед M4 — если
  нет, объём M4 кратно вырастает (нужно настраивать с нуля).

### Отложенные HIGH (H5b, H5c) — готовые сниппеты

См. `docs/PUBLIC_ENDPOINTS.md` §4:

- **§4.1** — nginx `location /media/photos/ { internal; }` + Django
  `X-Accel-Redirect`. Закрывает прямой доступ к фото мимо подписи.
  Самая важная по безопасности из отложенных.
- **§4.2** — hCaptcha (free tier 1M req/month), ENV
  `HCAPTCHA_SITE_KEY` / `HCAPTCHA_SECRET`, server-side verify через
  `https://hcaptcha.com/siteverify`.
- **§4.3** — `Referrer-Policy: same-origin`,
  `Cross-Origin-Resource-Policy: same-origin`,
  `X-Content-Type-Options: nosniff`, `Content-Security-Policy`.

---

## Что НЕ трогать в этой сессии

- HIGH-задачи (H1-H7) уже закрыты, переоткрывать без явного запроса
  пользователя нельзя.
- Полный переезд `requirements.txt` → `pyproject.toml` — это отдельная
  задача, не часть Medium, делается после M-блока.
- Миграция SQLite-tests → PostgreSQL-tests — намеренно не входит
  (CI на PG, локально SQLite — нормально).
- 2FA для админов, multi-tenant — отдельные проекты, не roadmap.
