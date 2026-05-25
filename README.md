# Logist2 — Система управления логистикой

Система управления логистикой для автомобильных перевозок: контейнеры, склады,
клиенты, инвойсы, банкинг (Revolut/Paysera), AI-аудит счетов и клиентский
портал.

Владелец: **Caromoto Lithuania MB**, сайт — [caromoto-lt.com](https://caromoto-lt.com).

## Быстрый старт (Windows, для уже настроенного venv)

1. Запустите проект:

   ```
   START_ME.bat
   ```

   Скрипт активирует `.venv`, применяет миграции, собирает статику и
   запускает Django dev-сервер на <http://127.0.0.1:8000/>.

2. Зайдите в админку: <http://127.0.0.1:8000/admin/> (логин/пароль —
   ваш суперпользователь).

> Если venv ещё не настроен или вы только склонировали репозиторий —
> идите в раздел [Setup for development](#setup-for-development).

## Setup for development

Полный сценарий для **нового разработчика** на чистой машине. После него
`pytest` должен пройти, а `python manage.py runserver` — поднять локальный
сервер.

### 1. Требования

- Python **3.10+** (рекомендуется 3.12; на сервере 3.10, локально работает
  и 3.13).
- PostgreSQL **14+** (для локальной БД; тесты идут на SQLite через
  `logist2.settings.test`).
- Git, [PowerShell](https://learn.microsoft.com/powershell/) для
  Windows-скриптов.
- Опционально: Redis (для Channels/Celery в продоподобном режиме). В dev
  работает InMemoryChannelLayer и `CELERY_TASK_ALWAYS_EAGER`, поэтому
  Redis не обязателен.

### 2. Клонирование и venv

```powershell
git clone https://github.com/Arturas7777/logist2.git
cd logist2

python -m venv .venv
.\.venv\Scripts\activate              # Windows PowerShell
# source .venv/bin/activate          # Linux / macOS

pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements-dev.txt` подтягивает основной `requirements.txt` плюс
тестовые/линтер-зависимости (`pytest`, `pytest-django`, `pytest-cov`,
`freezegun`, `ruff`).

### 3. Переменные окружения

```powershell
copy env.example .env
```

Минимально, чтобы локально что-то запустилось:

- `SECRET_KEY` — любой длинный random.
- `DEBUG=True` (для dev).
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST=localhost`, `DB_PORT=5432`
  — учётка локального Postgres.
- `ENCRYPTION_KEY` — сгенерировать командой
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

Остальное (Revolut, site.pro, Gmail, AI) для базовой разработки не
нужно — оставьте пусто.

> **Профиль настроек.** Локально `manage.py`, `wsgi.py`, `asgi.py` и
> `celery.py` по умолчанию загружают `logist2.settings.dev` (DEBUG=True,
> debug-toolbar при `USE_DEBUG_TOOLBAR=1`, CELERY_TASK_ALWAYS_EAGER).
> На сервере systemd-юниты (`scripts/*.service`) явно выставляют
> `DJANGO_SETTINGS_MODULE=logist2.settings.prod`, `scripts/deploy.ps1` —
> то же для миграций. Тесты идут через `logist2.settings.test` (SQLite,
> `DisableMigrations`). Переопределить вручную можно так:
>
> ```powershell
> $env:DJANGO_SETTINGS_MODULE = "logist2.settings.prod"   # проверить prod-конфиг локально
> ```

### 4. База данных

Вариант A. Синхронизироваться с продом (быстро, актуальные данные):

```powershell
.\scripts\sync_db.ps1
```

Скрипт делает `pg_dump` на сервере, скачивает дамп и заливает в локальную
БД `logist2_db`.

Вариант B. Поднять пустую БД и накатить миграции:

```powershell
createdb logist2_db                    # либо через pgAdmin/psql
python manage.py migrate
python manage.py createsuperuser
```

### 5. Тесты и линтер

```powershell
pytest                                  # 172+ тестов, ~3 сек на SQLite
ruff check .
ruff format .
```

Тесты используют `logist2.settings.test` (SQLite, миграции отключены).
Конфиг — в `pyproject.toml`. `pytest-env` безусловно override-ит
`DJANGO_SETTINGS_MODULE` на `logist2.settings.test`, даже если в шелле
осталась переменная от предыдущего `runserver` (`logist2.settings.dev`).
`freezegun` используется для billing-тестов с заморозкой времени.

### 6. Запуск dev-сервера

```powershell
.\START_ME.bat
# или:
python manage.py runserver 127.0.0.1:8000
```

После запуска: <http://127.0.0.1:8000/>, админка — `/admin/`.

## Деплой на VPS

```powershell
.\scripts\deploy.ps1
```

Скрипт:

1. Проверяет, что все локальные коммиты запушены в GitHub.
2. На сервере одной SSH-сессией: `git pull` → `migrate` → `collectstatic`
   → restart gunicorn/daphne/celery.
3. Показывает статус сервисов.

Сервер: `root@176.118.198.78`, путь
`/var/www/www-root/data/www/logist2`. Подробнее — см.
`.cursor/rules/git-workflow.mdc`.

## Структура проекта

```
logist2/
├── core/                      # Единственное Django-приложение (вся бизнес-логика)
│   ├── models/                # Пакет (бывший models.py, разнесён в H6a)
│   │   ├── __init__.py        #   реэкспорт всех моделей (from core.models import X)
│   │   ├── cars.py, containers.py, clients.py, warehouses.py,
│   │   ├── carriers.py, lines.py, company.py, services.py,
│   │   └── auto_transport.py, tasks.py, _vehicle_types.py
│   ├── models_billing.py      # NewInvoice, Transaction, ExpenseCategory
│   ├── models_banking.py      # BankConnection, BankAccount, BankTransaction
│   ├── models_accounting.py   # site.pro интеграция
│   ├── models_website.py      # ClientUser, AIChat, CarPhoto, NewsPost
│   ├── models_email.py        # ContainerEmail, ContactMessage, EmailGroup
│   ├── models_contact.py      # Contact, ContactPhone, ContactEmail
│   ├── models_invoice_audit.py, models_scans.py, models_monitoring.py
│   ├── admin/                 # ModelAdmin'ы (пакет)
│   │   ├── billing/           # Пакет (бывший admin_billing.py, разнесён в H6b)
│   │   └── …                  # container, car, autotransport и т.д.
│   ├── signals/               # Пакет (бывший signals.py, разнесён в H6d)
│   │   ├── __init__.py        #   импорт submodules → регистрация @receiver
│   │   └── container.py, car.py, invoice.py, transaction.py, …
│   ├── views_website/         # Пакет (бывший views_website.py, разнесён в H6c)
│   │   ├── public.py, client_portal.py, api.py, tracking.py,
│   │   └── photos_authed.py, ai_chat.py, signed_photos.py
│   ├── services/              # billing, revolut, sitepro, reconciliation, AI, signed_urls
│   ├── tests/                 # pytest-тесты (172+)
│   ├── tasks.py               # Celery-задачи
│   └── …
├── logist2/                   # Django project package
│   ├── settings/
│   │   ├── base.py            # Базовые настройки
│   │   ├── dev.py             # Dev-профиль (DEBUG, debug-toolbar)
│   │   ├── prod.py            # Prod-профиль
│   │   └── test.py            # Test-профиль (SQLite + DisableMigrations)
│   ├── settings_security.py   # SecurityHeadersMiddleware и др.
│   ├── urls.py
│   ├── celery.py
│   └── asgi.py / wsgi.py
├── templates/                 # HTML-шаблоны (admin + website + email)
├── static/, staticfiles/      # Статика
├── locale/                    # Переводы (lt, en, ru)
├── scripts/                   # deploy.ps1, sync_db.ps1, systemd units, nginx confs,
│                              #   server_pg_backup.sh, install_logist2_backup.sh
├── docs/                      # Внутренняя документация, roadmap'ы, BACKUPS.md,
│                              #   PUBLIC_ENDPOINTS.md, ENCRYPTION_KEY.md
├── CHANGELOG.md               # История релизов (Keep a Changelog)
├── pyproject.toml             # pytest + ruff конфиги
├── requirements.txt           # Prod-зависимости
├── requirements-dev.txt       # + тесты и линтер (pytest, pytest-env, freezegun, ruff)
├── env.example                # Шаблон .env
└── START_ME.bat               # Локальный запуск (Windows)
```

## Основные функции

- **Логистика** — контейнеры, авто, склады, автовозы, статусы доставки.
- **Клиенты и партнёры** — линии, перевозчики, склады, компании.
- **Биллинг** — инвойсы (NewInvoice/InvoiceItem), платежи, балансы,
  автогенерация счетов при сменe статусов.
- **Банкинг** — Revolut Business API, Paysera (через site.pro), кэш и
  сверка транзакций с инвойсами.
- **Бухгалтерия** — пуш инвойсов в site.pro, синхронизация оплат.
- **AI-аудит счетов** — распознавание PDF от поставщиков через Anthropic
  Claude.
- **Клиентский портал** — публичный сайт, tracking, фото контейнеров,
  AI-чат.

## Технологии

- **Backend:** Django 5.2.14, Python 3.10+ (рекомендуется 3.12)
- **Database:** PostgreSQL (тесты — SQLite через `logist2.settings.test`)
- **WebSockets:** Django Channels + Daphne (Redis в проде, InMemory в dev)
- **Tasks:** Celery + Redis (в dev — `CELERY_TASK_ALWAYS_EAGER`)
- **API:** Django REST Framework
- **Frontend:** Django templates + Bootstrap 5 + HTMX
- **Server:** Nginx + Gunicorn + Daphne, статика через WhiteNoise
- **AI:** Anthropic Claude (аудит счетов), OpenAI (клиентский чат + RAG)
- **Banking:** Revolut Business API (`cryptography`-шифрование токенов)

## Документация

- [`CHANGELOG.md`](CHANGELOG.md) — история значимых изменений
  (формат [Keep a Changelog](https://keepachangelog.com/)).
- `.cursor/rules/git-workflow.mdc` — git workflow, deploy, sync_db.
- `.cursor/rules/project-overview.mdc` — подробный обзор проекта.
- `docs/BACKUPS.md` — автоматизированные бэкапы PostgreSQL (cron,
  retention 30 дней, Sentry healthcheck).
- `docs/PUBLIC_ENDPOINTS.md` — аудит публичных endpoints, signed URLs
  для фото (H5a).
- `docs/ENCRYPTION_KEY.md` — ротация ключа шифрования банковских токенов.
- `docs/ROADMAP_2026-05_high_medium.md` — текущий roadmap по High/Medium-задачам.
- `docs/` — остальные roadmap'ы, аудиты, инструкции по ключевым
  подсистемам (banking, AI и т.п.).

## Поддержка

Caromoto Lithuania MB · <https://caromoto-lt.com>
