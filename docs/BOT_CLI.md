# Bot CLI — Справочник команд

> Интерфейс командной строки для автоматизации операций через bot-токены.
> Каждая команда возвращает JSON со schema_version, принципалом, ролью и результатом.

## Аутентификация

```bash
python manage.py bot <command> --token %BOT_TOKEN%
```

Токены выдаются с привязкой к роли (`chief`, `accountant`, `neto`, `handover`, `parser`).

## Информация о машинах и документах

### `car` — Детали автомобиля

Возвращает информацию об автомобиле: VIN, марка/модель, клиент, контейнер, статус, склад, даты.

**Параметры:**
- `--vin <VIN>` — поиск по VIN (рекомендуется)
- `--car-id <id>` — поиск по ID машины

**Роли:** `chief`, `neto`, `handover`, `accountant`

**Пример:**
```bash
python manage.py bot car --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232
```

**Результат:**
```json
{
  "schema_version": 1,
  "ok": true,
  "result": {
    "vin": "3N1CP5BVXRL481232",
    "id": 42,
    "brand": "Nissan Kicks",
    "year": 2018,
    "client": "CAROMOTO MOLDOVA",
    "container": "TCLU8824843",
    "status": "UNLOADED",
    "warehouse": "NETO Terminal",
    "weight_kg": "1200.50",
    "eta": "2026-10-01",
    "unload_date": "2026-10-15",
    "transfer_date": null,
    "has_title": true
  }
}
```

### `car-docs` — Список документов машины

Возвращает список документов машины (TITLE и другие).

**Параметры:**
- `--vin <VIN>` — поиск по VIN
- `--car-id <id>` — поиск по ID машины

**Роли:** `chief`, `neto`, `handover`, `accountant`

**Пример:**
```bash
python manage.py bot car-docs --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232
```

**Результат:**
```json
{
  "schema_version": 1,
  "ok": true,
  "result": {
    "vin": "3N1CP5BVXRL481232",
    "car_id": 42,
    "documents": [
      {
        "id": "car_42_title",
        "type": "TITLE",
        "filename": "nissan_kicks_title.pdf",
        "size": 245678,
        "uploaded_date": null
      }
    ]
  }
}
```

### `download-car-doc` — Скачать документ машины

Копирует документ машины в temp-директорию бота (`bot_tmp/<principal_name>/`).

**Параметры:**
- `--doc-id <id>` — ID документа из `car-docs` (формат: `car_42_title`)
- **или** `--vin <VIN> --type TITLE` — поиск по VIN + тип документа
- `--out <filename>` (опционально) — имя выходного файла (только внутри tempdir)

**Роли:** `chief`, `neto`, `handover`, `accountant`

**Примеры:**
```bash
# По ID документа
python manage.py bot download-car-doc --token %BOT_TOKEN% --doc-id car_42_title

# По VIN + тип
python manage.py bot download-car-doc --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232 --type TITLE

# С кастомным именем файла
python manage.py bot download-car-doc --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232 --type TITLE --out my_title.pdf
```

**Результат:**
```json
{
  "schema_version": 1,
  "ok": true,
  "result": {
    "filename": "nissan_kicks_title.pdf",
    "path": "/var/www/.../media/bot_tmp/neto-bot/nissan_kicks_title.pdf",
    "size": 245678
  }
}
```

После этого файл можно скопировать по SCP или использовать локально.

**Безопасность:**
- `--out` путь проверяется — разрешены только пути внутри temp-директории бота
- Попытка записи в `/etc/passwd` или другие системные пути будет отклонена

## Использование с re-export (без TR)

Для реэкспорта (например, в Украину) нет Transit Request, но можно получить title через эти команды:

```bash
# 1. Проверяем информацию о машине
python manage.py bot car --token %NETO_TOKEN% --vin 3N1CP5BVXRL481232

# 2. Смотрим какие документы есть
python manage.py bot car-docs --token %NETO_TOKEN% --vin 3N1CP5BVXRL481232

# 3. Скачиваем title
python manage.py bot download-car-doc --token %NETO_TOKEN% --vin 3N1CP5BVXRL481232 --type TITLE

# 4. Копируем на свой сервер
scp /path/from/result bot-server:/destination/
```

## Сопоставление клиентов Caromoto

### `caromoto-client-map` — список и запись соответствия

Роль: `parser`. Без `--apply` ничего не пишет. Запись: `--apply --idempotency-key`.

Офис целиком (id Caromoto пустой) или конкретный клиент Caromoto:

```bash
python manage.py bot caromoto-client-map --token %BOT_TOKEN%

python manage.py bot caromoto-client-map --token %BOT_TOKEN% --map-action upsert --office "PLB Office" --client-id 4 --apply --idempotency-key map-plb

python manage.py bot caromoto-client-map --token %BOT_TOKEN% --map-action upsert --caromoto-client-id 63570 --client-id 17 --apply --idempotency-key map-63570
```

Импорт `apply-caromoto` берёт клиента сначала по id Caromoto, если пары нет — по офису. Пока таблица пустая, машины без клиента пропускаются (`no-client`). Готовые строки сюда не заносятся: их пришлёт бот «Парсер».

Склад уже существующего контейнера импорт не затирает. Чтобы заменить его:

```bash
python manage.py bot apply-caromoto --token %BOT_TOKEN% --batch 12 --set-warehouse --apply --idempotency-key apply-12
```

## Машина без своего контейнера

### `car-upsert`

Роль: `parser`. Чужой контейнер пишется в `foreign_container_number`, свой контейнер не создаётся. ETA такой машины — поле `foreign_eta`.

Уведомление клиенту не уходит, пока нет `--notify`.

```bash
python manage.py bot car-upsert --token %BOT_TOKEN% --vin 3N1CP5BVXRL481232 --brand Nissan --model Kicks --year 2018 --client-id 17 --warehouse ATLANTIC --unload-site 1 --foreign-container TCLU8824843 --status UNLOADED --unload-date 2026-10-20 --eta 2026-10-18 --apply --idempotency-key car-1
```

Статусы: `FLOATING` или `UNLOADED`.

### `set-container-status` для парсера

Роль `parser` может двигать контейнер в `IN_PORT` / `UNLOADED`. Если у контейнера склад уже стоит, другой склад ставится только с `--set-warehouse`.

## Пакет Neto

`neto-bundle` для склада Neto предлагает получателей BLR:

`dovile@`, `rita@`, `customs@`, `evaldas@`, `evaldas.k@`, `ramune@netoterminal.lt`.

В ответе без `--apply` уже есть `subject` и `files`. Номер BLR при этом не тратится.

`--client-docs` — клиент приносит документы сам. На машину нужны тайтл, PDF клиента (`OTHER`, не сгенерированный) и два ĮSIPAREIGOJIMAS (`OBLIGATION`).

EORI — код страны и до 15 букв или цифр: `PL`, `LT`, `LV`, `EE`, `DE`, `BY` и другие известные префиксы (ЕС, а также `GB`, `XI`, `UA`, `MD`, `NO`, `CH`). `PL525308706300000` проходит. Код компании вроде `IK185300064` даёт `EORI_FORMAT`. Пустой код — `EORI`.

В `files` у каждого файла есть `id`: pk документа заявки или `car_<id>_title`, если тайтл взят из карточки машины.

У каждой машины в `result.completeness.cars` есть `buyer_address` — адрес покупателя из пакета документов этой машины. Пустая строка, если адрес ещё не задавали.

```bash
python manage.py bot neto-bundle --token %BOT_TOKEN% --number TR-1 --client-docs
```

## Заявка на автовоз (роль `neto`)

Команды ниже доступны только роли `neto`. Запись в базу — только с `--apply --idempotency-key`. Без `--apply` команда показывает, что изменится, и ничего не сохраняет. Клиенту письма и Telegram не уходят.

Файлы скачивания и загрузки лежат в `media/bot_tmp/neto/` — та же папка, куда `neto-bundle` кладёт ZIP.

### `tr-docs` — список документов заявки

`--tr` — номер заявки или её id.

Типы: `OBLIGATION`, `INVOICE`, `PASSPORT`, `PAYMENT_ORDER`, `LETTER_USA`, `CONTRACT`, `SIGNATURE`, `OTHER`. Тайтл машины в этот список не входит.

В каждой строке: `id`, `type`, `filename`, `size`, а также `vin` и `car_id` (файл привязан к авто заявки).

`cars` — машины заявки: `vin`, `car_id`, `buyer_address` (адрес покупателя из пакета документов, тот же, что в окне «Паспорт» и в PDF для Neto).

```bash
python manage.py bot tr-docs --token %BOT_TOKEN% --tr TR-20261029-001
```

### `download-tr-doc` — скопировать документ заявки

`--tr` и `--type` обязательны. Если файлов этого типа несколько, нужен `--doc-id` из `tr-docs`. Чужой документ (другая заявка) не отдаётся.

```bash
python manage.py bot download-tr-doc --token %BOT_TOKEN% --tr TR-20261029-001 --type CONTRACT --doc-id 15
```

### `tr-update` — водитель, перевозчик, номера, граница

Поля, которые можно передать (любое подмножество):

- `--driver-name` — ФИО водителя
- `--driver-passport` — паспорт водителя (отдельное поле заявки, не паспорт покупателя)
- `--driver-phone`
- `--carrier-name` — компания перевозчика
- `--carrier-eori` — та же проверка, что в `neto-bundle`
- `--tractor-plate` — номер тягача
- `--trailer-plate` — номер прицепа
- `--border-crossing` — граница

Статус заявки команда не меняет.

Адрес покупателя — отдельно на каждую машину заявки:

- `--buyer-address "..."` — до 500 символов. Пустая строка стирает адрес.
- `--car-vin` (или `--vin` / `--car-id`) — какая машина. Если в заявке одна машина, VIN можно не указывать. Чужой VIN отклоняется.

Адрес пишется в пакет документов машины (`buyer_address`). Оттуда его показывает карточка заявки в окне «Паспорт» и оттуда же его подставляют инвойс, платёжка и обязательство для Neto. Кириллический адрес (`buyer_address_ru`) эта команда не меняет.

```bash
python manage.py bot tr-update --token %BOT_TOKEN% --tr TR-20261029-001 --driver-name "Jonas Petraitis" --driver-passport MC3902087 --driver-phone +37060000000 --carrier-name "ANGER TRANSPORT" --carrier-eori PL525308706300000 --tractor-plate LTL123 --trailer-plate TR9 --border-crossing Medininkai --apply --idempotency-key tr-1
python manage.py bot tr-update --token %BOT_TOKEN% --tr TR-20261029-001 --car-vin 1HGCM82633A004352 --buyer-address "ul. Gaya 5, Minsk" --apply --idempotency-key addr-1
```

### `tr-upload-doc` — положить файл в заявку

`--type`: `CONTRACT`, `SIGNATURE`, `PASSPORT` или `OTHER`. `--file` — путь на сервере внутри `media/bot_tmp/neto/` (имя файла или полный путь). Выход за эту папку, в том числе через `..`, отклоняется. Допустимы PDF, JPG и PNG, до 20 МБ.

Документ привязан к машине заявки. Если машина одна, VIN не нужен. Если несколько — укажите `--vin`, `--car-vin` или `--car-id`, и эта машина должна быть в заявке.

```bash
python manage.py bot tr-upload-doc --token %BOT_TOKEN% --tr TR-20261029-001 --type CONTRACT --file contract.pdf --apply --idempotency-key up-1
python manage.py bot tr-upload-doc --token %BOT_TOKEN% --tr TR-20261029-001 --type PASSPORT --file passport.pdf --car-vin 1HGCM82633A004352 --apply --idempotency-key pass-1
```

### `tr-delete-doc` — удалить документ заявки

`--tr` и `--doc-id` обязательны. `--doc-id` — числовой `id` из `tr-docs`. Команда удаляет только документ этой заявки и его файл. Документ другой заявки, неизвестный id и тайтл карточки машины (`car_<id>_title`) не удаляются.

Тайтл, скопированный в пакет из карточки машины, можно удалить по id документа. При следующем открытии карточки заявки копия тайтла создаётся снова. Паспорт, договор и прочие загруженные файлы обратно не появляются.

```bash
python manage.py bot tr-delete-doc --token %BOT_TOKEN% --tr TR-20261029-001 --doc-id 15 --apply --idempotency-key del-1
```

## Бухгалтер

Роль `accountant`. Команды ниже только читают базу CRM и не ходят в Revolut и site.pro.

### `bank-transactions` — банковские операции

Строки уже загруженной таблицы `BankTransaction` (синхронизация Revolut / импорт Paysera). Ключи Revolut в ответ не попадают.

**Параметры:**

- `--date-from YYYY-MM-DD` и `--date-to YYYY-MM-DD` — обязательны, даты включительно
- `--direction in|out` — входящие (`amount > 0`) или исходящие
- `--account <id, имя или external_id счёта, либо имя подключения>`
- `--min-amount` / `--max-amount` — по знаковой сумме (плюс — вход, минус — выход)
- `--search <текст>` — контрагент, назначение или номер связанного счёта
- `--limit` — сколько строк вернуть (по умолчанию 200, не больше 1000)

В строке: `id`, `date`, `amount`, `currency`, `direction`, `counterparty`, `description` (сюда при синхронизации попадает reference Revolut), `account`, и если есть связь: `matched_invoice_id`, `matched_proforma_id` (если связанный документ — серия AV/AVBLC), `matched_proforma_ids` (журнал `proforma-upsert`), `matched_payment_id`.

Отдельного поля «счёт» у операции нет: команда подставляет `BankAccount` с тем же подключением и валютой. Если таких счетов несколько, `account.id` пустой.

```bash
python manage.py bot bank-transactions --token %BOT_TOKEN% --date-from 2026-10-01 --date-to 2026-10-31 --direction in --account "Revolut EUR" --min-amount 100 --search ANGER --limit 200
```

### `proformas` — проформы в CRM

Раньше команда читала пустую таблицу `Proforma` (её заполняет только `proforma-upsert`) и черновики с номером на `AV`. Выставленные проформы CRM в список не попадали.

Теперь `proformas` — это `NewInvoice` типов `PROFORMA` (серия AV) и `PROFORMA_BLC` (AVBLC), любой статус. Номер в CRM: `AV-000013`. Поле `number_compact` — тот же номер без дефиса (`AV000013`).

Серия AV, выписанная в site.pro, в CRM не хранится. `import_sitepro_history` такие документы только печатает и не создаёт строки. Ежедневный sync тянет только PARDP. Чтобы `AV000013` из site.pro появился в этой команде, его нужно отдельно загрузить в `NewInvoice` — в этом изменении синхронизация site.pro не добавляется.

Журнал бота остаётся в `bot_proformas`.

```bash
python manage.py bot proformas --token %BOT_TOKEN%
```

### Тайтл машины

`car-docs` и `download-car-doc` доступны роли `accountant` наравне с `neto`: список и копия скана тайтла (`TITLE`).

## Другие команды

- `capabilities` — список доступных команд для роли
- `dictionary` — справочник терминов и статусов
- `tempdir` — путь к temp-директории бота
- `emails` — список писем контейнеров
- `download-attachment` — скачать вложение из email
- `neto-bundle` — сформировать ZIP-пакет документов для Transit Request
- ...и другие (см. `python manage.py bot capabilities --token %BOT_TOKEN%`)

## Передача авто (`transfer-cars`) — уведомления выключены

Смена статуса машины на `TRANSFERRED` никому ничего не отправляет: ни клиенту, ни на другие адреса, ни по email, ни в Telegram. Это одинаково для:

- `python manage.py bot transfer-cars` — с `--no-notify` и без него;
- сохранения статуса в админке;
- `Car.save()` и массового `transition_cars` (автовоз, каскад контейнера).

Флаг `--no-notify` по-прежнему принимается, чтобы старые команды ботов не ломались. Пока настройка `CAR_TRANSFERRED_NOTIFICATIONS` не равна `true` (по умолчанию `false`), флаг ничего не меняет: рассылки о передаче нет в любом случае.

Уведомления о разгрузке и планируемой разгрузке этот запрет не затрагивает.

```bash
python manage.py bot transfer-cars --token %BOT_TOKEN% --vins 3N1CP5BVXRL481232 --transfer-date 2026-10-29 --apply --idempotency-key once-1
```

В ответе `notifications` для передачи пустой.

## Ошибки

При ошибке команда возвращает `"ok": false` и поле `"error"`:

```json
{
  "schema_version": 1,
  "ok": false,
  "error": "Машина не найдена (VIN=INVALID, id=None)."
}
```

Exit code при ошибке будет ≠ 0.
