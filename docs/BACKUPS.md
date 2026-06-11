# Бэкапы PostgreSQL — Logist2

Ежедневные автоматические дампы продакшн-БД с retention и healthcheck'ом
в Sentry.

## Где лежат

| Что | Где |
|---|---|
| Дампы (один файл в день) | `/var/backups/logist2/${DB_NAME}_YYYY-MM-DD.dump` |
| Лог бэкапа | `/var/log/logist2/backup.log` |
| Скрипт бэкапа | `/var/www/www-root/data/www/logist2/scripts/server_pg_backup.sh` |
| Cron-задание | `/etc/cron.d/logist2-backup` |
| Healthcheck | Celery beat task `core.tasks_monitoring.check_backup_freshness` |

## Что внутри одного дампа

- Формат `pg_dump -Fc` (custom-format) — бинарный со встроенным сжатием
  zlib (~80% сжатия для типичной OLTP-схемы).
- Флаги `--no-owner --no-acl` — дамп можно восстановить под любым
  PostgreSQL-пользователем, не нужны те же роли, что были в проде.
- Размер: ориентир ~4–10 МБ для текущей базы (растёт линейно с
  объёмом таблиц `BankTransaction`, `Car`, `NewInvoice`).

## Расписание

| Время (UTC, сервер) | Что |
|---|---|
| 03:30 | `scripts/server_pg_backup.sh` — pg_dump + smoke check + retention |
| 04:15 | Celery `check_backup_freshness` — алертит в Sentry, если последний .dump старше 36 часов |
| 30 дней | Retention — `*.dump` старше N дней удаляются (`RETENTION_DAYS=30`) |

Cron-расписание задаётся в `scripts/logist2-backup.cron`, healthcheck —
в `logist2/celery.py` → `beat_schedule`.

## Первичная установка

На сервере **один раз**:

```bash
ssh root@176.118.198.78
cd /var/www/www-root/data/www/logist2
git pull origin master
sudo ./scripts/install_logist2_backup.sh
```

Скрипт-инсталлятор (idempotent):

1. Создаёт `/var/backups/logist2/` и `/var/log/logist2/` (если ещё нет).
2. `chmod +x scripts/server_pg_backup.sh`.
3. Копирует `scripts/logist2-backup.cron` → `/etc/cron.d/logist2-backup`.
4. Релоадит cron daemon.

### Тестовый прогон вручную

```bash
sudo /var/www/www-root/data/www/logist2/scripts/server_pg_backup.sh
tail -n 30 /var/log/logist2/backup.log
ls -lh /var/backups/logist2
```

Ожидаемый вывод в логе:

```
[2026-05-25 12:00:01] === Backup start: logist2_db@localhost:5432 → /var/backups/logist2/logist2_db_2026-05-25.dump ===
[2026-05-25 12:00:03] OK: dump created, size=4.2M
[2026-05-25 12:00:03] Retention: deleted 0 files older than 30 days
[2026-05-25 12:00:03] Stats: 1 files in /var/backups/logist2, total 4.2M
[2026-05-25 12:00:03] === Backup done ===
```

## Восстановление

### Полное восстановление в существующую базу

```bash
# 1) Сбросить все активные соединения к БД (иначе DROP не пройдёт).
sudo -u postgres psql -c "
  SELECT pg_terminate_backend(pid)
  FROM pg_stat_activity
  WHERE datname='logist2_db' AND pid <> pg_backend_pid();
"

# 2) Пересоздать пустую БД.
sudo -u postgres psql -c "DROP DATABASE IF EXISTS logist2_db;"
sudo -u postgres psql -c "CREATE DATABASE logist2_db OWNER arturas;"

# 3) Залить дамп. Флаги --no-owner --no-acl уже встроены в pg_dump,
#    но дублируем для надёжности.
sudo -u postgres pg_restore \
    --no-owner --no-acl --clean --if-exists \
    -d logist2_db \
    /var/backups/logist2/logist2_db_2026-05-25.dump
```

### Восстановление в новую БД (без удаления старой)

```bash
sudo -u postgres psql -c "CREATE DATABASE logist2_db_restore;"
sudo -u postgres pg_restore --no-owner --no-acl \
    -d logist2_db_restore \
    /var/backups/logist2/logist2_db_2026-05-25.dump
```

### Восстановление одной таблицы

```bash
# 1) Посмотреть список объектов в дампе.
pg_restore --list /var/backups/logist2/logist2_db_2026-05-25.dump > toc.txt

# 2) Оставить в toc.txt только нужные строки (например, по таблице core_car).
# Открыть в редакторе, удалить лишнее.

# 3) Восстановить только то, что в toc.txt.
sudo -u postgres pg_restore --no-owner --no-acl \
    -L toc.txt \
    -d logist2_db \
    /var/backups/logist2/logist2_db_2026-05-25.dump
```

### Локальное восстановление (на dev-машине)

```powershell
# Скачать дамп с сервера
scp root@176.118.198.78:/var/backups/logist2/logist2_db_2026-05-25.dump .

# Восстановить (PowerShell, локально)
$env:PGPASSWORD = "your-local-db-password"
psql -h localhost -U arturas -d postgres -c "DROP DATABASE IF EXISTS logist2_db;"
psql -h localhost -U arturas -d postgres -c "CREATE DATABASE logist2_db OWNER arturas;"
pg_restore -h localhost -U arturas -d logist2_db --no-owner --no-acl logist2_db_2026-05-25.dump
```

Альтернатива — `scripts/sync_db.ps1` (делает свежий дамп и заливает локально
за одну команду).

## Проверка целостности

### Быстро (~1 секунда)

```bash
pg_restore --list /var/backups/logist2/logist2_db_2026-05-25.dump > /dev/null && echo OK
```

Это читает оглавление дампа. Если файл битый / обрезан — `pg_restore`
вернёт non-zero exit code. Этот же check встроен в
`scripts/server_pg_backup.sh` сразу после `pg_dump` (smoke check).

### Полная проверка (~ время восстановления)

```bash
# Развернуть в тестовую БД и убедиться, что приложение поднимается
sudo -u postgres psql -c "CREATE DATABASE logist2_db_verify;"
sudo -u postgres pg_restore --no-owner --no-acl \
    -d logist2_db_verify \
    /var/backups/logist2/logist2_db_2026-05-25.dump

# Прогнать Django check
DB_NAME=logist2_db_verify python manage.py check
DB_NAME=logist2_db_verify python manage.py showmigrations | grep -v '\[X\]'

# Подчистить
sudo -u postgres psql -c "DROP DATABASE logist2_db_verify;"
```

Стоит делать раз в квартал.

## Журнал restore-учений

Восстановление прод-дампа в отдельную локальную БД + `manage.py check`.
Проводить не реже раза в квартал, результат фиксировать здесь.

| Дата | Дамп | Результат |
|---|---|---|
| 2026-06-11 | `logist2_db_2026-06-11.dump` (5.3M, 851 объект) | OK: восстановлен в `logist2_restore_drill`; 754 car / 375 invoice / 474 transaction / 467 bank transaction; `manage.py check` чист; единственная ошибка restore — `COMMENT ON EXTENSION pg_stat_statements` (безвредно, расширения нет на dev-машине). |

> Замечание: при restore на машину без `pg_stat_statements` pg_restore
> печатает 1–2 игнорируемые ошибки про это расширение — это норма
> (`--no-owner --no-acl` не переносит расширения).

## Healthcheck в Sentry

Celery-задача `core.tasks_monitoring.check_backup_freshness` ежедневно
в 04:15 проверяет:

1. Существует ли директория `BACKUP_DIR` (по умолчанию `/var/backups/logist2`).
2. Есть ли в ней хоть один `*.dump`.
3. Самый свежий `*.dump` не старше `BACKUP_MAX_AGE_HOURS` (по умолчанию 36).

При проблеме пишет `logger.warning(...)`, что через Sentry LoggingIntegration
становится warning-issue с тегом `task=check_backup_freshness`. В Sentry
issues дедуплицируются по сообщению — повторные алерты группируются.

На локалке / в CI задача проверяет, что директория просто отсутствует и
тихо возвращает `not_configured` без алерта.

### Тюнинг

В `.env`:

```
BACKUP_DIR=/var/backups/logist2
BACKUP_MAX_AGE_HOURS=36
```

(пробрасываются в `settings.BACKUP_DIR` / `BACKUP_MAX_AGE_HOURS` — но
если не заданы, используются дефолты).

## Off-site (TODO)

Локальный диск ≠ disaster recovery. Если сервер сгорает целиком — дампы
сгорают вместе. План на будущее:

- rclone в S3-совместимое хранилище (Backblaze B2 ≈ €0.5/мес для нашего
  объёма) либо в Hetzner Storage Box.
- Шифрование GPG перед загрузкой (`gpg --symmetric` с ключом в .env).
- Retention отдельно для off-site: 7 daily + 4 weekly + 12 monthly.

Это **отдельная задача** в roadmap (см. H4 в `docs/ROADMAP_2026-05_high_medium.md`,
секция «Опционально: rclone-выгрузка в S3/Backblaze»). Сейчас локальные
бэкапы — необходимый минимум, off-site — желаемое дополнение.

## Troubleshooting

| Симптом | Что проверить |
|---|---|
| Sentry issue `backup check: latest dump … is N.Nh old` | `tail -n 50 /var/log/logist2/backup.log` — что упало в pg_dump? Проверить `systemctl status cron`. Прогнать вручную: `sudo bash scripts/server_pg_backup.sh`. |
| Sentry issue `backup check: no .dump files in …` | Cron вообще не запускался: `ls -la /etc/cron.d/logist2-backup`, `tail /var/log/syslog \| grep CRON`. |
| Sentry issue `backup check: directory … does not exist` | `scripts/install_logist2_backup.sh` не запускался либо удалили директорию. |
| `pg_dump: error: connection to server … failed` | Postgres недоступен или пароль в `.env` устарел. Проверить `psql -h localhost -U $DB_USER -d $DB_NAME`. |
| `smoke check (pg_restore --list) failed` | Диск переполнен (`df -h`), либо pg_dump оборвался. Файл `.tmp` остался — удалить, разобраться. |
| Бэкап весит 0 байт | Тот же диагноз — pg_dump оборвался. См. exit code в логе. |

## Связанные файлы

- `scripts/server_pg_backup.sh` — собственно бэкап.
- `scripts/logist2-backup.cron` — расписание.
- `scripts/install_logist2_backup.sh` — bootstrap на сервере.
- `core/tasks_monitoring.py` → `check_backup_freshness` — healthcheck.
- `logist2/celery.py` → `beat_schedule` → `check-backup-freshness-daily`.
- `scripts/sync_db.ps1` — синхронизация прод-БД на локальную машину
  (использует тот же `pg_dump`, но без cron-обвязки).
