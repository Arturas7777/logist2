# Публичные эндпоинты — модель угроз и защита

Документ описывает все эндпоинты, доступные без аутентификации, их назначение
и текущий уровень защиты. Часть мер (signed URL для фото) уже реализована
в рамках задачи H5a из `docs/ROADMAP_2026-05_high_medium.md`, остальные
зафиксированы как TODO в конце.

Поддерживается актуальным при каждом изменении публичной поверхности.

---

## 1. Полный список открытых эндпоинтов

Источник: `core/urls_website.py` + `core/views_website.py`.

| URL | Метод | View | Permission | Throttle | Защита |
| --- | --- | --- | --- | --- | --- |
| `/` | GET | `website_home` | публично | — | `cache_page(15m)` |
| `/about/`, `/services/`, `/contact/` | GET | static views | публично | — | `cache_page(1h)` |
| `/news/`, `/news/<slug>/` | GET | `news_list`, `news_detail` | публично | — | `cache_page(15m)` (для списка) |
| `/api/news/`, `/api/news/<slug>/` | GET | `NewsViewSet` | `AllowAny` | — | publish-only filter |
| `/api/contact/` | POST | `ContactMessageViewSet` | `AllowAny` | — | **TODO: CAPTCHA** |
| `/api/track/` | POST | `track_shipment` | `AllowAny` | `TrackShipmentThrottle` (20/min) | минимальная длина 8 символов, точное совпадение VIN/контейнер. **TODO: CAPTCHA** |
| `/api/ai-chat/` | POST | `ai_chat` | `AllowAny` | `AIChatThrottle` | SessionAuthentication опционально, лимит на IP |
| `/api/container-photos/<num>/` | GET | `get_container_photos` | `AllowAny` | `PhotoDownloadThrottle` (30/min) | **signed URL'ы в ответе (H5a)**, кэш 15 мин |
| `/api/download-photos-archive/` | POST | `download_photos_archive` | `AllowAny` | `PhotoDownloadThrottle` (30/min) | **обязателен `container_token` (H5a)**, фильтрация `photo_ids` по контейнеру |
| `/photo/s/<token>/` | GET | `serve_signed_photo` | `AllowAny` | — (см. ниже) | **TimestampSigner-подпись, TTL=1ч (H5a)**, only `is_public=True` |

Закрытые (`@login_required` / `IsAuthenticated` / `IsClientUser`) эндпоинты
здесь не перечисляются — их security-модель — стандартная аутентификация
Django/DRF.

---

## 2. Модель угроз

### 2.1 Активы

| Актив | Чувствительность |
| --- | --- |
| Фото контейнеров / автомобилей клиентов | **Высокая**. Могут содержать VIN, госномера, состояние авто. Это PII клиентов и коммерческая информация. |
| Статус контейнера / автомобиля (tracking) | **Средняя**. Конкуренту даёт возможность мониторить логистическую цепочку и сроки. |
| Контактная форма / форма заявки | **Низкая**, но спам приведёт к замусориванию БД и расходу на ручную обработку. |
| AI-chat (OpenAI) | **Средняя**. Стоит реальных денег за каждый запрос. |

### 2.2 Атакующие и сценарии

| Атакующий | Сценарий | Текущая защита | Достаточно? |
| --- | --- | --- | --- |
| Бот-парсер конкурента | Массово скачивает фото контейнеров через `get_container_photos` + `download_photos_archive`. | `PhotoDownloadThrottle` (30/min) + **signed URL с TTL=1ч (H5a)** + `container_token` на ZIP. | **Частично.** Прямой `/media/photos/...` всё ещё открыт через nginx — кто видел ссылку раньше, мог сохранить путь. Нужно закрыть nginx (см. §4). |
| Спам-бот | Шлёт POST в `/api/contact/` и `/api/track/` через open-proxy. | Throttle на tracking, ничего на contact. | **Нет.** Нужна CAPTCHA. |
| Script-kiddie | Подбирает `photo_id` через `download_photos_archive`. | До H5a: можно было собрать любые `is_public=True` фото. **После H5a:** `container_token` обязателен, `photo_ids` фильтруются по контейнеру. | **Да.** Эта дыра закрыта. |
| Hotlinker | Ссылается на наши `/media/photos/...` с своего сайта. | Никакой. | **Нет.** Нужны заголовки `Referrer-Policy`, `Cross-Origin-Resource-Policy`, `X-Content-Type-Options`. |
| DoS | Заваливает `download_photos_archive` тяжёлыми архивами. | Throttle 30/min + ZIP в памяти ограничен размером выборки. | Достаточно для текущей нагрузки. Будущий рост → стримить ZIP. |

---

## 3. Что реализовано в H5a

### 3.1 Signed URL для фото

- **Утилиты:** `core/services/signed_urls.py` — обёртка над `django.core.signing.TimestampSigner` (HMAC по `SECRET_KEY`).
- **Endpoint:** `GET /photo/s/<token>/` (`serve_signed_photo`) — единственный точка выдачи фото для публики.
- **Сериализация:** `get_container_photos` возвращает `url` и `thumbnail_url` уже как `/photo/s/<token>/`, никаких прямых `/media/...`.
- **TTL:** 1 час (`DEFAULT_TTL_SECONDS`); можно переопределить через `settings.PHOTO_URL_TTL`.
- **Идентификатор:** `make_photo_token(kind, photo_id, variant)` — токен привязан к конкретной фотографии и варианту (`full` или `thumb`).
- **Эффект:** даже зная `photo_id`, скачать файл нельзя без свежей подписи. Старые ссылки протухают за 1 час.

### 3.2 `container_token` для `download_photos_archive`

- **Утилита:** `make_container_token(container_number)`.
- **Контракт:** `download_photos_archive` теперь требует поле `container_token` в JSON-теле запроса.
- **Проверки:**
  - подпись валидна → расшифровка даёт `container_number`;
  - все `photo_ids` фильтруются по `container__number == container_number`;
  - если фронт не передал токен, либо токен битый — `400` / `403` / `410` (для просроченного).
- **Эффект:** клиент должен сперва открыть `get_container_photos` для нужного контейнера. Сторонний скрипт, не имеющий токена, отлуплен на этапе валидации до того, как запустится ORM-запрос и сборка ZIP.

### 3.3 Почему `serve_signed_photo` без throttle

Изначально я навесил `PhotoDownloadThrottle` (30/min) и на отдачу
файла, но галерея с 200+ фото на lazy-load выкачивает превью пачками и
быстро ловит 429. Раньше `/media/...` отдавал nginx **без всяких
лимитов**, и галерея работала.

Парсинг ограничен не на стадии отдачи, а на стадии **выдачи
подписей**:

- `get_container_photos` под `PhotoDownloadThrottle` (30/min) → не
  больше 30 уникальных контейнеров в минуту;
- каждая подпись живёт всего час, после чего возобновляется через тот
  же rate-limited endpoint;
- без знания номера контейнера или утечки signed URL получить файл
  невозможно.

Это та же модель, что у S3 pre-signed URL'ов — выдача под лимитом,
сама отдача нет.

### 3.4 Логирование

- Каждый успешный `serve_signed_photo` → `logger.info(...)` с `kind`, `photo_id`, `variant`, `parent_id`, `REMOTE_ADDR`.
- Каждый успешный `download_photos_archive` → `logger.info(...)` с `container_number`, `photo_ids`, `REMOTE_ADDR`, `size`.
- Каждый отказ (битая/просроченная подпись) → `logger.warning(...)` с `REMOTE_ADDR` — Sentry увидит аномалии.
- Sentry дополнительно подхватывает `logger.exception(...)` из `download_photos_archive` и `get_container_photos` при внутренних ошибках.

---

## 4. TODO — следующие шаги

### 4.1 Закрыть прямой `/media/photos/` в nginx [HIGH]

> Сейчас signed URL'ы выдаются клиентам, но nginx по-прежнему отдаёт
> `/media/photos/...` напрямую. Без этого шага защита частичная: кто-то,
> сохранивший прямую ссылку до H5a, продолжит скачивать.

Минимальный конфиг:

```nginx
location /media/photos/ {
    internal;  # доступно только через X-Accel-Redirect
}
```

И в `serve_signed_photo` заменить `FileResponse(...)` на:

```python
response = HttpResponse()
response['X-Accel-Redirect'] = '/internal/media/' + photo.photo.name
response['Content-Type'] = ''  # nginx выставит сам
del response['Content-Length']
return response
```

Альтернатива — `location /media/photos/ { deny all; }` и отдавать целиком
через Django (медленнее, но без `X-Accel-Redirect`-настройки).

### 4.2 CAPTCHA для tracking и contact-формы [MEDIUM]

- Использовать **hCaptcha** (free tier 1M req/month).
- Зависимость: `django-hcaptcha` либо самописная server-side verify через `https://hcaptcha.com/siteverify`.
- ENV: `HCAPTCHA_SITE_KEY` (public), `HCAPTCHA_SECRET` (private).
- Шаблоны: добавить `<div class="h-captcha" data-sitekey="...">` в формы.
- Сервер: проверять `h-captcha-response` в `track_shipment` и `ContactMessageViewSet.create()`.

### 4.3 CSP / Referrer / CORP для фото [MEDIUM]

В `logist2/settings/prod.py` (или middleware) выставить для всех ответов с
`/photo/s/...`:

```python
response['Referrer-Policy'] = 'same-origin'
response['Cross-Origin-Resource-Policy'] = 'same-origin'
response['X-Content-Type-Options'] = 'nosniff'
```

И добавить CSP-заголовок на страницах с фото:

```
Content-Security-Policy: img-src 'self' data:; frame-ancestors 'none';
```

### 4.4 Off-site backup для логов аудита [LOW]

В `docs/BACKUPS.md` уже есть TODO про off-site бэкап БД. Логи
`download_photos_archive` живут в systemd journal — стоит дублировать в
Sentry breadcrumbs или ELK для удобного поиска по IP.

### 4.5 Версионирование подписей [LOW]

В `core/services/signed_urls.py` соли уже содержат `.v1` — это позволит
безболезненно инвалидировать все выданные токены, изменив на `.v2`
(например, при подозрении на утечку `SECRET_KEY`). Документация на этот
сценарий нужна, но миграционных скриптов не требуется — старые токены
просто перестанут валидироваться.

---

## 5. Тесты

`core/tests/test_signed_photos.py` покрывает:

- Round-trip `make_photo_token` / `parse_photo_token` (включая
  невалидные `kind` и `variant`).
- Round-trip `make_container_token` / `parse_container_token`.
- `BadSignature` при подделке, `SignatureExpired` при истечении TTL
  (через `unittest.mock.patch('django.core.signing.time.time', ...)`).
- View `serve_signed_photo`: 200, 403, 410, 404 (включая
  `is_public=False`).
- `download_photos_archive`:
  - 400 без `container_token`;
  - 200 с валидным токеном (возвращает ZIP);
  - 403 с битым токеном;
  - 404, если `photo_ids` не принадлежат подписанному контейнеру.

Запуск:

```bash
pytest core/tests/test_signed_photos.py -v
```
