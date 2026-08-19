# Настройка проекта на новом компьютере (Windows, с нуля)

Инструкция для **агента Cursor**: развернуть Logist2 на чистой Windows-машине,
где не установлено ничего — ни Python, ни PostgreSQL, ни Git. Результат должен
быть таким, чтобы `pytest` проходил, `runserver` поднимался, `git push` и
`.\scripts\deploy.ps1` работали, а миграции не расходились со второй машиной.

Читать вместе с `README.md` → «Setup for development» (там детали по профилям
настроек) и `.cursor/rules/git-workflow.mdc` (там ежедневный цикл работы).

---

## 0. Что человек должен принести руками

Эти файлы **не лежат в git** (см. `.gitignore`) и по флешке/менеджеру паролей
переносятся со старой машины. Без них проект не заведётся. Никогда не отправлять
их в git, в чат и в облако без шифрования.

| Что | Куда положить | Обязательно | Зачем |
|-----|---------------|-------------|-------|
| `.env` | корень репозитория | **да** | все настройки: пароль БД, `SECRET_KEY`, ключи API |
| `~/.ssh/id_rsa`, `id_rsa.pub`, `config` | `C:\Users\<user>\.ssh\` | **да** | SSH к VPS: `deploy.ps1` и `sync_db.ps1` |
| `locale/*/LC_MESSAGES/*.mo` (3 файла: en, lt, ru) | те же пути внутри `locale\` | **да** | скомпилированные переводы; `.mo` в git не хранятся, а `gettext` на машине не установлен |
| `docs/CREDENTIALS.md` | `docs\` | желательно | пароли и доступы (файл в `.gitignore`) |
| `media\` | корень репозитория | нет (≈430 МБ) | загруженные фото/сканы; без них в интерфейсе будут битые картинки, логика работает |

Чего переносить **не нужно**: `.venv\` (создаётся заново), `staticfiles\`
(генерируется `collectstatic`), `data\` (индекс AI RAG, пересоздаётся),
`certs\` и `token.json` (Revolut/Gmail живут только на сервере),
`__pycache__`, `.pytest_cache`, `test_db.sqlite3`.

`SECRET_KEY` из `.env` менять нельзя: банковские и accounting-токены в БД
зашифрованы ключом `ENCRYPTION_KEY`, а если он пуст — fallback'ом на
`SECRET_KEY` (см. `core/encryption.py`, `docs/ENCRYPTION_KEY.md`). С другим
ключом токены в скачанной с сервера БД не расшифруются.

---

## 1. Установить софт

Версии подобраны под вторую рабочую машину — держим их одинаковыми, чтобы не
ловить расхождения в лок-файлах и поведении.

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.13 -e
winget install --id PostgreSQL.PostgreSQL.17 -e
```

Ориентиры по версиям (что стоит на второй машине):

- Git 2.48+, установка со значениями по умолчанию. Важно оставить
  `core.autocrlf=true` (Git for Windows ставит это в системный конфиг) — иначе
  первый же коммит перепишет переводы строк во всех файлах и превратит diff в
  мусор.
- Python **3.13.x**. На VPS 3.10, в CI 3.12 — код обязан работать на 3.10,
  поэтому `ruff` настроен на `target-version = py310` (`pyproject.toml`).
  Локально 3.13 годится и совпадает со второй машиной.
- PostgreSQL **17.x**. При установке задать пароль суперпользователя
  `postgres`, порт 5432 оставить.
- **Добавить в `PATH`** `C:\Program Files\PostgreSQL\17\bin` — `scripts\sync_db.ps1`
  вызывает `psql`, `pg_restore` напрямую. Проверка: `psql --version`.
- OpenSSH-клиент: в Windows 10/11 встроен, проверить `ssh -V`. Если нет —
  «Параметры → Приложения → Дополнительные компоненты → OpenSSH Client».
- **Redis** — нужен, потому что `CACHE_BACKEND` по умолчанию `redis`
  (`logist2/settings/base.py`). На второй машине стоит сборка
  [Redis-x64-5.0.14.1 от tporadowski](https://github.com/tporadowski/redis/releases),
  распакована в профиль пользователя и зарегистрирована как служба Windows:

  ```powershell
  redis-server --service-install redis.windows-service.conf
  redis-server --service-start
  ```

  Альтернатива без установки Redis — дописать в `.env`:
  `CACHE_BACKEND=filebased` и `CHANNELS_BACKEND=memory`. Тогда кэш пойдёт в
  файлы, а WebSocket'ы — через `InMemoryChannelLayer`. Celery в dev работает в
  режиме `CELERY_TASK_ALWAYS_EAGER`, брокер ему не нужен.

Ставить `gettext`/`msgfmt` не требуется, если принесли готовые `.mo` (см. §0).
Нужен он только для `python manage.py compilemessages` после правки переводов.

После установки Git — представиться теми же данными, что на второй машине
(иначе в истории появится второй автор):

```powershell
git config --global user.name "Arturas"
git config --global user.email "arturas.gaizhutis@gmail.com"
```

---

## 2. Клонировать репозиторий и собрать окружение

```powershell
cd $HOME\PycharmProjects        # путь произвольный
git clone https://github.com/Arturas7777/logist2.git
cd logist2

py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` и `requirements-dev.txt` — скомпилированные pip-tools
лок-файлы, руками их не редактируют. Всё ставится из wheel'ов
(`psycopg2-binary`, `pillow`, `pymupdf`, `cryptography`), компилятор C++ не нужен.

Затем положить принесённые файлы: `.env` в корень, `.mo` в `locale\`,
ключи в `%USERPROFILE%\.ssh\`.

---

## 3. Поднять локальную базу

Создать роль и базу с **теми же** именем и паролем, что в `.env`
(`DB_USER=arturas`, `DB_NAME=logist2_db`, `DB_PASSWORD=<из .env>`). Роли нужны
права на `CREATE DATABASE` — `sync_db.ps1` каждый раз пересоздаёт базу; проще
всего локально дать `SUPERUSER`:

```powershell
psql -U postgres -h localhost -c "CREATE ROLE arturas WITH LOGIN SUPERUSER PASSWORD 'ПАРОЛЬ_ИЗ_ENV';"
psql -U postgres -h localhost -c "CREATE DATABASE logist2_db OWNER arturas;"
```

Залить актуальные данные с сервера:

```powershell
.\scripts\sync_db.ps1
```

Скрипт делает `pg_dump` на VPS, скачивает дамп и пересоздаёт локальную
`logist2_db`. Предупреждения `pg_restore` про владельцев объектов — норма.
Первый `ssh` попросит подтвердить отпечаток хоста — ответить `yes`.

---

## 4. Проверить, что всё сошлось

```powershell
python manage.py migrate --noinput
python manage.py makemigrations --check --dry-run   # должно быть "No changes detected"
python manage.py check
pytest
ruff check .
python manage.py runserver
```

- `makemigrations --check` — главный индикатор рассинхронизации: если он что-то
  «обнаружил» на свежесклонированном коде, значит модели и миграции разошлись
  ещё до вас (или в БД стоит чужая ветка) — разбираться до первого коммита.
- `pytest` идёт на SQLite через `logist2.settings.test`, локальную БД не трогает.
- Сайт: <http://127.0.0.1:8000/>, админка `/admin/`.

Проверить деплой-канал, ничего не меняя на сервере:

```powershell
ssh root@176.118.198.78 "cd /var/www/www-root/data/www/logist2 && git status --short && git log --oneline -1"
```

---

## 5. Правила, чтобы две машины не конфликтовали

1. **Начало работы** — всегда `git pull origin master`, затем
   `.\scripts\sync_db.ps1` и `python manage.py migrate --noinput`. Полный
   порядок описан в `.cursor/rules/git-workflow.mdc` (команда «начинаем
   работу»).
2. **Конец работы** — `git add`, коммит, `git push origin master` и
   `.\scripts\deploy.ps1`. Не оставлять незапушенные коммиты на машине, за
   которую сядете не сегодня: вторая машина их не увидит, а `deploy.ps1`
   откажется деплоить, пока локальные коммиты не в GitHub.
3. **Миграции — самое хрупкое место.** Не создавайте `makemigrations` на двух
   машинах параллельно: получите две миграции с одним родителем и конфликт
   номеров. Правило простое — перед `makemigrations` сделать `pull`, а после
   коммита сразу `push`. Если конфликт всё же случился, чинить через
   `python manage.py makemigrations --merge`, а не переименованием файлов.
4. **Зависимости.** После `pull`, который менял `requirements*.txt`, выполнить
   `pip install -r requirements.txt -r requirements-dev.txt`. Иначе на одной
   машине код пойдёт на другой версии Django/библиотек.
5. **`.env` не синхронизируется автоматически.** Добавили новый ключ на одной
   машине — перенесите его руками на вторую и на сервер, иначе фича молча
   отключится (`os.getenv(...)` вернёт пусто).
6. **`pre-commit install` делать не нужно.** Конфиг `.pre-commit-config.yaml` в
   репозитории есть, но на второй машине хуки не установлены — если включить их
   только здесь, поведение машин разойдётся, а лимит
   `check-added-large-files --maxkb=500` начнёт блокировать коммиты с PDF и фото
   из `marketing/`. Линт запускать вручную: `ruff check .`.
7. **Работа напрямую на сервере** (правки в `/var/www/.../logist2`) —
   запрещена, кроме `git pull`; иначе `deploy.ps1` увидит грязное дерево.
   Порядок синхронизации разобран в `docs/SYNC_GUIDE.md`.

---

## 6. Cursor на новой машине

Правила проекта (`.cursor/rules/*.mdc`) лежат в репозитории и подхватываются
автоматически после клонирования — отдельно настраивать ничего не нужно. Там же
зафиксировано, что окружение PowerShell (без `&&` и heredoc), что общение на
русском, и как выполняются команды «начинаем работу» / «заканчиваем работу».

Полезно открыть один раз: `.cursor/rules/project-overview.mdc` — обзор
подсистем, и `docs/QUICK_START.md` — короткая шпаргалка по командам.
