# План: переписка в карточке контейнера (Gmail IMAP)

Цель: в карточке каждого контейнера отображать всю почтовую переписку, относящуюся к
нему — автоматически подтянутую из основного Gmail-ящика. Фаза 1 — только чтение и
привязка (без отправки).

## Согласованные решения (из обсуждения)

| Параметр           | Значение                                                          |
| ------------------ | ----------------------------------------------------------------- |
| Провайдер          | Gmail / Google Workspace                                          |
| Аутентификация     | App Password (16-значный пароль приложения) — начальный вариант   |
| Фазы сейчас        | **Только Phase 1** (чтение + привязка). Отправка — следующим шагом |
| Критерии матчинга  | Номер контейнера (ISO 6346) **или** номер букинга **или** ответ в уже привязанном треде |
| Папки Gmail        | INBOX + Sent (чтобы видеть и исходящие)                           |

Поле `Container.booking_number` уже добавлено (миграция `0151`).

## Архитектура

```
┌─────────────┐   IMAP pull    ┌──────────────┐   regex match   ┌─────────────┐
│   Gmail     │ ──(каждые 5м)──▶│  Celery task │ ───(по номеру)─▶│  Container  │
│ INBOX+Sent  │                 │ email_sync   │  (по букингу)   │  .emails    │
└─────────────┘                 └──────┬───────┘  (по thread_id) └─────┬───────┘
                                       │                                │
                                       ▼                                ▼
                                ┌──────────────┐   отображение   ┌──────────────┐
                                │ContainerEmail│────────────────▶│ change_form  │
                                │   (модель)   │                 │  (сайдбар)   │
                                └──────────────┘                 └──────────────┘
```

## Структура модели `ContainerEmail`

```python
# core/models_email.py  (новый файл, чтобы не раздувать models.py)

class ContainerEmail(models.Model):
    DIRECTION_CHOICES = [
        ('INCOMING', 'Входящее'),
        ('OUTGOING', 'Исходящее'),
    ]
    MATCHED_BY_CHOICES = [
        ('CONTAINER_NUMBER', 'По номеру контейнера'),
        ('BOOKING_NUMBER',   'По номеру букинга'),
        ('THREAD',           'По треду (ответ на привязанное)'),
        ('MANUAL',           'Привязано вручную'),
        ('UNMATCHED',        'Не привязано'),
    ]

    container    = models.ForeignKey(
        'Container', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='emails',
    )
    # RFC 5322 Message-ID — уникальный идентификатор письма
    message_id   = models.CharField(max_length=500, unique=True, db_index=True)
    # Собственный thread_id: либо Gmail X-GM-THRID, либо первый Message-ID в треде
    thread_id    = models.CharField(max_length=500, db_index=True)
    in_reply_to  = models.CharField(max_length=500, blank=True, default='')
    references   = models.TextField(blank=True, default='')   # пробел-разделённый список

    direction    = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    from_addr    = models.CharField(max_length=500)
    to_addrs     = models.TextField(blank=True, default='')
    cc_addrs     = models.TextField(blank=True, default='')
    subject      = models.CharField(max_length=1000, blank=True, default='')
    body_text    = models.TextField(blank=True, default='')
    body_html    = models.TextField(blank=True, default='')
    snippet      = models.CharField(max_length=500, blank=True, default='')  # для превью
    received_at  = models.DateTimeField(db_index=True)
    gmail_uid    = models.BigIntegerField(null=True, blank=True, db_index=True)

    # Вложения: [{filename, size, content_type, storage_path}]
    attachments_json = models.JSONField(default=list, blank=True)

    matched_by   = models.CharField(max_length=20, choices=MATCHED_BY_CHOICES, default='UNMATCHED')
    is_read      = models.BooleanField(default=False)  # отметка «прочитано в интерфейсе»

    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['container', '-received_at']),
            models.Index(fields=['thread_id', 'received_at']),
        ]
        ordering = ['-received_at']
```

Вложения хранить в `MEDIA_ROOT/container_emails/<yyyy>/<mm>/<uid>/<filename>` —
путь писать в `attachments_json[i].storage_path`.

## Файлы, которые нужно создать/изменить

### Новые
1. `core/models_email.py` — модель `ContainerEmail`
2. `core/services/email_ingest.py` — класс `GmailImapClient` + функция `sync_mailbox()`
3. `core/services/email_matcher.py` — функция `match_email_to_container(email_dict) -> (Container|None, matched_by)`
4. `core/tasks_email.py` (или добавить в `core/tasks.py`) — celery task `sync_emails_from_imap`
5. `core/migrations/0152_add_container_email.py` — миграция модели
6. `templates/admin/core/container/_emails_panel.html` — сайдбар с перепиской
7. `templates/admin/core/container/_email_detail.html` — контент одного письма (раскрываемый)
8. `core/admin/email.py` — `ContainerEmailAdmin` (отдельный список для ручной ревизии непривязанных)

### Правки существующих
1. `core/models.py` → импорт `from core.models_email import ContainerEmail` (или через `__init__` приложения)
2. `core/admin/__init__.py` → подключить `email.py`
3. `templates/admin/core/container/change_form.html` → подключить `{% include "admin/core/container/_emails_panel.html" %}`
4. `logist2/celery.py` → добавить в `beat_schedule`:
   ```python
   'sync-emails-from-imap': {
       'task': 'core.tasks_email.sync_emails_from_imap',
       'schedule': crontab(minute='*/5'),
   },
   ```
5. `env.example` → новые переменные:
   ```
   # IMAP для чтения входящей переписки по контейнерам
   IMAP_HOST=imap.gmail.com
   IMAP_PORT=993
   IMAP_USER=your_mailbox@gmail.com
   IMAP_APP_PASSWORD=xxxxxxxxxxxxxxxx
   IMAP_FOLDERS=INBOX,"[Gmail]/Sent Mail"
   IMAP_LOOKBACK_DAYS=30
   ```
6. `logist2/settings/base.py` → прочитать эти переменные.

## Алгоритм Celery-задачи `sync_emails_from_imap`

```
1. Подключаемся к IMAP (imaplib.IMAP4_SSL, login, select folder).
2. Для каждой папки (INBOX, Sent):
   a. Находим max gmail_uid уже сохранённых писем для этой папки.
      Если нет — берём письма за последние IMAP_LOOKBACK_DAYS дней.
   b. search UID <last_uid+1>:* (или SINCE date на первом запуске)
   c. Для каждого UID:
      - fetch RFC822 + X-GM-THRID + X-GM-LABELS
      - распарсить email.message_from_bytes
      - извлечь: Message-ID, From, To, Cc, Subject, Date, In-Reply-To, References,
        body_text (text/plain part), body_html (text/html part), attachments
      - если Message-ID уже есть в БД — пропустить (идемпотентность)
      - direction = OUTGOING если from_addr == IMAP_USER или папка == Sent, иначе INCOMING
      - thread_id = X-GM-THRID если есть, иначе References[0] или Message-ID
      - attachments: сохранить на диск, записать мета в attachments_json
      - вызвать match_email_to_container → получить (container, matched_by)
      - создать запись ContainerEmail (transaction.atomic, get_or_create по message_id)
3. Логировать результат: processed / new / matched / unmatched.
```

### Матчинг (`match_email_to_container`)

Порядок проверок (первый сработавший — победитель):

1. **Тред**: если в БД уже есть письмо с тем же `thread_id` и `container_id IS NOT NULL` — берём его контейнер → `matched_by=THREAD`.
2. **Контейнер по Message-ID родителя**: если `In-Reply-To` указывает на уже привязанное письмо — тот же контейнер.
3. **Номер контейнера**: regex `\b[A-Z]{4}\d{7}\b` по `subject + body_text`.
   - Нормализовать: `A-Z` + 7 цифр. Сопоставить с `Container.objects.filter(number__iexact=...)`.
   - Если найдено ровно одно совпадение → `matched_by=CONTAINER_NUMBER`.
   - Если найдено несколько (письмо про несколько контейнеров сразу) → привязать к
     каждому через доп. M2M-таблицу. **Решение**: для Phase 1 берём первый
     найденный + создаём отдельные записи по одной на контейнер (дублирование
     приемлемо). Более чистый вариант — M2M — опционально.
4. **Букинг**: берём все `Container.booking_number` не пустые, ищем в `subject + body_text`
   подстроку (точное совпадение через границы слов `\b<booking>\b`, case-insensitive).
   - Чтобы не сканировать миллион контейнеров на каждое письмо: один раз в memory
     загрузить dict `{booking_lower: container_id}` в начале задачи.
   - `matched_by=BOOKING_NUMBER`.
5. Ничего не нашли → `matched_by=UNMATCHED`, `container=None`.

### Безопасность / надёжность
- App password хранится в env, не в БД.
- В `body_html` чистить от вредного через `bleach` перед рендером в админке.
- Лок на задачу: celery `task_time_limit` + advisory lock в postgres, чтобы два
  воркера не тянули одновременно.
- Идемпотентность по `message_id` (UNIQUE).

## UI в карточке контейнера

### Сайдбар-блок (шаблон `_emails_panel.html`)

```
┌─────────────────────────────────────┐
│ 📧 Переписка (12)   [обновить] [+] │
├─────────────────────────────────────┤
│ ● 17.04 16:23  Иван Петров          │
│   Re: MSKU1234567 документы         │
│   Пришлите пожалуйста скан TD...    │
├─────────────────────────────────────┤
│ ○ 17.04 10:05  shipping@line.com    │
│   MSKU1234567 ETA update            │
│   Vessel delayed 2 days due to...   │
├─────────────────────────────────────┤
│ ○ 16.04 09:12  booking@forwarder    │
│   Booking ABC123 confirmed          │
│   Dear customer, your booking...    │
└─────────────────────────────────────┘
```

- `●` — непрочитано (is_read=False), `○` — прочитано.
- Клик по строке → inline-expand с полным телом + кнопки скачать вложения.
- Кнопка «обновить» → AJAX вызывает `sync_emails_from_imap.apply_async()` (dry-run с таймаутом 20сек; если долго — просто показать «синхронизация идёт в фоне»).
- Кнопка `[+]` — «привязать существующее письмо» (модалка с поиском из UNMATCHED) — опционально для phase 1.

### Рендеринг тела письма
- По умолчанию text (как моноширинный цитируемый блок).
- Кнопка «показать HTML» переключает на `body_html` (через `bleach.clean` с whitelist тегов).
- Вложения: картинки-превью inline, остальное — ссылки на скачивание.

### View-эндпоинты
```
/admin/core/container/<id>/change/       — уже есть, сайдбар включается
/admin/emails/<email_id>/                — детальная страница / partial для expand (AJAX)
/admin/emails/<email_id>/attachment/<i>/ — скачивание вложения (permission check)
/admin/emails/<email_id>/mark-read/      — POST, mark is_read=True
/admin/emails/sync/                      — POST, ручной триггер celery-задачи
```

## Отдельный админ-список `ContainerEmailAdmin`

- Список писем с фильтром `matched_by` (в первую очередь `UNMATCHED`).
- Action «привязать к контейнеру» → форма с autocomplete по контейнерам.
- Колонки: direction, from, subject, received_at, container, matched_by.

## Зависимости (в `requirements.txt`)

Уже есть, скорее всего, но нужно проверить:
- `bleach` — санитайзинг HTML (возможно уже есть)
- `python-dateutil` — парсинг заголовков Date (возможно уже есть)

Новых внешних зависимостей не требуется: `imaplib` и `email` — stdlib.

## Чеклист реализации

- [ ] Создать модель `ContainerEmail` + миграция `0152`
- [ ] Регистрация в `core/admin/email.py` (отдельный list для UNMATCHED)
- [ ] Реализовать `GmailImapClient` в `core/services/email_ingest.py`
  - [ ] connect/login/logout с ретраем
  - [ ] fetch UIDs since last / since N days
  - [ ] parse message (+ X-GM-THRID через `fetch UID (X-GM-THRID)`)
  - [ ] скачать вложения на диск
- [ ] Реализовать `match_email_to_container` в `core/services/email_matcher.py`
- [ ] Celery-задача `sync_emails_from_imap` + регистрация в `beat_schedule`
- [ ] Добавить env-переменные в `env.example` + чтение в `settings/base.py`
- [ ] Шаблон `_emails_panel.html` + включение в `change_form.html`
- [ ] View-эндпоинты для expand / attachment / mark-read / sync
- [ ] Юнит-тесты:
  - матчер: контейнер по номеру, по букингу, по треду, UNMATCHED
  - парсер: multipart / attachments / кириллица в subject
  - идемпотентность: повторный запуск задачи не создаёт дублей
- [ ] Документация: README.md-раздел «Как настроить app password в Google»
- [ ] Ручная проверка: закинуть реальные письма и посмотреть привязку

## Потенциальные грабли

1. **Gmail thread-id**: доступен только через extension `X-GM-THRID`, нужно
   использовать `conn.fetch(uid, '(X-GM-THRID)')`. Если переехать на другой IMAP —
   fallback на `References[0]`.
2. **Большие вложения**: ограничить скачиваемый размер (например, 25 МБ на вложение,
   100 МБ на письмо). Больше — только метаданные.
3. **Кодировки**: использовать `email.header.decode_header` для subject, `part.get_content_charset()` для тела.
4. **Часовые пояса**: `received_at` сохраняем в UTC (`email.utils.parsedate_to_datetime`).
5. **Дубли INBOX/Sent**: одно письмо может быть в обеих папках. Спасает уникальность по `message_id`.
6. **Rate limiting Gmail**: при первом прогоне с `IMAP_LOOKBACK_DAYS=30` может быть
   много писем. Добавить батчинг по 50 писем и `sleep(0.2)` между fetch'ами.
7. **Букинг-коллизии**: если у двух разных контейнеров одинаковый `booking_number`
   (например, консолидация груза) — письмо привяжется к первому найденному. Нужно
   логировать предупреждение и/или завести M2M-таблицу в будущем.

## Следующие фазы (вне текущего плана)

- **Phase 2**: отправка/ответы через SMTP, In-Reply-To/References, аттачи.
- **Phase 3**: OAuth (Gmail API) вместо app password, push-уведомления через
  `users.watch`, отметка прочитано синхронизируется с Gmail.
- **Phase 4**: AI-саммари длинных тредов, категоризация (линия / клиент / таможня),
  автоизвлечение ETA из писем линии → обновление `Container.eta`.
