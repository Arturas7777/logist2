# План: переписка в карточке контейнера (Gmail API + OAuth 2.0)

Цель: в карточке каждого контейнера отображать всю почтовую переписку, относящуюся к
нему — автоматически подтянутую из основного Gmail-ящика. Фаза 1 — только чтение и
привязка (без отправки).

## Согласованные решения

| Параметр           | Значение                                                          |
| ------------------ | ----------------------------------------------------------------- |
| Провайдер          | Google Workspace (`@caromoto.com`)                                |
| Аутентификация     | **OAuth 2.0 via Gmail API** (App Password заблокирован админом)  |
| Фазы сейчас        | **Только Phase 1** (чтение + привязка). Отправка — следующим шагом |
| Критерии матчинга  | Номер контейнера (ISO 6346) **или** номер букинга **или** ответ в уже привязанном треде |
| Источник писем     | Gmail INBOX + Sent (через единый API, не нужно явно выбирать папки) |

## Статус подготовки (на момент написания)

- ✅ Поле `Container.booking_number` (миграция `0151`)
- ✅ Google Cloud проект `Logist2-email` в организации `caromoto.com`
- ✅ Gmail API включён
- ✅ OAuth consent screen настроен как **Internal**
- ✅ OAuth client ID создан (Desktop app, `Logist2 Email Client`)
- ✅ `client_secret.json` + `token.json` (refresh_token) получены локально в `C:\Users\art-f\gmail-oauth\`
- ✅ Одноразовый скрипт `scripts/get_gmail_refresh_token.py` на случай обновления токена
- ✅ `.gitignore` защищает от коммита секретов (`client_secret*.json`, `token.json`, `secrets/`)

## Архитектура

```
┌─────────────┐  Gmail API     ┌──────────────┐   regex match   ┌─────────────┐
│   Gmail     │ ──(каждые 5м)──▶│  Celery task │ ───(по номеру)─▶│  Container  │
│ (OAuth)     │  history.list   │ email_sync   │  (по букингу)   │  .emails    │
└─────────────┘                 └──────┬───────┘  (по threadId)  └─────┬───────┘
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
    # Gmail API message id (hex string, стабильный для истории API)
    gmail_id     = models.CharField(max_length=64, blank=True, default='', db_index=True)
    # Gmail history id — чтобы инкрементально подтягивать через users.history.list
    gmail_history_id = models.BigIntegerField(null=True, blank=True)
    labels_json  = models.JSONField(default=list, blank=True)  # Gmail labels

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
2. `core/services/gmail_client.py` — класс `GmailApiClient` (обёртка над `google-api-python-client`), работает с OAuth refresh_token из env
3. `core/services/email_ingest.py` — функция `sync_mailbox()` — инкрементальная подтяжка через `users.history.list` (или `messages.list` на первом запуске)
4. `core/services/email_matcher.py` — функция `match_email_to_container(email_dict) -> (Container|None, matched_by)`
5. `core/tasks_email.py` — celery task `sync_emails_from_gmail`
6. `core/migrations/0152_add_container_email.py` — миграция модели
7. `templates/admin/core/container/_emails_panel.html` — сайдбар с перепиской
8. `templates/admin/core/container/_email_detail.html` — контент одного письма (раскрываемый)
9. `core/admin/email.py` — `ContainerEmailAdmin` (отдельный список для ручной ревизии непривязанных)
10. `scripts/get_gmail_refresh_token.py` — **уже создан** — одноразовое получение refresh_token

### Правки существующих
1. `core/models.py` → импорт `from core.models_email import ContainerEmail` (или через `__init__` приложения)
2. `core/admin/__init__.py` → подключить `email.py`
3. `templates/admin/core/container/change_form.html` → подключить `{% include "admin/core/container/_emails_panel.html" %}`
4. `logist2/celery.py` → добавить в `beat_schedule`:
   ```python
   'sync-emails-from-gmail': {
       'task': 'core.tasks_email.sync_emails_from_gmail',
       'schedule': crontab(minute='*/5'),
   },
   ```
5. `env.example` → новые переменные:
   ```
   # Gmail OAuth для чтения переписки по контейнерам
   GMAIL_ENABLED=true
   GMAIL_CLIENT_ID=816142178998-xxxxx.apps.googleusercontent.com
   GMAIL_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxx
   GMAIL_REFRESH_TOKEN=1//0xxxxxxxxxxxxxxxx
   # Почтовый ящик, к которому выдан токен (для справки; API сам знает).
   GMAIL_USER_EMAIL=user@caromoto.com
   # Сколько писем тянуть при первом прогоне (когда ещё нет gmail_history_id в БД)
   GMAIL_INITIAL_LOOKBACK_DAYS=30
   # Лимит размера одного вложения (МБ). Крупнее — сохраняем только метаданные.
   GMAIL_MAX_ATTACHMENT_MB=25
   ```
6. `logist2/settings/base.py` → прочитать эти переменные.

## Алгоритм Celery-задачи `sync_emails_from_gmail`

```
1. Построить OAuth credentials из env (GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN).
   Использовать google.oauth2.credentials.Credentials(...).
2. service = build('gmail', 'v1', credentials=creds)
3. Если в БД уже есть max(gmail_history_id) — использовать инкрементальный режим:
   a. service.users().history().list(userId='me', startHistoryId=max_hid,
          historyTypes=['messageAdded']).execute()
   b. Для каждого нового messageId из history:
      - service.users().messages().get(userId='me', id=messageId,
             format='full').execute()
      - распарсить payload → headers, body, attachments
      - thread_id = message['threadId']  # уже в API
      - labels = message['labelIds']
      - direction = OUTGOING если 'SENT' в labels, иначе INCOMING
      - если gmail_id уже есть в БД — пропустить
      - вызвать match_email_to_container → (container, matched_by)
      - создать ContainerEmail (get_or_create по gmail_id)
   c. Если history expired (errors.HttpError 404) — fallback на полный re-sync
      через messages.list.
4. Если max_hid нет (первый запуск):
   a. service.users().messages().list(userId='me',
          q=f'newer_than:{GMAIL_INITIAL_LOOKBACK_DAYS}d -in:spam -in:trash',
          maxResults=500).execute()
   b. Пагинация через pageToken, пока не закончится.
   c. Для каждого id — messages.get как выше.
5. Обновить max gmail_history_id в БД (или в отдельной таблице «sync state»).
6. Логировать: processed / new / matched / unmatched / api_calls / quota_used.
```

**Парсинг `message['payload']`:**
- `payload.headers` — список `{name, value}`, достаём Message-ID, Subject, From, To, Cc, In-Reply-To, References, Date.
- `payload.body.data` — base64url-закодированное тело (если single-part).
- `payload.parts` — список вложенных частей (multipart). Рекурсивно:
  - `mimeType == 'text/plain'` → `body_text`
  - `mimeType == 'text/html'` → `body_html`
  - прочее с `filename` → вложение: `attachmentId` + `messages.attachments.get()` → base64url → файл
- `message['snippet']` — готовое превью от Google.

**Квота**: Gmail API даёт 1 млрд quota units в день. `messages.get(format=full)` = 5 units.
Даже 10 000 писем в день = 50 000 units — запас 20 000×.

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
- OAuth secrets (`GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`) только в env, не в БД/git.
- `client_secret.json` и `token.json` **никогда** не коммитятся (уже в `.gitignore`).
- Если refresh_token скомпрометирован — отозвать через https://myaccount.google.com/permissions
  и перегенерировать через `scripts/get_gmail_refresh_token.py`.
- В `body_html` чистить от вредного через `bleach` перед рендером в админке.
- Лок на задачу: celery `task_time_limit` + advisory lock в postgres, чтобы два
  воркера не тянули одновременно.
- Идемпотентность по `gmail_id` (UNIQUE) + `message_id` (UNIQUE) — защита от дублей.

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
- Кнопка «обновить» → AJAX вызывает `sync_emails_from_gmail.apply_async()` (с таймаутом ~15 сек; если долго — показать «синхронизация идёт в фоне»).
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

Всё нужное уже есть:
- `google-api-python-client>=2.100.0` ✅
- `google-auth>=2.23.0` ✅
- `google-auth-httplib2>=0.1.1` ✅
- `google-auth-oauthlib>=1.1.0` ✅ (нужно только для `scripts/get_gmail_refresh_token.py`)
- `bleach` — проверить и добавить, если нет (санитайзинг HTML)
- `python-dateutil` — проверить и добавить, если нет

## Чеклист реализации

- [ ] Создать модель `ContainerEmail` + миграция `0152`
- [ ] Регистрация в `core/admin/email.py` (отдельный list для UNMATCHED)
- [ ] Реализовать `GmailApiClient` в `core/services/gmail_client.py`
  - [ ] OAuth credentials из env (авто-refresh токена)
  - [ ] `list_history(start_history_id)` — инкрементальная подтяжка
  - [ ] `list_messages(q)` — для первого прогона
  - [ ] `get_message(id)` → распарсенный dict (headers, body_text, body_html, attachments)
  - [ ] `get_attachment(message_id, attachment_id)` → bytes
- [ ] Реализовать `sync_mailbox()` в `core/services/email_ingest.py`
- [ ] Реализовать `match_email_to_container` в `core/services/email_matcher.py`
- [ ] Celery-задача `sync_emails_from_gmail` + регистрация в `beat_schedule`
- [ ] Добавить env-переменные в `env.example` + чтение в `settings/base.py`
  - на проде добавить реальные значения в серверный `.env`
- [ ] Шаблон `_emails_panel.html` + включение в `change_form.html`
- [ ] View-эндпоинты для expand / attachment / mark-read / sync
- [ ] Юнит-тесты:
  - матчер: контейнер по номеру, по букингу, по треду, UNMATCHED
  - парсер Gmail API payload: multipart / attachments / кириллица в subject
  - идемпотентность: повторный запуск задачи не создаёт дублей
- [ ] Документация: `docs/README.md` — раздел «OAuth для Gmail: как обновить токен»
- [ ] Ручная проверка: закинуть реальные письма и посмотреть привязку

## Потенциальные грабли

1. **Gmail thread-id**: нативно доступен как `message['threadId']` — проблема решена,
   без костылей.
2. **Большие вложения**: ограничить скачиваемый размер (например, 25 МБ на вложение,
   100 МБ на письмо). Больше — только метаданные. Используем `GMAIL_MAX_ATTACHMENT_MB`.
3. **Base64url тело**: Gmail API отдаёт `payload.body.data` в base64url-кодировке.
   Использовать `base64.urlsafe_b64decode(data + '==')` с padding.
4. **Кодировки заголовков**: сами заголовки в API уже декодированы, но если
   встречается MIME-encoded (например `=?UTF-8?B?...?=`), декодировать через
   `email.header.decode_header`.
5. **Часовые пояса**: header `Date` парсим через `email.utils.parsedate_to_datetime`
   → UTC.
6. **History expiration**: `users.history.list` может вернуть 404 «historyId not
   found» если с последнего обновления прошло больше ~7 дней и данные уже удалены.
   Fallback → полный re-sync через `messages.list` с `newer_than:Nd`.
7. **Rate limiting**: Gmail API — 250 quota units per user per second. Batching
   через `service.new_batch_http_request()` — до 100 операций за раз.
8. **Refresh token ротация**: Google изредка инвалидирует refresh_token (при смене
   пароля, 6 мес. неактивности, отзыве админом). Нужен graceful error — логировать
   и слать уведомление админу «перегенерируйте refresh_token».
9. **Букинг-коллизии**: если у двух разных контейнеров одинаковый `booking_number`
   (например, консолидация груза) — письмо привяжется к первому найденному. Нужно
   логировать предупреждение и/или завести M2M-таблицу в будущем.
10. **Internal OAuth и новые пользователи**: поскольку consent screen = Internal,
    только учётки домена `caromoto.com` могут получать токен. Это фича, не баг.

## Следующие фазы (вне текущего плана)

- **Phase 2**: отправка/ответы через Gmail API (`users.messages.send`),
  In-Reply-To/References — склеивание с существующим тредом, аттачи.
- **Phase 3**: Push-уведомления через `users.watch` + Pub/Sub — real-time
  вместо polling каждые 5 минут. Отметка «прочитано» синхронизируется с Gmail.
- **Phase 4**: AI-саммари длинных тредов, категоризация (линия / клиент / таможня),
  автоизвлечение ETA из писем линии → обновление `Container.eta`.
