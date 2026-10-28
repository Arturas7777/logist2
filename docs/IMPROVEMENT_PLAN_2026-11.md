# План улучшений Logist2 — ноябрь 2026

> Бриф для работы с агентом: «начинаем работу» → «работаем по
> `docs/IMPROVEMENT_PLAN_2026-11.md`, волна N» — агент идёт по пунктам волны.
> Продолжение `docs/IMPROVEMENT_PLAN_2026-10.md`: тот план в основном закрыт,
> здесь — то, что нашлось после него (корректность денег, безопасность
> AI-агента, оставшиеся узкие места, понятность кода).

Составлен 2026-10-27 по результатам: собственных замеров (Django test client
на свежей копии прод-БД, `EXPLAIN ANALYZE`, прогон `pytest` с блокировкой
сети) и четырёх параллельных read-only аудитов — деньги, производительность,
логистика/почта/интеграции, архитектура.

**Пометки достоверности:** ✅ — проверено вручную по коду или замером;
без пометки — найдено аудитом, перед исправлением перепроверить (строки
могли сдвинуться).

## Правила работы по плану

1. **Один коммит = один пункт** (мелкие родственные из волны 4 можно
   группировать по 2–3).
2. Для каждого пункта: тест на сценарий → код → `pytest` → `ruff check . ;
   ruff format <файлы>` → `python manage.py makemigrations --check --dry-run`
   → коммит → `[x]` здесь + запись в `CHANGELOG.md`.
3. Деньги (раздел M) — только через `BillingService`, тест обязательно
   проверяет **итоговый `Client.total_balance`**, а не только статус счёта.
   Перед исправлением данных на проде — `--dry-run` и отчёт затронутых записей.
4. Производительность — замер «до/после», бюджет запросов фиксировать в
   `core/tests/test_query_budgets.py`.
5. Браузерную проверку не запускаем по умолчанию — программный смоук.

---

## 0. Исходное состояние (2026-10-27)

### Качество на `master`

- `pytest`: **1063 passed**, 4 skipped, 1 xfailed (~65–100 с локально).
- `ruff check` / `ruff format --check`: чисто. `makemigrations --check`: пусто.
- Покрытие `core` ≈ **58 %** при пороге CI 38 %; `billing_service` ≈ 45 %,
  `admin/banking` 24 %, `email_compose` 12 %, `invoice_audit_service` 0 %.
- 262 предупреждения в тестах, из них про Django 6.0: `CheckConstraint.check`
  ×16, `format_html()` без аргументов ×13, `LogEntry.log_action` ×1.

### Статус пунктов плана 2026-10 (проверка аудитом)

| Пункт | Статус | Что осталось |
|---|---|---|
| B1, B2, B3, B5, B6, B13 | сделано | B1/B3 обходятся через форму инвойса и inline контейнера — см. M8, L2 |
| B7 кредит-ноты | частично | KRE считается долгом клиента — M2 |
| B9 разнесение платежа | частично | нет страницы «Разнести платёж» в админке; в BT пишется только первый инвойс |
| B11 freeze TRANSFERRED | частично | `signals/container.py:105` — L1 |
| Q2–Q8, Q12, P2, P3, P7 | сделано | — |
| P1 фото через nginx | частично | флаг `PHOTO_SERVE_VIA_NGINX` по умолчанию выкл.; `/media/` не закрыт |
| P4 дашборд | частично | `_prime_dashboard_cache` выбрасывает результат; любой save сносит ключи — S6 |
| P5 GDrive | частично | нет `bulk_create`, на фото 2×`exists()` + `save()` |
| P6 пересчёт хранения | частично | нет одного SQL-UPDATE |
| P11 системный монитор | **не сделано** | S1 |

### Замеры страниц (холодный кэш, локальный PG, копия прода)

| Страница | SQL | SQL мс | Всего мс | Комментарий |
|---|---|---|---|---|
| `/admin/system-monitor/` | 7 | 2 760 | **14 185** | живой снимок на каждый запрос (локально Redis не запущен → 2×4 с таймаута; `process_iter` 1,3 с) |
| `/admin/core/newinvoice/` | 11 | **299** | 533 | ✅ planning 200 мс при execution 7 мс (14 JOIN) |
| `/admin/core/transaction/` | 10 | 184 | 294 | тот же паттерн (10+ JOIN from_*/to_*) |
| `/admin/dashboard/` | 51 | 135 | 451 | ×7 одинаковых запросов `core_company` |
| `/admin/reconciliation/` | 33 | 83 | 360 | — |
| `/admin/core/autotransport/<pk>/change/` | 36 | 44 | 256 | ×10 запросов `core_car` |
| остальные списки/карточки | 9–30 | 15–70 | 70–210 | ок |

---

## Волна 1. Деньги (раздел M) — корректность балансов

Главная тема: **балансы и долги клиентов могут показываться неверно**.

### [x] M1. Отмена транзакции не пересчитывает деньги (High, S) ✅
- Где: `core/signals/transaction.py:23` — `if instance.status != "COMPLETED": return`.
  Админка разрешает COMPLETED → CANCELLED (`core/admin/billing/transaction.py:115-118`),
  в модели пересчёта при смене статуса тоже нет. `reverse_cash_payments`
  ставит `_skip_balance_recalc` (`billing_service.py:339-345`).
- Решение: `BillingService.cancel_transaction(trx, reason)` — под блокировкой,
  пересчёт балансов обеих сторон и `paid_amount` счёта. В сигнале — пересчёт
  и при уходе из COMPLETED (старый статус запоминать в `pre_save`/`from_db`).
  Статус в форме админки сделать read-only, отмену — кнопкой через сервис.
- DoD: тест «COMPLETED-платёж отменён → `paid_amount`, статус счёта,
  `Client.balance` и `total_balance` вернулись»; `verify_balances` чист.

### [x] M2. Кредит-нота становится долгом клиента (High, S) ✅
- Где: `billing_service.py:1074-1092` — KRE создаётся ISSUED с положительной
  суммой и `recipient_client`; `Client.open_invoices_debt` (`models/clients.py:215`)
  тип документа не фильтрует. Также `admin/partners.py:402-406`,
  `mixins.py:132-155`, портал `portal_invoices.py:56`. Через 14 дней
  `check_overdue_invoices` переведёт KRE в OVERDUE.
- Решение: один queryset-метод «открытые счета к оплате»
  (`NewInvoice.objects.receivable()`), исключающий `CREDIT_NOTE`, и
  использовать его во всех местах подсчёта долга. KRE не участвует в
  `check_overdue_invoices`.
- DoD: тест «PARDP 100 € → KRE 100 € → `total_balance` клиента = 0, KRE не
  OVERDUE через 15 дней»; миграция данных не нужна (проверить 5 существующих KRE).

### [x] M3. Черновик становится просроченным (High, S) ✅
- Где: `models/billing.py:758-760` — `update_status` ставит OVERDUE при
  `is_overdue`, а `is_overdue` (`:609-615`) не исключает DRAFT.
- Решение: OVERDUE только из ISSUED / PARTIALLY_PAID. Скрипт-отчёт по проду:
  сколько счетов сейчас OVERDUE, которые никогда не были ISSUED.
- DoD: тест «DRAFT с прошедшим `due_date` после `recalculate_paid_amount` остаётся DRAFT».

### [x] M4. Автосверка привязывает поступления к оплаченным счетам (High, M) ✅
- Где: `core/management/commands/auto_reconcile.py:181` — кандидаты
  `exclude(status="CANCELLED")`, т. е. включая PAID, DRAFT, AV; при
  нулевом остатке транзакция не создаётся (`:312-314`), но `matched_invoice`
  записан и операция считается сверенной (`models/banking.py:399-403`).
  Переплата в пределах допуска 1 € отбрасывается.
- Решение: кандидаты — только открытые счета с остатком > 0; переплата и
  совпадение с уже оплаченным счётом → BALANCE_TOPUP клиенту + пометка в
  `reconciliation_note`, а не «тихо сверено».
- DoD: тесты: BT на PAID-счёт; BT на 100.80 при счёте 100 → TOPUP 0.80;
  отчёт `--dry-run` по прошлым операциям, привязанным к PAID без транзакции.

### [x] M5. Одна формула `paid_amount` (High, S)
- Где: модель — платежи − возвраты + ADJUSTMENT (`models/billing.py:668-679`);
  `_collect_balance_mismatches` (`tasks.py:463-493`) и `verify_balances --fix`
  (`verify_balances.py:113-121`) — без ADJUSTMENT, т. е. «чинят» счета с
  кредит-нотами обратно в ISSUED; `check_data_integrity.py:112-133` — без
  исключения LINKED_PAID.
- Решение: одна функция/аннотация в модели, все проверки и «ремонт» через неё.
- DoD: тест «счёт с кредит-нотой → `repair_balance_consistency` ничего не меняет».

### [x] M6. Оплата клиента — всегда пара TOPUP + PAYMENT (High, M)
- Где: `pay_invoice` и `register_cash_payment` создают одиночный PAYMENT
  от клиента (`billing_service.py:298-328, 416-430`); вызовы —
  `invoice_urls.py:223-245`, `invoice_actions.py:52-85`, `invoice_forms.py:410-413`.
  Это та же ошибка «ложного долга», что исправлена для банка
  (`register_incoming_bank_payment`); action «Обнулить балансы» маскирует её.
- Решение: один путь «клиент платит» внутри `BillingService`, используемый
  и кассой, и кнопкой «Оплатить», и банком.
- DoD: тест «оплата наличными PARBLC → `Client.balance` = 0, `total_balance` = 0».
  Отчёт по проду: клиенты с отрицательным `balance` из-за одиночных PAYMENT.

### [x] M7. Деактивация услуги каталога удаляет её у всех машин (High, S)
- Где: `signals/service_catalog.py:190-206` — при выключении или цене 0
  `CarService` удаляется у всех авто, включая TRANSFERRED и машины в
  выставленных счетах, вместе с индивидуальными ценами; ошибки глотаются (`:226`).
- Решение: явный сервис; трогать только не-TRANSFERRED авто и машины без
  счетов или только в DRAFT; цену 0 не считать причиной удаления.
- DoD: тест «выключили услугу → у переданной машины и в ISSUED-счёте всё на месте».

### [x] M8. Обходы FSM и B1 через форму инвойса (Medium, M)
- Где: `invoice_forms.py:271` — статус из POST (можно отменить счёт с оплатами);
  `:306-357` — смена выставителя/получателя у оплаченного счёта;
  `_handle_manual_items` (`:465-521`) и `InvoiceItemInline` меняют `total`
  при любом статусе без пересчёта оплаты; `generate_invoices` меняет состав
  машин у ISSUED (`models/auto_transport.py:250-252`); `add_invoice_item`
  допускает ISSUED (`billing_service.py:198`). Хардкод «Caromoto Lithuania»
  (`invoice_forms.py:335, 529`) вместо `COMPANY_NAME`.
- Решение: статус меняется только кнопками через `BillingService`; стороны и
  позиции у ISSUED+ — read-only (правка только через кредит-ноту/force с LogEntry).

### [x] M9. Гонки в денежных операциях (Medium, S)
- `register_cash_payment` и `change_invoice_series` без `select_for_update`
  (`billing_service.py:283-328`) — двойной клик = двойной платёж.
- `Transaction.clean` проверяет переплату без блокировки и без ADJUSTMENT
  (`models/billing.py:1700-1731`).
- Разный порядок блокировок: `pay_invoice` (счёт → контрагенты) vs сигнал
  (контрагенты → счёт) — возможен deadlock.
- DoD: тесты в `test_concurrency.py` (PG-only).

### [x] M10. Банковская операция: перепривязка и изменения из Revolut (Medium, M)
- `matched_invoice` / `matched_transaction` редактируются в форме
  (`admin/banking.py:482, 535-575`) — старый платёж остаётся, возможен второй.
- `revolut_service.py:323-335` молча перезаписывает сумму/состояние уже
  привязанной операции; возврат (reverted) не обрабатывается.
- Решение: поля read-only, сервис `unlink_bank_transaction` (отмена платежа
  через M1); при изменении суммы/состояния привязанной BT — Task менеджеру.

### [x] M11. Часовой пояс (Medium, M)
- Где: `settings/base.py:284` `TIME_ZONE="UTC"`; ~40 мест
  `timezone.now().date()` (`models/cars.py:699`,
  `container_lifecycle_service.py:92`, `signals/autotransport.py:92`,
  `admin/car.py:1386`, `models/billing.py:615`); `transport_docs.py` —
  `date.today()`.
- Суть: с 00:00 до 02:00–03:00 по Вильнюсу «сегодня» = вчера → ±1 день
  хранения, неверные `transfer_date`, `due_date`, `is_overdue`.
- Решение: `TIME_ZONE="Europe/Vilnius"` + `timezone.localdate()` везде;
  beat-расписание пересмотреть (`CELERY_TIMEZONE`). Решение бизнеса — ниже.

### [x] M12. Мелочи денежного домена (Low)
- `float()` и запятая в ценах: `car_admin_service.py:110-168` — «12,50»
  игнорируется, наценка сбрасывается в 0.
- Округления: `ROUND_HALF_UP` в коде vs HALF_EVEN при сохранении; ручные
  позиции не квантуются (`invoice_forms.py:486-494`).
- Срок оплаты «сегодня + 14», а не «дата счёта + 14» (`models/billing.py:1064`,
  `billing_service.py:108`).
- `refund` и `reverse_cash_payments` ищут исходные транзакции по тексту
  описания (`billing_service.py:333-338, 821-825`) → прямая FK на исходную.
- `PersonalTransfer.execute` двигает деньги мимо `BillingService`
  (`models/billing.py:2078-2134`), без идемпотентности.
- `balance_manager.recalculate_all_balances` ловит ошибки внутри одного
  `atomic` — на PG после первой ошибки падают все следующие.
- Тест-долг: `test_ledger_immutability.py`, `test_wave_money_credit_note.py`
  не проверяют `total_balance`.

---

## Волна 2. Логистика и безопасность AI-агента (разделы L, A)

### [x] L1. Каскад `unload_date` трогает переданные авто (High, S)
- Где: `signals/container.py:105` — `container_cars.update(unload_date=...)`
  для всех машин; противоречит `container_lifecycle_service.py:143-144`.
  Признано strict-xfail тестом `test_wave_money_container_cascade.py:257-262`.
- Решение: удалить сигнал, оставить один путь `apply_unload_date_change`.
- DoD: xfail-тест становится обычным и проходит.

### [x] L2. Одна функция смены статуса авто (High, M)
- Сейчас статус меняют 4 места: `Car.save` (`models/cars.py:696-697` —
  `transfer_date` заставляет TRANSFERRED, откат невозможен), bulk action
  (`admin/car.py:1330` — `.update(status=...)`), каскад контейнера, сигнал
  автовоза (`signals/autotransport.py:78-80, 113-117` — на **каждом** save
  в LOADED/IN_TRANSIT/DELIVERED, мимо FSM, «Важное» и истории). Inline машин
  в карточке контейнера (`admin/container.py:337-350`) тоже переписывает статус.
- Решение: `CarLifecycleService.transition_cars(ids, target, date, source)` —
  FSM, `transfer_date` (с очисткой при откате), `CarStatusHistory`, свёртка
  статуса контейнера, finalize, уведомления. Автовоз вызывает её только при
  переходе статуса; авто-реген счетов автовоза — только DRAFT
  (`tasks.py:1707-1728` сейчас берёт force-набор статусов).
- DoD: тесты на откат TRANSFERRED → UNLOADED, повторный save автовоза, «Важное».

### [x] L3. Гонка `generate_invoices` автовоза (Medium, S)
- FORMED, `m2m_changed` и finalize одновременно вызывают генерацию без
  блокировки (`models/auto_transport.py:244-274`) → два DRAFT одному клиенту;
  клиент, чьи машины убрали, остаётся с устаревшим счётом.
- Решение: `select_for_update` на автовоз; обработка «клиентов без машин».

### [x] L4. Идемпотентность почты и уведомлений (Medium, M)
- Уведомления о контейнере: `select_for_update` блокирует только
  существующие строки (`email_service.py:245-250`) → двойная отправка;
  SMTP внутри транзакции, `EMAIL_TIMEOUT` не задан; неудачная отправка не
  бросает исключение → нет retry. Решение: claim-запись + `UniqueConstraint`.
- Gmail-ингест: вложения качаются до дедупа по `message_id`
  (`email_ingest.py:314` vs `:347`); «ядовитое» письмо навсегда держит курсор
  (`:173-181`) → карантин после N попыток; мёртвый код обратной синхронизации
  UNREAD (`:439-456`).
- Отправка письма: запись `ContainerEmail` создаётся после отправки
  (`email_compose.py:733`) → IntegrityError при гонке с синком → PENDING-запись
  до отправки.
- Фото GDrive: дедуп по имени без `photo_type` (`google_drive_sync.py:467-479`) →
  хранить `drive_file_id` + `UniqueConstraint(container, drive_file_id)`;
  `sync_container_by_number` глотает исключения (`:702-704`), retry не работает.
- Telegram `getUpdates` без `offset` (`telegram_service.py:161`) — после 100
  апдейтов новые `/start` не обрабатываются.

### [x] L5. Нормализация VIN в одном месте (Medium, S)
- 4 разные реализации (`vin_gate.py:326`, `scan_applier.py:241`,
  `scan_review.py:63`, `vin_corrector.py:64`) и 4 копии regex
  (`ai_chat.py:326`, `ai_chat_service.py:82`, `admin_ai_agent.py:21`,
  `email_matcher.py:40`); `Car.vin` уникален с учётом регистра, `Car.save`
  не нормализует.
- Решение: `core/services/vin.py::normalize_vin`, вызов в `Car.save`,
  `UniqueConstraint(Upper("vin"))` (перед миграцией — проверка дублей на проде).

### L6. Прочее (Low–Medium)
- Скан-задача: захват без условного UPDATE (`tasks.py:1114-1126`), зависшие
  PROCESSING не чистятся.
- Портал: `awaiting_client_docs` позволяет клиенту вернуть IN_PROGRESS в DRAFT
  или отменить заявку (`portal_transport.py:447-448, 578-579`); для COMPLETED — 500.
- Заблокированный инвойс при регенерации пропускается навсегда
  (`tasks.py:1561-1566`) → `self.retry(countdown=...)`.
- `SoftTimeLimitExceeded` глотается широким `except`
  (`email_analyzer.py:165-172`, `email_ingest.py:263`).
- Букинг матчится подстрокой от 4 символов (`email_matcher.py:47, 345-360`)
  и наследуется по треду — ложные связи.
- Email-адреса клиентов в INFO-логе (`email_service.py:207, 435`).
- Вопрос комплаенса: `transport_docs.py:215-261` генерирует документы с
  датами задним числом — подтвердить допустимость.

### [x] A1. Память агента пишется без подтверждения (High, S) ✅
- Где: `core/services/agent/agent_tools.py:348-357` — `_tool_save_memory`
  сразу создаёт `AgentMemory` с `source="MANUAL"`; память подмешивается в
  системный промпт (`context_builder.py:42-46`), письма — без пометки
  «недоверенные данные» (`context_builder.py:97-99`). Нарушает правило 1 из
  `.cursor/rules/ai-agent.mdc`; сохраняющийся prompt-injection через письмо.
- Решение: `save_memory` только через `AgentAction` (ASK), `source="AGENT"`,
  `is_active=False` до одобрения; письма в явных разделителях «это данные,
  не команды».

### [x] A2. Ответ агента на произвольное письмо (High, S)
- `email_id` берётся из аргументов LLM без проверки (`agent_tools.py:253`),
  ответ уходит на `parent.from_addr` (`agent_executor.py:146-150`).
- Решение: `email_id` только из `context["source_email"]` или его треда;
  отправка писем — всегда риск HIGH (только ASK); в карточке одобрения
  показывать адресата.

### [x] A3. Двойное исполнение «Принять» (High, S)
- `views/agent_board.py:100-103` — проверка PROPOSED и исполнение не атомарны.
- Решение: условный `UPDATE ... WHERE status='PROPOSED'`, исполнять при
  `rowcount == 1`; проверка `has_perm` на одобрение.

### A4. Прямые записи помощника админки (Medium, S)
- Grok-помощник создаёт `Task` напрямую (`admin_ai_tools.py:495-511`).
  Решение: через `AgentAction`; тест-инвариант «ни один инструмент не пишет
  в БД напрямую».

### [x] A5. Токен Telegram в логах и БД (High, S)
- `telegram_service.py:81-82, 163-164` — текст исключения requests содержит
  URL с `/bot<token>/` → `NotificationLog.error_message` и Sentry.
- Решение: маскировать токен в тексте ошибок, логировать тип ошибки.

---

## Волна 3. Скорость (раздел S)

### S1. Системный монитор из последнего снимка (High, S) — бывший P11
- Где: `core/views/system_monitor.py:34, 52` — `collect_snapshot()` на
  каждый запрос и на HTMX-обновление раз в 30 с; внутри
  `cpu_percent(interval=0.3)`, 28 вызовов `systemctl`, `process_iter`,
  Redis `INFO`/`LLEN` (без таймаута сокета), `pg_stat_statements`.
- Решение: страница и автообновление читают последний `SystemMetric`;
  живой снимок — по кнопке; таймаут подключения к Redis 1 с.
- DoD: страница < 300 мс.

### S2. Списки инвойсов и транзакций: время планирования (High, S) ✅
- Где: `core/admin/billing/invoice_forms.py:41-57` (14 `select_related`),
  `core/admin/billing/transaction.py:21-31, 89+` (10+). Замер: planning
  **200 мс**, execution 7 мс; с 3 JOIN planning < 1 мс.
- Решение: стороны (`issuer_*`, `recipient_*`, `from_*`, `to_*`),
  `linked_*`, `created_by` — через `prefetch_related` (5–10 дешёвых
  `IN`-запросов на 50 строк); `select_related` оставить для 2–3 полей.
- DoD: SQL списка < 40 мс; бюджет в `test_query_budgets.py`.

### S3. Проверка VIN в форме (High, M)
- Где: `core/admin/car_forms.py:57-70` — `check_vin()` при любом сохранении;
  `_near_duplicate` (`vin_gate.py:581-595`) выгружает VIN всех машин в Python;
  inline контейнера (`admin/inlines.py:372`) повторяет это на каждую строку;
  при устаревшем `VinCheck` — HTTP к NHTSA (5 с) последовательно.
  Аналогично `find_similar_vins` (`scan_applier.py:119-125`).
- Решение: проверять только при `"vin" in changed_data`; в формах
  `allow_network=False` (сеть — фоном через `schedule_vin_check`); поиск
  похожих в SQL (pg_trgm или префикс/суффикс), один раз на formset.

### S4. Доска заявок ~400 запросов (High, S)
- Где: `core/views/requests_board.py:190-191` → `transport_request_check.py:76, 141`,
  `models/website.py:1065`, `transport_declarations.py:63, 68` — обходы prefetch.
- Решение: работать с загруженными `.all()`, сортировать в Python, кэш
  `TransportDocumentRule` на запрос.
- DoD: бюджет в `test_query_budgets.py`.

### S5. Синхронные LLM-вызовы в веб-запросе без таймаута (High, M)
- Паспорт/адрес: `portal_transport.py:842-866` → `transport_package_actions.py:251, 275, 530`;
  `anthropic.Anthropic(...)` без `timeout` (`passport_extractor.py:203`,
  `expense_analytics_service.py:211`) → по умолчанию 600 с при gunicorn
  `--workers 3 --worker-class sync --timeout 120`. AI-чат админки — до 6×60 с.
- Решение: распознавание — в Celery с опросом статуса; везде
  `timeout=30, max_retries=1`; лимит общего времени AI-чата.

### S6. Кэш: удалить мёртвый и SCAN-инвалидацию (Medium, S)
- `signals/cache_invalidation.py:36-46` → `cache_utils.py:352-375`: три `SCAN`
  по всему Redis + `delete_many` дашборда на **каждый** save Car/Transaction/
  Invoice/Container; `cache_client_stats` / `cache_warehouse_stats` /
  `cache_company_stats` / `cache_comparison_data` нигде не вызываются.
- Решение: удалить мёртвые кэши и pattern-инвалидацию; инвалидацию дашборда
  схлопнуть в один `on_commit` на транзакцию; `_prime_dashboard_cache` убрать;
  `get_cash_wallet` — один aggregate с `filter=` вместо 4.

### S7. Celery: две очереди (Medium, S)
- Сейчас один воркер `--concurrency=2`, одна очередь
  (`scripts/celery.service:13-16`), долгие периодические задачи (Gmail каждые
  2 мин, банк, аудит контейнеров, ETA, агент) задерживают пересчёт цен,
  реген счетов, миниатюры, уведомления.
- Решение: `task_routes` → `interactive` / `periodic`, отдельные воркеры
  (или `-Q` с приоритетом), `worker_prefetch_multiplier=1`,
  `task_ignore_result=True` для beat-задач. `process_telegram_starts_task` — под лок.

### S8. Лишняя работа на каждом `Car.save` (Medium, M)
- Пересчёт цены ставится в очередь без учёта `update_fields`
  (`signals/car.py:197-198`); старая строка читается трижды
  (`models/cars.py:679, 711`, `signals/car.py:50`); `calculate_total_price`
  сбрасывает prefetch и дважды читает `WarehouseService` (`cars.py:342, 470, 508`)
  → 4–5 запросов на машину в циклах (`admin/container.py:463-488`, `tasks.py:1610-1624`).
- `ContainerAdmin.change_view` (`admin/container.py:956-975`) на GET и POST
  строит полный отчёт сверки и пишет UPDATE — делать только на GET, без записи.
- WebSocket-дебаунс «первый побеждает» (`car_lifecycle_service.py:92`) — в UI
  уходит цена до пересчёта → trailing-отправка или из конца задачи пересчёта.

### S9. Синхронные внешние вызовы в admin actions (Medium, S)
- `push_to_sitepro` в цикле по 30 с (`invoice_actions.py:301`),
  `retry_failed` (`admin/accounting.py:341-349`), тестовый
  `RevolutService.sync_all()` после регенерации JWT (`admin/banking.py:315`) → Celery.

### S10. Ретеншен и мелочи (Low)
- Без ретеншена: `django_session` (cached_db, `clearsessions` не запускается),
  `TrackingRequest` (`views_website/tracking.py:89`), `AIChat`, результаты
  Celery в Redis → добавить в `cleanup_monitoring_tables`.
- `/admin/reconciliation/`: `get_car_profitability` считается дважды
  (`reconciliation_service.py:375-376`).
- `find_discrepancies` (`comparison_service.py:426-442`) и `get_top_items`
  (`expense_analytics_service.py:113-141`) — агрегаты в SQL.
- Дублирующиеся индексы `Car` (`models/cars.py:922-938`).
- nginx: `proxy_buffering off` при sync-воркерах (`nginx_logist2.conf:61`);
  `Connection "upgrade"` на всех запросах (`nginx_caromoto.conf:123`);
  конфиги в репо проксируют на `:8000`, а gunicorn слушает unix-сокет —
  сверить с реальным конфигом на сервере.
- P1 до конца: включить `PHOTO_SERVE_VIA_NGINX` на проде, закрыть `/media/`.
- Подписанные фото: токен меняется при каждом обновлении кэша
  (`signed_photos.py:96-143`) → браузерный кэш не переживает 15 минут.
- `base_site.html` — ~600 строк inline JS/CSS на каждой странице админки → static.

---

## Волна 4. Чистка и тесты (раздел D)

### D1. Сломанный код, который выглядит рабочим (High, S) ✅ частично
- ✅ Action «Обнулить балансы клиентов» вызывает несуществующую команду
  `reset_client_balances` (`core/admin/partners.py:749`) — удалить (см. M6).
- Команды падают при импорте: `init_new_balance_system.py:4`,
  `migrate_services.py:4`; `sync_client_balances.py:51-71` обращается к
  несуществующим полям — удалить.
- ✅ `gunicorn_config.py` нигде не используется и противоречит
  `scripts/gunicorn.service` (gthread/4 потока/порт 8000 vs sync/3 воркера/
  unix-сокет) — удалить, чтобы никто не включил потоки случайно (см. K3).

### D2. Тесты не ходят в сеть и не пишут в `media/` (High, S) ✅
- Тесты с реальными внешними вызовами (ключи из локального `.env`):
  `core/tests/test_agent.py::test_board_answer_question` → Anthropic API;
  `core/tests/test_transport_requests_admin.py::test_email_panel_endpoints_match_template_urls`
  → Google OAuth. В CI ключей нет — там не видно.
- `logist2/settings/test.py` не переопределяет `MEDIA_ROOT` → в
  `media/container_photos/` уже 443 тестовых файла.
- Решение: в `settings/test.py` — `MEDIA_ROOT` во временную папку, пустые
  API-ключи; autouse-фикстура, блокирующая сокеты кроме localhost
  (или `pytest-socket`); удалить мусор из `media/`.

### D3. Предупреждения Django 6.0 (Medium, S) ✅
- `CheckConstraint(check=...)` → `condition=` (16 мест; миграция не нужна, но
  проверить `makemigrations --check`); `format_html()` без аргументов → вынести текст в аргумент,
  например `format_html('<span class="cm-muted">{}</span>', "—")` (`mark_safe`
  в админке запрещён правилами проекта; 13 мест, в т. ч. `core/admin/tasks.py:185-265`);
  `LogEntry.objects.log_action` → `log_actions` (`invoice_actions.py:204`);
  незакрытые файлы в тестах (ResourceWarning).

### D4. Dev-окружение без шума (Low, S) ✅
- `logist2/settings/dev.py:6-7` — логгер `django` на DEBUG: каждая команда
  печатает весь SQL, `runserver` — тысячи строк `autoreload`.
- Решение: `django.db.backends` и `django.utils.autoreload` на INFO, SQL —
  по флагу `SQL_DEBUG=1`.

### D5. Мёртвый код и файлы (Low, S)
- Одноразовые команды: `create_default_company`, `apply_optimizations`,
  `add_performance_indexes`, `fix_ths_line_services`, `fix_photo_names`,
  `import_sitepro_history`, `import_paysera_from_sitepro`,
  `cleanup_gmail_drafts`, `cleanup_email_duplicates`; дубль
  `sync_google_drive_photos` ↔ `sync_photos_gdrive`; опасная `reset_billing`
  (хотя бы запрет при `DEBUG=False`).
- ✅ `core/static/js/logist2_invoice_admin.js` (680 строк, не подключён);
  `templates/email/transport_request_message.html`; `CONTAINER_STATUS_COLORS`
  (`admin/container.py:21`); поле `NewInvoice._balance_updated`;
  `Car.sync_with_container`, `OptimizedContainerManager.update_related`.
- `scripts/`: `inspect_mysql.sh`, `diag_mysql_memory.sh`, `prepare_for_deploy.py`,
  `create_env.py`, `vps_push.sh`, `sync_from_vps.ps1`, `run_all_tests.py`.
- Перед удалением команд — убедиться, что их не вызывает cron на сервере.

### D6. Покрытие и структура тестов (Low, M)
- Поднять ratchet покрытия до ~55 % и перенести в `[tool.coverage.report]`
  (`pyproject.toml`); отдельный порог для `billing_service` после волны 1.
- `test_wave_*` разложить по доменам (`tests/billing/`, `tests/logistics/`…);
  медленные PDF-тесты пометить `@pytest.mark.slow`.

---

## Волна 5. Каркас и понятность (раздел K)

### K1. Одна функция цены услуги (High, S)
- `days * default_price` скопировано 5 раз: `models/cars.py:520`,
  `admin/container.py:389`, `car_service_manager.py:621`,
  `signals/service_catalog.py:221`, `views/api.py:583`;
  `car_admin_service.py:160-168` создаёт складские услуги **без умножения на
  дни** и через `float`. Формула хранения ещё и в `_compute_items_from_cars`
  (`models/billing.py:916-931`).
- Решение: `price_for_service(service, car) -> (price, markup)` в
  `car_service_manager`, все места через неё.

### K2. Сигналы → сервисы (High, L)
- Админка/сервисы импортируют приватные функции сигналов
  (`admin/car.py:1314, 1400`, `container_lifecycle_service.py:172`,
  `car_service_manager.py:689`). `car_post_save` (`signals/car.py:127-216`)
  делает 9 дел. Бизнес-логика в админке: `ContainerAdmin.save_formset`
  (`admin/container.py:308-489`), `recalculate_ths_view`
  (`admin/partners.py:1233-1298`), `WarehouseAdmin.change_view` (`:250-298`).
- Решение: `CarService.apply_changes(car, changes, source)` / сервисы
  контейнера и автовоза вызывают цены, счета, события явно; сигналы — только
  WebSocket и аудит. Модели без побочных эффектов: `generate_invoices` →
  `AutoTransportService`, `PersonalTransfer.execute` → `BillingService`.
- Обновить `docs/signals_classification.md` (устарел).

### K3. Один механизм подавления каскадов (Medium, S)
- Сейчас три: `core/signal_utils.py:21` (подмена `sig.receivers` на процесс),
  `services/cascade_control.py:36` (disconnect/connect), флаги на экземплярах
  (`_bulk_updating`, `_creating_services`…). При sync-воркерах gunicorn это
  безопасно, но сломается при переходе на потоки и уже затрагивает Daphne.
- Решение: `contextvar` «каскад подавлен», который проверяют сами обработчики.

### K4. Статусы как `TextChoices` (Medium, M)
- Литералы: `"TRANSFERRED"` ×132, `"PAID"` ×110, `"COMPLETED"` ×104,
  `"DRAFT"` ×85. `class Status(models.TextChoices)` для Car, Container,
  NewInvoice, Transaction, TransportRequest — значения те же, миграция не
  нужна (проверить `makemigrations --check`); литералы менять постепенно.

### K5. Шимы `core.models_*` / `core.admin_*` (Medium, S)
- 236 импортов в 117 файлах, включая свежий код. Codemod-коммит
  `core.models_X` → `core.models.X` (и строки в `patch(...)`), удалить шимы,
  включить ruff `TID251` (banned-api). `admin_accounting`, `admin_banking`,
  `admin_invoice_audit`, `admin_scans`, `admin_website` не импортирует никто.
- `admin/__init__.py`: убрать `try/except ImportError` вокруг регистрации
  billing/banking/accounting — при ошибке раздел молча пропадает.
- `core.models` не реэкспортирует `NewInvoice` (вопреки README) — либо
  реэкспорт, либо поправить документацию.

### K6. Разрезать большие модули (Medium, L)
- `admin/partners.py` (≈1980): `AutoTransportAdmin` (`:1403-1958`) →
  `admin/autotransport.py`; Client, Carrier — отдельно;
  `balance_transactions_display` Warehouse/Company — в общий миксин.
- `admin/car.py` (≈1770): `*_services_display` (`:1572-1758`) и bulk-actions
  (`:1300-1416`) → отдельные модули.
- `admin/container.py`: scan-job views (`:1038-1330`) → `container_scans.py`.
- `tasks.py` (≈1790, 44 задачи) → пакет `core/tasks/` с явными `name=`
  (beat-имена не меняются); логику `_collect_balance_mismatches`,
  `check_business_rules`, `audit_all_containers` — в сервисы.
- `models/billing.py` (≈2130): генерация позиций (`:782-998`) →
  `services/invoice_items.py`; Transaction → `models/ledger.py`;
  PersonalCard/Transfer → `models/personal.py`.
- `models/website.py` (≈1510): домен TransportRequest (`:651+`) →
  `models/transport_requests.py`.
- Сверка банка: логика живёт в management-команде (`auto_reconcile.py:164`,
  импортируется из `tasks.py:745`) и параллельно в
  `BillingService.auto_reconcile_bank_transactions` с другим допуском →
  `services/bank_reconciliation.py`; нынешний `reconciliation_service.py`
  (прибыльность, SupplierCost) переименовать.
- Views собрать в `core/views/` (сейчас ещё `views_website/`, 4 файла
  `views_*.py` в корне, `view_utils.py`); `google_drive_sync.py` → `services/`.

### K7. Дубли UI (Medium, M)
- Панели писем всё ещё держат свои `refreshBadge` / `markAllRead` /
  `setupEmailPolling` (car `:511/:529/:639`, autotransport `:541/:559/:665`,
  container `:1117/:1135/:1203`) при наличии `email_thread.js` — U1 доделать.
- `views/emails.py`: 4 копии `email_mark_*_read` и 4 копии `email_*_updates` →
  один generic view с реестром `kind → LinkModel`.
- `emails_for_panel` ×3, фильтр «есть непрочитанные» ×3, `colored_status` ×2.
- THS определяется то по `code OR name__icontains`, то только по имени
  (`car_service_manager.py:116-185` vs `:542`).
- Шаблоны: 30 тыс. строк, из них ~7,6 тыс. inline JS и ~8,6 тыс. inline CSS
  (`container/_emails_panel` 2157, `client_transport_requests` 1577,
  `company_dashboard` 1571, `requests_card` 1309, `email/_composer` 1305,
  `base_site` 848) → `static/`, URL через data-атрибуты.

### K8. Настройки и документация (Low, S)
- `settings/base.py` (783 строки, 110 `os.getenv`, 10 самописных парсеров bool) →
  `env_bool`/`env_int`, интеграции в `settings/integrations.py`; 107
  `getattr(settings, ..., default)` с повторяющимися дефолтами → дефолт в одном месте.
- Устаревшие документы → `docs/archive/`: `NEXT_SESSION_PROMPT.md`,
  `ROADMAP_2026-05_high_medium.md`, `CONTAINER_EMAIL_THREAD_PLAN.md`,
  `CAR_AUTOTRANSPORT_EMAIL_PLAN.md`, `case_export_*_handoff.md`, `QUICK_START.md`.
- Актуализировать `.cursor/rules/project-overview.mdc` (пакет `views_website/`,
  ~85 тест-файлов и 1068 тестов, покрытие), `accounting-context.mdc`
  (ссылки на `admin_billing.py`, кредит-ноты после M2, оплата после M6).
- В `IMPROVEMENT_PLAN_2026-10.md` проставить `[x]` по фактическому статусу.

---

## Порядок исполнения (волны)

| Волна | Содержание | Срок | Эффект |
|---|---|---|---|
| **1. Деньги** | M1–M7, затем M8–M11 | 1–1,5 недели | балансы и долги клиентов верны; автосверка не «теряет» деньги |
| **2. Логистика и агент** | L1, L2, A1–A3, A5, затем L3–L5, A4 | 1 неделя | статусы авто через один путь; агент не исполняет команды из писем |
| **3. Скорость** | S1–S5, затем S6–S9 | 1 неделя | нет зависаний воркеров; тяжёлые страницы < 300 мс |
| **4. Чистка и тесты** | D1–D4, затем D5–D6 | 2–3 дня | тесты изолированы, нет мёртвого и сломанного кода |
| **5. Каркас** | K1, K3, K4, K5, затем K2, K6–K8 | 2 недели | одна формула цены, явные каскады, меньше файлов-гигантов |

Волну 4 (D1–D4) можно делать параллельно с любой другой — она мелкая и
не трогает бизнес-логику. Внутри волны 1 перед M2/M4/M6 — отчёт по проду
(`--dry-run`): сколько записей затронуто, нужна ли миграция данных.

Критерий перехода между волнами: все пункты волны `[x]`, `pytest` зелёный,
прод задеплоен, в Sentry сутки без новых регрессий, запись в `CHANGELOG.md`.

## Открытые вопросы к бизнесу (решить до соответствующей волны)

1. **Кредит-нота (M2):** как она должна выглядеть для клиента и бухгалтера —
   отдельный документ «минус к долгу» или только закрытие исходного счёта?
   Нужен ли пуш KRE в site.pro?
   **Решено 2026-10-28:** только закрытие исходного счёта — KRE не входит в
   долг клиента и не становится OVERDUE. Пуш KRE в site.pro — решим позже,
   сейчас не трогаем.
2. **Переплата и платёж по уже оплаченному счёту (M4):** зачислять на аванс
   клиента (TOPUP) автоматически или оставлять на ручной разбор?
   **Решено 2026-10-28:** автоматически TOPUP на аванс клиента + пометка в
   `reconciliation_note`.
3. **Часовой пояс (M11):** переводим `TIME_ZONE` на `Europe/Vilnius`? Это
   изменит времена в логах и расписание beat (сейчас в UTC).
   **Решено 2026-10-28:** да, `Europe/Vilnius`, расписание beat пересмотреть.
4. **Выключение услуги в каталоге (M7):** должно ли оно вообще трогать
   существующие машины, или только новые?
   **Решено 2026-10-28:** убирать только у не переданных машин без счетов
   или со счетами в DRAFT.
5. **Момент TRANSFERRED в автовозе (L2, бывший B12):** при LOADED или при
   фактическом отъезде; какую дату ставить `transfer_date`.
6. **Документы задним числом (L6):** подтвердить допустимость генерации
   платёжек и «писем USA» со случайными прошлыми датами.

## Что НЕ входит

- Продуктовые темы (онлайн-оплата в портале, PWA/push, мульти-склад).
- Остатки старого roadmap: H5b (CAPTCHA), H5c (CSP).
- Расширение AI-агента на денежные операции.
