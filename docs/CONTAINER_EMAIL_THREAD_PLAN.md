# План: переписка в карточке контейнера (Gmail API + OAuth 2.0)

Цель: в карточке каждого контейнера видеть всю почтовую переписку, относящуюся к
нему, и **отвечать на письма прямо из карточки**. Фаза 1 (чтение + матчинг)
закрыта. Фаза 2 (отправка/ответы) — текущая задача.

---

## Статус

| Фаза    | Состояние        | Коротко                                                       |
| ------- | ---------------- | ------------------------------------------------------------- |
| Phase 1 | ✅ В проде        | Чтение Gmail, матчинг, messenger-UI, счётчики, ре-матчинг     |
| Phase 2 | ⏳ **В работе**   | Ответы/новые письма из карточки контейнера через Gmail API    |
| Phase 3 | 🔜 Отложено      | Pub/Sub push-уведомления вместо polling                        |
| Phase 4 | 🔜 Отложено      | AI-саммари тредов, авто-извлечение ETA, категоризация          |

### Согласованные решения

| Параметр           | Значение                                                        |
| ------------------ | --------------------------------------------------------------- |
| Провайдер          | Google Workspace (`@caromoto.com`)                              |
| Аутентификация     | **OAuth 2.0 via Gmail API** (App Password заблокирован админом) |
| Источник писем     | Gmail INBOX + Sent через единый API                             |
| Синхронизация      | Celery Beat каждые 5 минут + ручной триггер ↻ из UI             |
| Критерии матчинга  | Номер контейнера (ISO 6346) / букинг / thread_id родителя       |
| UI                 | Messenger-стиль (чат-бабблы), сворачиваемый `<details>`         |

---

## Phase 1 — ✅ Реализовано и в проде

### Модели
- **`core/models_email.py`**:
  - `ContainerEmail` — все письма с привязкой к `Container` (FK nullable).
    Поля: `message_id`, `thread_id`, `in_reply_to`, `references`, `direction`
    (INCOMING/OUTGOING), `from_addr`, `to_addrs`, `cc_addrs`, `subject`,
    `body_text`, `body_html`, `snippet`, `received_at`, `gmail_id`,
    `gmail_history_id`, `labels_json`, `attachments_json`, `matched_by`,
    `is_read`.
  - `GmailSyncState` — один singleton-ряд с `last_history_id`, `last_synced_at`,
    счётчиками processed/new/matched/errors.
- **Миграция**: `core/migrations/0152_add_container_email.py` (применена).

### Сервисы
- **`core/services/gmail_client.py`** — `GmailApiClient`:
  - Credentials строятся из env `GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN` с
    автоматическим refresh через `google.auth.transport.requests`.
  - Методы: `list_history(start_history_id)`, `list_messages(query, max_results)`,
    `get_message(gmail_id, format='full')`, `get_attachment(message_id, attachment_id)`.
  - `_detect_inline(part)` помечает inline-картинки (Content-ID, Content-Disposition=inline)
    → их байты не скачиваются, сохраняются только метаданные.
- **`core/services/email_matcher.py`** — `match_email_to_container(parsed) -> (Container|None, matched_by)`:
  1. `THREAD` — такой же `thread_id` уже привязан к контейнеру.
  2. `IN_REPLY_TO` родитель уже привязан.
  3. `CONTAINER_NUMBER` — regex `\b[A-Z]{4}\d{7}\b` по subject+body (ISO 6346).
  4. `BOOKING_NUMBER` — substring по dict `{booking_lower: container_id}`.
  5. Ничего → `UNMATCHED`, `container=None`.
- **`core/services/email_ingest.py`** — `sync_mailbox()`:
  - Инкрементальная подтяжка через `users.history.list`; fallback на полный
    re-sync через `messages.list(q='newer_than:30d -in:spam -in:trash')` если
    `historyId` уже протух (404).
  - Идемпотентность по `gmail_id` UNIQUE.
  - Attachments сохраняются в `MEDIA_ROOT/container_emails/<yyyy>/<mm>/<uid>/`,
    inline — skip (только метаданные в `attachments_json`).
- **`core/services/email_reply_parser.py`** — парсер и очистка тел:
  - `split_reply_and_quote(text)` — разделяет свежую реплику и цитату.
  - `clean_message_body(text)` — убирает подписи (`Kind regards`, `Pagarbiai/Best Regards`),
    Salesforce-мусор (`UserContext.initialize`, `window.onload` stubs),
    CSS-блоки, пустые скобки, трекинг-URL (`Label ( https://u28369205.ct.sendgrid.net/... )`).
  - `_fix_mojibake` — `ftfy.fix_text` + кастомная `_LT_MOJIBAKE_MAP` для литовских
    кракозябр (`SiunÄ¨iu → Siunčiu`).
  - `html_to_plain(html)` — примитивная конвертация HTML → plain (для писем,
    у которых пусто `body_text`, но заполнен `body_html`).
  - `messenger_body_from_email(text, html)` — выбирает источник и прогоняет полный
    пайплайн очистки.

### Задачи и расписание
- **`core/tasks_email.py`** — `sync_emails_from_gmail` Celery task.
- **`logist2/celery.py`**:
  - `beat_schedule['sync-emails-from-gmail'] = crontab(minute='*/5')`
  - `app.autodiscover_tasks(related_name='tasks_email')` (иначе воркер
    не видит задачу — обычный `autodiscover_tasks()` ищет только `tasks.py`).

### Админ
- **`core/admin/email.py`**:
  - `ContainerEmailAdmin` — список с фильтром `matched_by`, action «привязать
    к контейнеру», autocomplete по номеру контейнера.
  - `GmailSyncStateAdmin` — статус синхронизации.
- **`core/admin/container.py`**:
  - `number_with_unread()` — колонка с бейджем непрочитанных рядом с номером
    (красный badge c числом / зелёный "0").
  - `HasUnreadEmailsFilter` — фильтр в sidebar change_list.

### UI в карточке контейнера
- **`templates/admin/core/container/_emails_panel.html`** — сворачиваемый
  fieldset через нативный `<details>/<summary>`:
  - Чат-бабблы: входящие слева, исходящие справа.
  - Аватары с цветом по hash от email-а, инициалы из display-name.
  - Бейджи типа матчинга (CONTAINER_NUMBER, BOOKING, THREAD, MANUAL, UNMATCHED).
  - `<details>` внутри баббла прячет цитату переписки.
  - Вложения: клики по `attachment_json` с фильтром inline-картинок.
  - Кнопка ↻ «Синхронизировать» (AJAX → `sync_emails_from_gmail.apply_async`).
  - При первом раскрытии панели — автоматический `mark-all-read` через fetch.
  - JS перемещает панель после последнего inline («Автомобили»).
- **`templates/admin/core/container/photos_gallery.html`** — унифицирован в тот
  же паттерн `<details>/<summary>`.
- **Глобальные стили `core/static/css/dashboard_admin.css`**:
  - `.cm-form-main .inline-group` — «карточку» перенесена с wrapper'а на
    `fieldset.module` (транспарентный wrapper, никаких двойных подложек).
  - `fieldset.collapse:has(> details:not([open]))` — свёрнутое состояние обнуляет
    нижний padding → секция схлопывается до одной полосы.
  - Усилены тени `--cm-shadow-card` (эффект парения над фоном).
  - Все секции (Основные данные / Автомобили / Фотографии / Переписка) теперь
    визуально идентичны.

### View-эндпоинты (`core/views/emails.py` + `core/urls.py`)
```
GET  /core/emails/<email_id>/                    — детальный partial для expand
GET  /core/emails/<email_id>/attachment/<i>/     — скачивание вложения (staff only)
POST /core/emails/<email_id>/mark-read/          — одиночная отметка прочитанным
POST /core/emails/container/<id>/mark-all-read/  — bulk отметка
POST /core/emails/sync/                          — ручной триггер Celery-задачи
POST /core/emails/<email_id>/attach-to-container/ — привязка UNMATCHED письма
```

### Template filters (`core/templatetags/email_extras.py`)
- `display_name` / `initials` / `avatar_color` — для аватарок.
- `fix_mojibake` — исправление кодировок.
- `visible_attachments` — фильтр inline-картинок.
- `quote_part` — выдёргивает цитату из `body_text`.
- `linkify_urls` — превращает URL в `<a>`.
- `messenger_body_auto` — универсальная очистка тела (plain или html).

### Management commands
- **`core/management/commands/rematch_container_emails.py`** — один раз пройтись
  по UNMATCHED письмам и привязать к контейнерам (активные `FLOATING`, `IN_PORT`,
  `UNLOADED`). Опции: `--dry-run`, `--include-matched`.

### Прочее
- Зависимости в `requirements.txt`: `bleach`, `python-dateutil`, `ftfy`,
  `google-api-python-client`, `google-auth*`.
- `env.example` и `logist2/settings/base.py` — `GMAIL_*` переменные.
- `scripts/deploy.ps1` перезапускает `gunicorn + daphne + celery + celerybeat`.

---

## Phase 2 — ⏳ Ответы из карточки контейнера

### Цель

В карточке контейнера пользователь может:
1. **Ответить** на любое входящее письмо тредa → Gmail API разошлёт ответ
   от `@caromoto.com` с правильным `In-Reply-To` + `References`, чтобы Gmail
   сохранил его в том же треде, что и оригинал.
2. **Написать новое письмо** по контейнеру (без родительского письма, но с
   автозаполнением темы и упоминанием номера/букинга).
3. Прикрепить файлы (PDF, фото).
4. Сразу увидеть отправленное сообщение в чат-ленте как `OUTGOING`-баббл
   справа.

### 2.0. Новые зависимости / конфиг

#### Gmail OAuth scope

В Phase 1 мы используем scope `https://www.googleapis.com/auth/gmail.readonly`.
Для отправки нужен **`gmail.send`** (или более широкий `gmail.modify`).

**Действие при старте Phase 2:**
1. В `scripts/get_gmail_refresh_token.py` заменить scope на:
   ```python
   SCOPES = [
       'https://www.googleapis.com/auth/gmail.readonly',  # чтение
       'https://www.googleapis.com/auth/gmail.send',      # отправка
       # (опционально) gmail.modify — для mark-as-read синка с Gmail
   ]
   ```
2. Перегенерировать `refresh_token` через скрипт (в браузере подтвердить новые
   scope-ы).
3. Обновить `GMAIL_REFRESH_TOKEN` в `.env` локально и на сервере.
4. Проверить: `creds.has_scopes([...])` перед `messages.send`.

#### Env-переменные (добавить в `env.example` и `settings/base.py`)
```
# Подпись добавляется в конец исходящих писем (plain text + html).
GMAIL_SIGNATURE_TEXT="— Caromoto Lithuania"
GMAIL_SIGNATURE_HTML="<p>— Caromoto Lithuania</p>"
# Максимальный суммарный размер вложений исходящего письма, МБ.
GMAIL_MAX_OUTBOUND_MB=25
# Имя/email отправителя (если захотим отправлять от имени конкретного человека
# через alias в Google Workspace — alias должен быть настроен в Gmail UI).
GMAIL_FROM_NAME="Caromoto Lithuania"
GMAIL_FROM_EMAIL=""   # пусто = use authenticated account
```

### 2.1. Бэкенд

#### Новый сервис `core/services/gmail_sender.py`

```python
def build_mime_message(
    *,
    from_addr: str,
    from_name: str,
    to: list[str],
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body_text: str,
    body_html: str | None,
    in_reply_to: str | None,        # Message-ID родителя (без < >)
    references: str | None,         # пробел-разделённый список Message-ID предков
    attachments: list[tuple[str, bytes, str]] | None,  # (filename, data, mime)
) -> EmailMessage:
    """Формирует валидное RFC 5322 сообщение с правильными headers для треда."""


def send_message(
    *,
    gmail_client: GmailApiClient,
    mime_msg: EmailMessage,
    thread_id: str | None,          # Gmail threadId, если ответ в существующий
) -> dict:
    """
    Gmail API users.messages.send(userId='me', body={'raw': ..., 'threadId': ...}).
    Возвращает полный dict отправленного message (чтобы сразу создать
    ContainerEmail с правильным gmail_id и thread_id).
    """
```

**Ключевые детали:**
- `raw` = `base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()`.
- Если указан `threadId` — Gmail принудительно положит сообщение в тот же тред
  (важно: Subject должен начинаться с `Re:` чтобы не ломать UI Gmail'а).
- Headers `In-Reply-To` / `References` строим из родительского письма:
  - `In-Reply-To: <parent.message_id>`
  - `References: parent.references + " " + parent.message_id` (с обрамлением `<...>`).

#### Сервис `core/services/email_compose.py`

Высокоуровневая обёртка, принимающая **pk** контейнера и (опционально) pk
родительского `ContainerEmail`, делающая всё — сбор заголовков, отправка,
создание локальной записи:

```python
def reply_to_email(
    *,
    parent_email: ContainerEmail,   # то, на что отвечаем
    user: User,                     # кто нажал «Отправить» в админке
    to: list[str],
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body_text: str,
    attachments: list[UploadedFile] | None,
) -> ContainerEmail:
    """
    1. Build MIME (signature подмешиваем в конец body_text / body_html).
    2. Call send_message(thread_id=parent.thread_id).
    3. Сохранить вложения в MEDIA_ROOT/container_emails/...
    4. Создать ContainerEmail(
           container=parent.container,
           direction='OUTGOING',
           matched_by='THREAD',
           gmail_id=response['id'],
           thread_id=response['threadId'],
           in_reply_to=parent.message_id,
           references=...,
           subject=subject,
           from_addr=GMAIL_FROM_EMAIL or creds.email,
           to_addrs=..., cc_addrs=...,
           body_text=body_text,
           body_html=plain_to_simple_html(body_text),  # или WYSIWYG
           attachments_json=[...],
           received_at=timezone.now(),
           is_read=True,
           sent_by_user=user,
       )
    5. Вернуть созданный ContainerEmail.
    """


def compose_new_email(
    *,
    container: Container,
    user: User,
    to: list[str],
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body_text: str,
    attachments: list[UploadedFile] | None,
) -> ContainerEmail:
    """Новое письмо вне существующего треда. thread_id=None → Gmail создаст новый."""
```

#### Мини-миграция модели
Добавить поля в `ContainerEmail` (новая миграция `0153_add_container_email_sender.py`):
```python
sent_by_user = models.ForeignKey(
    'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
    related_name='sent_container_emails',
    help_text='Кто отправил письмо из админки (только для OUTGOING).',
)
# Статус отправки для UI (pending/sent/failed, чтобы видеть ошибки)
send_status = models.CharField(
    max_length=10,
    choices=[('SENT', 'Отправлено'), ('FAILED', 'Ошибка'), ('PENDING', 'В очереди')],
    blank=True, default='',
)
send_error = models.TextField(blank=True, default='')
```

#### View-эндпоинты (расширить `core/views/emails.py`)

```
POST /core/emails/<parent_email_id>/reply/
  form/multipart:
    to         — обязательно, csv
    cc         — optional
    bcc        — optional
    subject    — default: "Re: " + parent.subject
    body_text  — обязательно
    attachments[] — optional files
  200: { ok: true, email_id: <new>, html: <rendered bubble> }
  4xx: { ok: false, error: "..." }

POST /core/emails/compose/
  form/multipart:
    container_id — обязательно
    to, cc, bcc, subject, body_text, attachments[]
  200: { ok: true, email_id, html }
```

- **Permission**: только `request.user.is_staff`.
- **Лимит размера**: суммарный `sum(file.size)` ≤ `GMAIL_MAX_OUTBOUND_MB * 1024 * 1024`.
  Иначе 413 с понятным сообщением.
- **Idempotency**: если Gmail API упал на середине — `send_status='FAILED'`,
  в UI показать кнопку «Повторить». Храним черновик локально, не отправляем
  дважды.
- **Quota**: `messages.send` = 100 quota units. Запас огромный.

### 2.2. UI в карточке контейнера

#### Изменения в `_emails_panel.html`

1. **Кнопка «Написать»** в actions-bar (рядом с «↻ Синхронизировать»):
   ```html
   <button id="cm-email-compose-btn">✍ Написать</button>
   ```
   Открывает composer для нового письма (не в треде).

2. **Hover-action «Ответить»** у каждого входящего баббла:
   ```html
   <div class="cm-bubble-actions">
     <button class="cm-reply-btn" data-email-id="{{ email.pk }}">Ответить</button>
   </div>
   ```
   - Появляется по hover на баббл (opacity 0 → 1).
   - Клик → открывает composer с автозаполнением:
     - To = parent.from_addr
     - Cc = parent.to_addrs без наших адресов
     - Subject = `Re: ${parent.subject without leading Re:}`
     - Body = `> ${parent.body_text linewise prefixed}` под пустой строкой + signature
     - thread_id = parent.thread_id (скрытое поле)

3. **Composer** (модалка или inline-expand под последним сообщением):
   ```html
   <div class="cm-composer" data-mode="reply|compose" data-parent-id="...">
     <div class="cm-composer-header">
       <h3>Ответ на: <em>Re: …</em></h3>
       <button class="cm-composer-close">×</button>
     </div>
     <div class="cm-composer-fields">
       <label>Кому</label>
       <input name="to" type="text" required>
       <details><summary>Cc / Bcc</summary>
         <label>Cc</label><input name="cc">
         <label>Bcc</label><input name="bcc">
       </details>
       <label>Тема</label><input name="subject" required>
       <label>Сообщение</label>
       <textarea name="body_text" rows="8" required></textarea>
       <div class="cm-composer-attachments">
         <input type="file" multiple>
         <div class="cm-attached-list"></div>
       </div>
     </div>
     <div class="cm-composer-footer">
       <span class="cm-composer-status"></span>
       <button class="cm-composer-send">Отправить</button>
       <button class="cm-composer-cancel">Отмена</button>
     </div>
   </div>
   ```

4. **После отправки**:
   - fetch `POST /core/emails/<parent_id>/reply/` multipart.
   - Ответ `{ ok, html }` → парсим `html` (уже отрендеренный шаблон баббла) и
     вставляем в `#cm-emails-list` **сверху** (у нас новые сверху).
   - Или проще: `location.reload()` после успеха.
   - Composer закрывается, форма очищается.

#### Клавиши/UX
- `Ctrl+Enter` в textarea → отправка.
- `Esc` → закрыть composer с подтверждением если есть черновик.
- Черновик в `localStorage` (ключ = `draft:email:<parent_id>` или `draft:compose:<container_id>`).
  Восстанавливается при повторном открытии composer'а.

### 2.3. Цитирование и подпись

#### Автоцитата (`format_quoted_reply` в `email_reply_parser.py`)
```python
def format_quoted_reply(parent: ContainerEmail) -> str:
    """
    Формирует шапку цитаты для ответа в Gmail-стиле:
      \n\n\nOn 17 Apr 2026, 14:23, ivan@example.com wrote:\n
      > первая строка\n
      > вторая строка\n
    """
    hdr = f"\n\n\nOn {parent.received_at:%d %b %Y, %H:%M}, " \
          f"{parent.from_addr} wrote:\n"
    quoted = '\n'.join('> ' + ln for ln in (parent.body_text or '').splitlines())
    return hdr + quoted
```

#### Подпись
- Читать из `settings.GMAIL_SIGNATURE_TEXT` и `_HTML`.
- Добавлять в конец `body_text` перед отправкой (но **не** показывать в composer'е
  дважды — показывать как placeholder «Ваша подпись будет добавлена автоматически»).

### 2.4. Чеклист Phase 2

- [ ] **0. OAuth scope upgrade**
  - [ ] Обновить `SCOPES` в `scripts/get_gmail_refresh_token.py`
  - [ ] Перегенерировать `refresh_token`
  - [ ] Обновить `.env` локально и на сервере
  - [ ] Проверить `creds.has_scopes([...])` в `GmailApiClient`
- [ ] **1. Модель**
  - [ ] Добавить поля `sent_by_user`, `send_status`, `send_error`
  - [ ] Миграция `0153_add_container_email_sender.py`
- [ ] **2. Сервисы**
  - [ ] `core/services/gmail_sender.py` — `build_mime_message`, `send_message`
  - [ ] `core/services/email_compose.py` — `reply_to_email`, `compose_new_email`
  - [ ] `format_quoted_reply` в `email_reply_parser.py`
  - [ ] Санитайзинг входящего body_text (XSS, стрип control chars)
- [ ] **3. View-эндпоинты**
  - [ ] `POST /core/emails/<id>/reply/`
  - [ ] `POST /core/emails/compose/`
  - [ ] URL-паттерны в `core/urls.py`
  - [ ] Permission check (`is_staff`)
  - [ ] Limit на размер вложений
- [ ] **4. Settings / env**
  - [ ] `GMAIL_SIGNATURE_TEXT` / `_HTML`
  - [ ] `GMAIL_MAX_OUTBOUND_MB`
  - [ ] `GMAIL_FROM_NAME`
  - [ ] `env.example` + прочитать в `settings/base.py`
- [ ] **5. UI**
  - [ ] Кнопка «✍ Написать» в actions-bar
  - [ ] Hover-кнопка «Ответить» у каждого входящего баббла
  - [ ] Composer (inline под последним сообщением или fixed-bottom)
  - [ ] Автозаполнение полей (To / Cc / Subject / цитата)
  - [ ] Drag-n-drop вложений
  - [ ] Индикатор статуса отправки («Отправляется…», «✓ Отправлено», «✗ Ошибка»)
  - [ ] Черновик в localStorage
- [ ] **6. Тесты**
  - [ ] `build_mime_message` — правильные headers для треда (In-Reply-To, References)
  - [ ] `send_message` — с mocked Gmail API, проверка `raw` + `threadId`
  - [ ] `reply_to_email` — создаётся локальная запись с `direction=OUTGOING`,
    `matched_by=THREAD`, `sent_by_user=user`
  - [ ] Проверка лимита размера вложений
  - [ ] E2E (Selenium или Playwright): открыть карточку → ответить → увидеть
    баббл справа
- [ ] **7. Документация**
  - [ ] В `docs/README.md` раздел «Phase 2: обновление scope и тестирование»
  - [ ] Комментарии в коде по threading headers

### 2.5. Потенциальные грабли Phase 2

1. **Subject kebab при ответе**: Gmail UI шьёт тред по `References` + по Subject
   (последний совпадает с нормализацией `Re:` / `Fwd:`). Если Subject не начинается
   с `Re:` — Gmail может не склеить. **Решение**: в `reply_to_email` принудительно
   добавлять `Re: ` если нет.

2. **Encoding**: русские буквы в Subject требуют кодирования `=?UTF-8?B?...?=`
   (RFC 2047). Использовать `email.header.Header('text', 'utf-8').encode()`.
   Python stdlib `EmailMessage` делает это автоматически если объект
   инициализирован правильно.

3. **Large attachments**: Gmail API `messages.send` работает до 35 МБ raw
   (≈ 25 МБ после base64 overhead). Больше — нужен `messages.import` или
   Drive-вложение (phase 2.1).

4. **Thread mismatch**: если указать `threadId` но `References` не содержит ни
   одного Message-ID из треда — Gmail откажет с «Invalid thread». Всегда
   подтягивать `References` родителя + его `Message-ID`.

5. **From alias**: если `GMAIL_FROM_EMAIL != authenticated email`, и alias не
   настроен в Gmail Settings → Accounts → Send mail as — Gmail перепишет From
   на authenticated адрес и добавит `Sender:` header.

6. **Черновики Gmail**: если хотим хранить черновики в Gmail Drafts (не только
   localStorage), это phase 2.1 — отдельная сложность.

7. **Race condition**: если юзер быстро дважды жмёт «Отправить» — задабливать
   кнопку сразу после клика + idempotency token в form. Или сохранить
   ContainerEmail с `send_status='PENDING'` и блокировать повтор.

8. **Mark as read in Gmail**: при отметке прочитанным в админке можно дополнительно
   через `users.messages.modify(id, removeLabelIds=['UNREAD'])` синкануть с
   Gmail. Требует scope `gmail.modify`. Опционально, phase 2.1.

### 2.6. Что **не** делаем в Phase 2

- Rich-text WYSIWYG редактор — пока plain textarea с сигнатурой и цитатой.
- Inline-картинки в теле (drag-n-drop в textarea). Только вложения.
- Email templates / canned responses.
- Mail merge / массовые рассылки.
- Delegated send / отправка от имени коллеги.
- Push-уведомления о новых письмах (это phase 3).

---

## Phase 3 — 🔜 Push-уведомления (в будущем)

- `users.watch` + Google Pub/Sub → real-time вместо polling каждые 5 мин.
- Когда приходит новое письмо — Pub/Sub дергает наш webhook → сразу `messages.get`
  + показываем уведомление в UI через Django Channels.
- Двусторонняя синхронизация mark-as-read (читаем в админке → убираем label
  UNREAD в Gmail; читаем в Gmail → убираем бейдж в админке).

## Phase 4 — 🔜 AI и автоматизация (в будущем)

- Саммари длинных тредов через OpenAI API.
- Категоризация писем: линия / клиент / таможня / склад / финансы.
- Автоизвлечение ETA из писем линии → обновление `Container.eta`.
- Автоизвлечение booking/VIN из новых писем → привязка к контейнеру без regex.

---

## Как продолжить в новом чате

Стартовый промпт:
> Продолжаем Phase 2 плана в `docs/CONTAINER_EMAIL_THREAD_PLAN.md`. Начни с
> пункта 2.4.0 (OAuth scope upgrade) и дальше по чеклисту. Работай
> последовательно: сперва сервер (модель → сервис → view), потом UI.

Главные файлы для ориентации:
- `core/services/gmail_client.py` — уже есть, в неё добавим send.
- `core/services/email_reply_parser.py` — туда `format_quoted_reply`.
- `core/views/emails.py` — туда `reply_email`, `compose_email`.
- `templates/admin/core/container/_emails_panel.html` — туда composer UI.
- `core/models_email.py` — туда `sent_by_user`, `send_status`, `send_error`.
