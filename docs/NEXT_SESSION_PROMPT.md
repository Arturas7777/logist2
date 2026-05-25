# Промт для следующей сессии

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
продолжаем работу по docs/ROADMAP_2026-05_high_medium.md.

Состояние из предыдущей сессии:
- H1–H4 закрыты (см. ROADMAP, последние коммиты в master).
- H5a закрыт: signed URL для фото готов и работает на проде.
  Документация в docs/PUBLIC_ENDPOINTS.md.
- H5b (CAPTCHA на /api/track/ и /api/contact/) и H5c (CSP + закрытие
  /media/photos/ в nginx через X-Accel-Redirect) — отложены, план в
  docs/PUBLIC_ENDPOINTS.md §4.

Дальше в порядке roadmap:

1. H6 — God-files split (поэтапно). Начни с аудита: какие файлы > 800
   строк, какие из них реально стоит разбить (views.py, models.py,
   admin.py могут оказаться оправданно большими). Подготовь план в
   docs/, и только потом разбивай поэтапно — каждый этап = отдельный PR.

2. H7 — следующая после H6 по roadmap.

Альтернатива: можно вернуться к H5b (hCaptcha) или H5c (CSP + nginx),
если приоритет безопасности выше архитектурного долга. Реши вместе со
мной перед началом работы.

Правила:
- 1 PR = 1 задача. Никаких сборных коммитов.
- Не используй `ruff format` на существующих файлах целиком — он
  переделывает кавычки и раздувает diff на 700+ строк. Применяй только
  на новых файлах. На существующих используй `ruff check --fix` для
  фиксов конкретных правил (например, `--fix-only --select I001` для
  упорядочивания импортов).
- Перед коммитом: `pytest` локально, потом `ruff check` точечно.
- После push — `scripts\deploy.ps1` и smoke-test (homepage 200 +
  специфичный для задачи endpoint).
- Если в фотогалерее или другом UX что-то меняется — обязательно
  открой `https://caromoto-lt.com/?track=MRSU5522473&photos=1` и
  проверь живьём, а не только curl'ом.

В конце задачи: пометь H6 как `[x]` в roadmap и предложи следующий шаг.
```

---

## Полезные напоминания для агента

- **Прод**: `root@176.118.198.78:/var/www/www-root/data/www/logist2`,
  venv в `.venv`, settings = `logist2.settings.prod`.
- **Локально**: venv в `.venv`, settings = `logist2.settings.dev`
  (manage.py подставляет по умолчанию).
- **БД локально**: postgres `arturas:arturas@localhost/logist2_db`.
- **Тесты**: 166 + 18 signed-photos = 184 шт, прогон ~3 сек на SQLite.
- **Backup PG на сервере**: `/var/backups/logist2/`, cron 03:30 UTC,
  retention 30 дней. Документ — `docs/BACKUPS.md`.
- **Sentry**: healthcheck `check_backup_freshness` в Celery beat 04:15.
- **Throttle**: глобальный `AnonRateThrottle=30/min` в
  `logist2/settings/base.py` применяется ко ВСЕМ DRF views. Если
  делаешь публичный endpoint, который должен пропускать много
  запросов — добавь `@throttle_classes([])`.

## Текущие H5b / H5c TODO

См. `docs/PUBLIC_ENDPOINTS.md` §4 — там готовые сниппеты:

- §4.1: nginx `location /media/photos/ { internal; }` + Django
  `X-Accel-Redirect`.
- §4.2: hCaptcha (free), ENV `HCAPTCHA_SITE_KEY` / `HCAPTCHA_SECRET`,
  server-side verify через `siteverify`.
- §4.3: `Referrer-Policy`, `Cross-Origin-Resource-Policy`,
  `X-Content-Type-Options`, `Content-Security-Policy`.
