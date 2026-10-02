# План улучшений Logist2 — октябрь 2026

> Бриф для работы с агентом: открываем «начинаем работу», говорим «работаем по
> `docs/IMPROVEMENT_PLAN_2026-10.md`, волна N» — агент идёт по пунктам волны.
> Аналог `docs/ROADMAP_2026-05_high_medium.md`, но фокус другой: **бизнес-логика,
> наглядность, удобство, UX, производительность** (инфраструктура/безопасность/
> архитектура уже закрыты в `docs/AUDIT_ROUND3.md` — не повторяем).

Составлен 2026-10-02 по результатам: чтения кода (4 параллельных аудита —
админка, бизнес-логика, производительность, клиентский портал), замеров через
Django test client на свежей копии прод-БД и прогона `pytest` / `ruff`.

## Статус исполнения (2026-10-02, вечер)

Сделано и покрыто тестами (951 passed до интеграции; полный прогон после
миграции `0034` — в коммите). Миграция `0034_improvement_plan_2026_10`:
`CarStatusHistory`, `ClientUser.notification_prefs`, `Warehouse.storage_day_policy`,
индексы P9, типы уведомлений.

**Не сделано** (исполнители оборвались на лимите модели):

- **B7** — кредит-нота как рабочий процесс (`BillingService.create_credit_note`).
- **U1** — единая email-панель вместо четырёх копий.
- **U2** — пилот HTMX (фильтры, inline-статус, монитор). Скрипт HTMX по-прежнему
  подключён, атрибутов `hx-*` нет.

Остальные пункты B1–B6, B8–B14, Q1–Q12, V1–V9, U3–U9, C1–C9, P1–P11
реализованы в коде и тестах `core/tests/test_wave_*.py`. Чек-боксы ниже
остаются `[ ]` как журнал исходного плана; источник правды по факту — этот блок.

## Правила работы по плану

1. **Один коммит = один пункт** (исключение — «быстрые победы» волны 0, их можно
   группировать по 2–3 родственных).
2. Для каждого пункта: код → `pytest` → `ruff check . ; ruff format <новые файлы>`
   → `python manage.py makemigrations --check --dry-run` → коммит → push →
   `deploy.ps1` (если применимо) → `[x]` здесь + запись в `CHANGELOG.md`.
3. Деньги (раздел B) — только через `BillingService`, с тестом на сценарий,
   без широких `except Exception`. Перед миграциями данных — `--dry-run`.
4. Наглядность/UX — браузерную проверку не запускаем по умолчанию; достаточно
   программного смоука. Пользователь сам смотрит и говорит, что не так.
5. Производительность — сначала замер «до» (скрипт ниже), потом «после»; число
   запросов фиксируем в `core/tests/test_query_budgets.py`.

---

## 0. Исходное состояние (baseline, 2026-10-02)

### Данные (прод-дамп)

| Сущность | Кол-во | Комментарий |
|---|---|---|
| Авто | 1 086 | 1 043 TRANSFERRED, **43 UNLOADED** — активный рабочий набор маленький |
| Контейнеры | 283 | 15 UNLOADED |
| Инвойсы | 390 | 342 PAID, 33 DRAFT, 9 OVERDUE, 6 CANCELLED; FACT 249 / PARDP 103 / AVBLC 33 / KRE 5 |
| Транзакции | 474 | |
| Банковские операции | 510 | **76 несверенных** (в апреле 2026 было 28 — покрытие сверки падает) |
| Клиенты | 109 | 7 с доступом в портал |
| Фото контейнеров | 19 801 | |
| Заявки на автовоз | 43 | 472 документа |
| Логи мониторинга | `uptime_check` 43 682, `system_metric` 8 736, `agentrun` 1 919, `notificationlog` 1 757 | растут без ретеншена |
| БД | 80 МБ | |

Вывод: производительность сейчас — не про объёмы, а про **латентность конкретных
страниц** (N+1, тяжёлые аннотации, синхронные внешние вызовы) и про **рост
служебных таблиц**.

### Замеры страниц (Django test client, холодный кэш, локальный PG)

| Страница | SQL | SQL мс | Всего мс | HTML КБ | Проблема |
|---|---|---|---|---|---|
| `/admin/dashboard/` | 38 | 206 | **1 017** | 137 | тяжёлый запрос по `core_transaction` (93 мс) |
| `/admin/core/car/` | 8 | 50 | 190 | 176 | ок |
| `/admin/core/car/<pk>/change/` | 20 | 39 | 165 | 201 | ок |
| `/admin/core/container/` | 6 | 238 | 290 | 100 | один запрос списка **225 мс** (4×Count + prefetch cars) |
| `/admin/core/container/<pk>/change/` | 26 | 42 | 217 | 230 | ок |
| `/admin/core/newinvoice/` | 6 | 116 | 215 | 155 | запрос списка 114 мс |
| `/admin/core/newinvoice/<pk>/change/` | 38 | 34 | 109 | 103 | **×18** одинаковых `ExpenseCategory` |
| `/admin/core/transaction/` | 5 | 119 | 190 | 119 | запрос списка 117 мс |
| `/admin/core/transportrequest/` | **50** | 40 | 129 | 103 | **×43** `COUNT(*)` по машинам заявки |
| `/admin/requests/` | 26 | 38 | 85 | 75 | ×7 `TransportDeclarationGroup` |
| `/admin/reconciliation/` | **197** | 105 | 400 | **601** | **×170** `CarService` N+1, гигантский HTML |
| `/admin/tasks-board/` | 11 | 45 | 109 | 288 | большой HTML |
| `/admin/system-monitor/` | 7 | 804 | **10 686** | 65 | `psutil.cpu_percent(interval=…)` + `pg_database_size` (локально; на проде перепроверить) |
| Прод снаружи: `/`, `/admin/login/`, `/health/` | | | 68–123 | | nginx+gunicorn отвечают быстро |

Скрипт замера (вне репо, воспроизводим при необходимости): логин суперпользователем
через `django.test.Client`, `connections["default"].execute_wrapper` для подсчёта
запросов, `cache.clear()` перед каждым URL.

### Качество на `master`

- `pytest`: **799 passed, 1 failed** (`core/tests/test_cmr.py::test_blank_has_exactly_three_line_weights`
  — в бланке CMR нет CSS-переменных `--base/--bold/--rule`), 4 skipped.
- `ruff check .`: **11 замечаний** (RUF005 ×5, I001 ×3, UP037, RUF023, RUF100) — все autofix.
- `makemigrations --check`: **есть незафиксированное изменение** —
  `TransportRequestDocument.doc_type` (label «Паспорт» → «Паспорт / ID-карта»,
  миграция `0033` не создана).
- Итого: **CI на master красный** по трём джобам (lint, django-checks, test).

---

## Волна 0. Быстрые победы (1–2 дня)

Всё — S по трудоёмкости, без изменения бизнес-поведения.

- [x] **Q1. Починить CI.** `ruff check . --fix`; создать миграцию `0033_alter_transportrequestdocument_doc_type`;
      разобраться с `test_cmr` (либо вернуть CSS-переменные толщины линий в
      `templates/.../cmr` бланк, либо обновить тест, если дизайн менялся осознанно).
      DoD: `pytest` зелёный, `ruff` чистый, `makemigrations --check` пустой.
      → Сделано 2026-10-02 (`d027909`): бланк CMR осознанно перешёл на px и в печати
      (комментарий в `cmr_editor.html:470–479`) — тест обновлён; попутно `ruff format`
      15 файлов, которые уже не проходили `format --check`. 800 passed.
- [ ] **Q2. `ContainerAdmin.get_queryset`** — убрать `prefetch_related("container_cars")`
      из списка (`core/admin/container.py:195–218`): `list_display` машины не использует,
      а prefetch тянет сотни объектов. DoD: запрос списка < 100 мс, бюджет в
      `test_query_budgets.py`.
- [ ] **Q3. `TransportRequestAdmin`** — N+1 `cars.count()` на строку (43 COUNT):
      аннотация `Count("cars")` в `get_queryset`. DoD: ≤ 10 запросов на changelist.
- [ ] **Q4. Форма инвойса** — 18 одинаковых запросов `ExpenseCategory` (виджет/choices
      категории в `core/admin/billing/invoice_forms.py` или `invoice_display.py`):
      один запрос + кэш на время запроса. DoD: ≤ 20 запросов на change-форму.
- [ ] **Q5. `/admin/reconciliation/`** — `ReconciliationService.get_cost_confirmation_status`
      (`core/services/reconciliation_service.py:50–54`) делает 2–3 запроса на каждую
      услугу → один `values("car_service_id").annotate(Sum, ArrayAgg)`. DoD: ≤ 30 запросов.
- [ ] **Q6. Подписанные фото** — добавить `Cache-Control: private, max-age=<TTL подписи>`
      в `serve_signed_photo` (`core/views_website/signed_photos.py:~333`). Сейчас каждое
      повторное открытие галереи снова идёт через Django.
- [ ] **Q7. gzip для HTML/JSON** в `scripts/nginx_logist2.conf` (сейчас gzip только
      для `/static/` в `nginx_caromoto.conf`). Админские списки по 150–600 КБ едут несжатыми.
- [ ] **Q8. WebSocket-шторм** — `group_send` уходит на **каждый** `Car.save`, включая
      фоновые UPDATE денормализованных полей (`car_lifecycle_service.py:43–66`,
      `signals/car.py:200–204`). Не слать при `_bulk_updating` / `update_fields` только
      denorm-полей; дебаунс по `car_id` 1–2 с.
- [ ] **Q9. Chart.js** — убрать CDN-дубли (`company_dashboard.html:1100–1101`,
      `expense_analytics.html:444`), везде `{% static 'js/chart.umd.min.js' %}`.
- [ ] **Q10. Кабинет клиента: дефолтный фильтр** (`core/views_website/client_portal.py:95–97`)
      прячет FLOATING — клиент, у которого всё «в море», видит пустой кабинет. Дефолт —
      все активные статусы (FLOATING/IN_PORT/UNLOADED) + чипы «В пути / На складе / Все».
- [ ] **Q11. i18n карточек** — `car_detail.html` (строки 60, 75–252) и
      `container_detail.html` (весь файл) без `{% trans %}`; CTA на главной
      (`home.html:200–204`) и тексты трекинга в JS (`home.html:247–362`,
      `toLocaleDateString('ru-RU')`) — хардкод по-русски. Обернуть, обновить `.po`.
- [ ] **Q12. Ретеншен служебных таблиц** — beat-задача чистки `UptimeCheck` (> 30 дней),
      `SystemMetric` (> 90 дней), `AgentRun` (> 90 дней), `NotificationLog` (> 180 дней).

---

## 1. Бизнес-логика (раздел B)

Главная тема: **мутабельность уже выставленных денег** и **каскады задним числом**.

### B1. Регенерация позиций только для DRAFT (High, M)
- Где: `core/mixins.py:41` — `REGENERATABLE_INVOICE_STATUSES = ("DRAFT", *OPEN_INVOICE_STATUSES)`;
  используется в `signals/car_service.py:162–170`, `tasks.py:1286–1295`,
  `container_lifecycle_service.py:143–146`, `models/billing.py:783–792`.
- Суть: смена услуги, `unload_date`, тарифа или состава автовоза переписывает позиции
  и `total` у ISSUED / OVERDUE / PARTIALLY_PAID. Клиент держит один PDF, в системе
  (и в site.pro) — другая сумма.
- Решение: автоматический реген — только `DRAFT`. Для ISSUED+ — явная кнопка
  «Пересоздать позиции (force)» с подтверждением и записью в `LogEntry`, либо
  кредит-нота (B5). В UI инвойса показывать бейдж «позиции расходятся с услугами»
  вместо тихой перезаписи.
- DoD: тесты «CarService изменён → ISSUED-инвойс не тронут, DRAFT пересобран»;
  `regenerate_items_from_cars()` без `force` на ISSUED пишет warning и возвращает False.

### B2. После регена пересчитывать оплату (High, S)
- Где: `core/models/billing.py:883–885` — после пересборки только `calculate_totals()`
  + `save(update_fields=["subtotal","total"])`, без `recalculate_paid_amount()` /
  `update_status()`.
- Решение: вызывать `recalculate_paid_amount()`; запрет регена при `paid_amount > 0`
  без `force`. Делать вместе с B1.
- DoD: тест «PARTIALLY_PAID, total уменьшился ниже paid → статус PAID / переплата
  видна», баланс клиента сходится (`verify_balances`).

### B3. Каскад статуса контейнера → авто через FSM (High, S)
- Где: `core/services/container_lifecycle_service.py:35–44` —
  `container.container_cars.update(status=container.status)`.
- Суть: откат контейнера (UNLOADED → IN_PORT) сбрасывает уже TRANSFERRED авто
  → снова «идёт хранение», ломаются инвойсы.
- Решение: как в `CarAdmin._bulk_set_status` — фильтр по `ALLOWED_STATUS_TRANSITIONS`,
  TRANSFERRED не трогать при откате, остальным — только разрешённые переходы;
  пропущенные авто — в сообщение пользователю.
- DoD: тест на смешанный контейнер (часть TRANSFERRED).

### B4. Дни хранения: календарные vs рабочие (High, M)
- Где: `core/models/cars.py:339–353` — `(end − unload).days + 1 − free_days`,
  `is_business_day` существует только в transport-docs.
- Решение: поле `Warehouse.storage_day_policy` (CALENDAR / BUSINESS_DAYS /
  BUSINESS_DAYS_LT_HOLIDAYS), `get_storage_days()` учитывает политику;
  дефолт — текущее поведение (миграция без изменения данных).
- DoD: тесты на выходные/праздники; `recalculate_storage --dry-run` показывает
  разницу перед включением политики на складе.

### B5. Смена клиента на авто ↔ инвойсы (High, M)
- Где: `core/services/car_admin_service.py:273–305` — тариф и наценка
  пересчитываются, `NewInvoice.recipient_client` старого инвойса не меняется;
  реген может переписать чужой инвойс.
- Решение: при смене `client` у авто, входящего в открытый инвойс, — либо
  отвязать авто от DRAFT и создать новый DRAFT новому клиенту, либо запретить
  смену, если инвойс ISSUED+ (ValidationError с подсказкой «сначала кредит-нота»).
- DoD: тесты на оба пути.

### B6. Бейдж «Важное» + смена статуса (Medium, S)
- Где: `core/models/cars.py:640–644` — комментарий обещает откат статуса, в коде `pass`.
- Решение: `self.status = old_status` + `messages.warning` в админке (или ValidationError).
- DoD: тест «снятие галочки и смена статуса одним save → статус не изменился».

### B7. Кредит-ноты как рабочий процесс (Medium, L)
- Где: `core/models/billing.py:107, 730–732`; `docs/accounting_session_handoff.md:209`.
- Суть: тип KRE есть (5 шт.), но нет связи с исходным PARDP, влияния на баланс,
  push/PDF в site.pro — возвраты живут вне системы.
- Решение: `BillingService.create_credit_note(invoice, amount|items, reason)` →
  KRE с `linked_invoice`, корректировка `paid_amount`/статуса исходного, запись
  в леджер (ADJUSTMENT), опционально push в site.pro.
- DoD: тесты полный/частичный KRE; кнопка «Кредит-нота» в карточке PARDP.

### B8. Просрочки: напоминания и эскалация (Medium, M)
- Где: `core/tasks.py:32–48` — `check_overdue_invoices` только ставит статус.
- Решение: email/Telegram клиенту за 3 дня до `due_date` и при OVERDUE (с
  дедупом через `NotificationLog`); дело (`Task`) менеджеру при OVERDUE > 14 дней;
  блок «Просрочки» в утреннем дайджесте агента. Пени — отдельно, только при
  наличии договорной ставки (флаг на Client).
- DoD: тесты на расписание и дедуп; шаблоны писем lt/en/ru.

### B9. Один банковский платёж → несколько инвойсов (Medium, L)
- Где: `core/management/commands/auto_reconcile.py:108–162`,
  `core/services/billing_service.py:1099–1219` — 1 BT → 1 инвойс, частичные только
  при `|diff| ≤ 1 €`.
- Суть: 76 несверенных операций; «хвост» переплаты не разносится, объединённые
  платежи оптовиков — руками.
- Решение: страница «Разнести платёж» (BT → список открытых инвойсов клиента с
  чекбоксами и суммами, остаток → BALANCE_TOPUP); авто-правило 4 — жадное покрытие
  открытых инвойсов одного клиента по дате. Колонка «почему не сматчилось»
  (нет номера / сумма не совпала / неизвестный контрагент / не-EUR).
- DoD: тесты на разнесение и остаток; покрытие сверки на проде ≥ 95 %.

### B10. FSM для `TransportRequest` (Medium, M)
- Где: `core/models/website.py:850–857` — choices есть, переходов/гейтов нет.
- Решение: `ALLOWED_TRANSITIONS` + проверки: SUBMITTED требует ≥ 1 авто и
  подпись/паспорт; COMPLETED — наличие автовоза и полного пакета документов.
  Доска `/admin/requests/` и портал показывают только разрешённые кнопки.
- DoD: тесты на недопустимые прыжки; UI не предлагает запрещённых переходов.

### B11. Freeze для TRANSFERRED при изменениях контейнера задним числом (Medium, M)
- Где: `core/models/containers.py:261–299`, `container_lifecycle_service.apply_ths_change`.
- Решение: смена склада/THS/линии применяется только к не-TRANSFERRED авто; для
  переданных — только с явным `force` (admin action «Применить и к переданным»).
- DoD: тест «смена склада у контейнера с переданными авто не трогает их историю».

### B12. Момент TRANSFERRED в автовозе (Medium, M)
- Где: `core/signals/autotransport.py:78–93` — все авто → TRANSFERRED уже на LOADED.
- Решение: обсудить с бизнесом: если машины физически уезжают при LOADED — оставить,
  но `transfer_date` брать из `AutoTransport.loading_date`, а не `today`; если нет —
  переносить на `DELIVERED`/фактический отъезд. Зафиксировать в
  `accounting-context.mdc`.

### B13. Авто без контейнера (loose car) (Medium, M)
- Где: `car_service_manager.apply_client_tariffs_for_container`, `signals/car.py`.
- Решение: явный сценарий `sync_car_services(car)` для авто без контейнера
  (тарифы клиента, хранение, без THS) + тест «FORMED с loose car → полный инвойс».

### B14. Единый `ReconciliationService` и объяснимость (Low→Medium, L)
- Входящая/исходящая сверка живут в разных местах с разными допусками
  (±1 € / ±0.02 €). Свести в один сервис с конфигурируемыми допусками, одним
  набором правил и журналом решений (`reconciliation_note` уже есть — заполнять
  причиной автоматически). Делать после B9.

---

## 2. Наглядность (раздел V — «что видно с первого взгляда»)

### V1. Цветовая эскалация дней хранения (High, S)
- Где: `core/admin/car.py:1067–1075` — «15 (из 20)» как обычное число.
- Решение: бейдж: зелёный в пределах `free_days`, жёлтый 1–7 платных дней,
  красный > 7; tooltip со ставкой и накопленной суммой. Та же шкала — в карточке
  контейнера и в кабинете клиента.

### V2. ETA и просроченный ETA у контейнеров (High, S)
- Контейнеры FLOATING: колонка «ETA» с countdown («через 3 дн.» / «просрочен на 2 дн.»
  красным). Фильтр «ETA просрочен». Источник — `update_container_etas_task` уже есть.

### V3. Сводная строка над списком авто (Medium, S)
- Над changelist авто: «На складе: N · платных дней > 7: K · без тайтла: M ·
  непрочитанных писем: E» (частично есть в `car/change_list.html` — усилить и
  сделать кликабельными фильтрами).

### V4. Live-обновление ячеек, а не только подсветка (Medium, S)
- Где: `core/static/.../live_updates.js:4–7, 104–116` — WS только пульсирует строку.
- Решение: обновлять `status`, `days`, `total_price` из payload (docstring это уже
  обещает). Делать после Q8 (дебаунс).

### V5. Инвойс: колонка «сигналы» (Medium, S)
- В списке инвойсов одна колонка с иконками: вложение есть/нет, аудит OK/расхождения,
  связан (`linked_invoice`), отправлен в site.pro, «позиции расходятся с услугами» (из B1).

### V6. Дашборд: касса и личные карты рядом с банком (Medium, S)
- `company_dashboard.html` — добавить виджет «Касса сегодня» (Company.balance) и
  остатки `PersonalCard` рядом с балансами банка; плитку «Несверено: N операций на
  X €» сделать кнопкой на `/admin/core/banktransaction/?reconciled=no`.

### V7. Прогресс комплекта документов в заявке (Medium, S)
- На карточке `/admin/requests/<pk>/` и в портале — прогресс-бар «7 из 9 документов»
  с перечнем недостающих (данные уже есть в facts).

### V8. Reconciliation: липкая сводка (Low, S)
- Sticky-шапка «ещё не разобрано: N услуг на X €» при скролле таблицы; HTML 600 КБ
  уменьшить пагинацией по контейнерам/поставщикам.

### V9. Счётчики в sidebar (Low, S)
- У «Авто»/«Контейнеры» — бейдж непрочитанных писем; у «Банк» — несверенных;
  у «Инвойсы» — OVERDUE. Один кэшируемый запрос на 60 с.

---

## 3. Удобство и UX админки (раздел U)

### U1. Единая email-панель (High, L)
- Где: `templates/admin/core/container/_emails_panel.html` (~2 100 строк),
  `car/_emails_panel.html` (682), `autotransport/_emails_panel.html` (709),
  `requests/_emails_panel.html` (229) — четыре копии с разным UX.
- Решение: один include `admin/_email_thread.html` + один JS-модуль
  (`email_thread.js`), параметризованные типом объекта; composer уже вынесен в
  `_composer.html` — продолжить. Делать поэтапно: сначала car → container.
- DoD: один файл шаблона, баги правятся в одном месте, smoke на 4 страницах.

### U2. HTMX: внедрить или убрать (High, M)
- Факт: `htmx` подключён в `Media` (`core/admin/car.py:1407`, `container.py:165`,
  `client_change.html:6`), но **ни одного `hx-*` атрибута в проекте нет**;
  `system_monitor.py:5` упоминает htmx, а `system_monitor.html:294` делает `setInterval+fetch`.
- Решение (рекомендуется внедрить, т.к. это дешёвый путь к U3–U5): пилот на
  трёх местах — фильтры changelist авто без перезагрузки, inline-смена статуса
  контейнера, авто-обновление системного монитора. Если пилот не приживётся —
  удалить скрипт и поправить docs.

### U3. Карточка авто: layout без тройного костыля (High, M)
- Где: `templates/admin/core/car/change_form.html:19–65` (inline CSS `:has()`),
  `core/admin/car.py:540–561` (widget `style=`), `car_form_layout.js` (JS fallback).
- Решение: CSS Grid по классам полей в `dashboard_admin.css`, без `!important`
  и `setProperty`. Попутно убрать перестройку DOM changelist через JS в
  `base_site.html:57–200` (U6).

### U4. Меню «Финансы» (Medium, S)
- Где: `logist2/admin_site.py:96–113` — 3 модели + 7 кастомных ссылок вперемешку.
- Решение: подгруппы «Документы» (инвойсы, транзакции), «Банк и сверка»
  (банк, reconciliation, аудит счетов), «Касса и карты» (касса, личные карты),
  «Аналитика» (расходы, сравнение). Доску дел дополнить ссылкой «Все дела (список)»
  на скрытый `TaskAdmin`.

### U5. Быстрые действия с места (Medium, M)
- Changelist авто: кнопка «Разгружено сегодня» в строке (HTMX POST) вместо
  bulk-dropdown; карточка контейнера: смена статуса inline; банк: «Привязать»
  прямо из строки с автокомплитом инвойса.

### U6. Каркас без runtime-мутаций DOM (Medium, M)
- `templates/admin/base_site.html:57–200` пересобирает toolbar changelist через JS
  с `setProperty(..., 'important')`. Перенести в CSS-классы на `#changelist`.

### U7. Общая дизайн-система кастомных страниц (Medium, M)
- `company_dashboard.html`, `tasks_board.html`, `reconciliation_dashboard.html`,
  `expense_analytics.html` держат по 100–450 строк своих `<style>`. Вынести `.kpi`,
  `.tbl-card`, tabs, бейджи статусов (сейчас inline в `car.py:934–940`,
  `container.py:453–458`, `tasks.py:180–208`) в `admin_custom_pages.css` /
  `dashboard_admin.css` как `.cm-badge--status-*`.

### U8. Форма инвойса: дожать вынос jQuery (Low, M)
- `templates/admin/core/newinvoice/change_form.html:982–1376` (~400 строк inline JS)
  → `logist2_invoice_admin.js`; emoji в fieldsets (`billing/invoice.py:124–184`) →
  иконки как в sidebar.

### U9. Мобильная доска заявок (Low, S)
- `requests_board.html:41–44` — `minmax(420px, 1fr)` даёт горизонтальный скролл на
  телефоне → `minmax(min(100%, 420px), 1fr)`.

---

## 4. Клиентский портал и сайт (раздел C)

### C1. Таймлайн статусов в трекинге и карточках (High, M)
- Где: `templates/website/home.html:281–348` — плоский alert; ETA в API контейнера
  есть (`serializers_website.py:151–164`), в UI не показывается.
- Решение: визуальный таймлайн FLOATING → IN_PORT → UNLOADED → TRANSFERRED с датами
  и ETA, одинаковый компонент для публичного трекинга, `car_detail`, `container_detail`.

### C2. История статусов (Medium, L)
- Нет журнала смен статуса ни в модели, ни в API. Лёгкий вариант —
  `CarStatusHistory(car, status, changed_at, source)` заполняется из
  `CarLifecycleService`; показывать в таймлайне (C1) и в карточке авто админки.

### C3. Контейнеры на дашборде клиента (High, M)
- `client_portal.py:46–150` — только авто. Добавить вкладку/блок «Мои контейнеры»
  с ETA, статусом, числом авто и ссылкой.

### C4. Восстановление пароля (High, M)
- `core/urls_website.py:30–32`, `login.html:47–53` — нет `PasswordReset*`.
  Стандартные Django-вьюхи + шаблоны писем lt/en/ru + ссылка «Забыли пароль?».
  Регистрация создаёт пустой `Client` — добавить онбординг-баннер «кабинет
  активируется менеджером» и уведомление менеджеру (Task).

### C5. Уведомления о событиях, которых нет (High, L)
- Сейчас: PLANNED, UNLOADED, CAR_UNLOADED, сообщение по заявке.
- Добавить (email + Telegram, с дедупом через `NotificationLog`): «фото готовы»
  (при первой публичной фото контейнера/авто), «инвойс выставлен» (ISSUED PARDP с
  PDF), «просрочка» (из B8), «заявка сменила статус», «ETA изменился > 2 дней»,
  «авто передано». Настройки подписки в профиле клиента (`ClientUser`).

### C6. Цены в карточке авто (Medium, S)
- `car_detail.html:207–230` — `$` рядом с EUR на дашборде, внутренние поля без
  пояснений. Единая валюта EUR, подписи («хранение по тарифу склада»), скрыть
  внутренние поля.

### C7. Фото: lazy + thumbnails в карточке авто (Medium, S)
- `car_detail.html:86–89` грузит оригиналы без `loading="lazy"`; контейнер уже
  использует thumbnails и lightbox — унифицировать.

### C8. SEO и доступность (Medium, S)
- `base.html:8–10` — нет Open Graph / Twitter Card / canonical / JSON-LD
  (`Organization`, `NewsArticle`); meta description не per-page; футер «2024»;
  `navbar-toggler` без `aria-label`/`aria-controls` (`base.html:33–35`).

### C9. Пагинация и лёгкость страницы заявок (Medium, M)
- `core/views_website/portal_transport.py:65–86, 97+` — все заявки клиента с
  prefetch всего в память, шаблон ~2 000 строк. Пагинация / ленивые вкладки,
  `unread` через annotate, inline-стили → `portal.css`.

### C10. Публичный API отдаёт адрес склада по VIN (Low, решение бизнеса)
- `serializers_website.py:82–100` — любой, знающий VIN, видит адрес склада.
  Либо осознанно оставить, либо показывать только город.

---

## 5. Производительность (раздел P)

Помимо волны 0 (Q2–Q9):

### P1. Фото через nginx `X-Accel-Redirect` (High, M)
- Где: `core/views_website/signed_photos.py:266–333`, `photos_authed.py:27–65` —
  `FileResponse` из Django на каждое превью; галерея на 100 фото = 100
  gunicorn-запросов. Это же — пункт H5c roadmap-2026-05 (`docs/PUBLIC_ENDPOINTS.md` §4.1).
- Решение: `location /media/photos/ { internal; }` + Django проверяет подпись и
  отдаёт `X-Accel-Redirect`. Плюс Q6 (Cache-Control).

### P2. Один путь пересчёта цены авто (High, M)
- Где: `core/signals/car_service.py:45–70` (sync `calculate_total_price` в
  `on_commit`) + `signals/car.py:197–198` (Celery `recalculate_cars_total_price_task`).
- Суть: при сохранении карточки авто цена считается дважды (sync + async).
- Решение: оставить Celery + thread-local дедуп; sync-путь только когда брокер
  недоступен (как уже сделано для регена инвойсов).

### P3. Changelist авто: email-бейджи (High, M)
- Где: `core/admin/car.py:419–443` — `Count(email_links)` + `Count(email_links__email)`
  с JOIN/DISTINCT на каждую страницу.
- Решение: `Exists`/Subquery вместо Count, либо денормализованный
  `unread_email_count` на Car/Container (обновляется сигналом `CarEmailLink`).

### P4. Дашборд холодный 1 с (Medium, M)
- `dashboard_service.py` — 38 запросов, самый тяжёлый по `core_transaction` (93 мс)
  и дальше суммы в Python. Перевести на агрегаты `values().annotate`; кэш уже есть —
  греть его beat-задачей раз в 5 мин, чтобы первый пользователь утром не ждал.

### P5. GDrive-синхронизация фото (Medium, L)
- `core/google_drive_sync.py:471–513` — download + `photo.save()` по одному,
  контейнер на 100 фото занимает worker надолго (отсюда и были дубли 2026-09-29).
- Решение: ограниченный пул загрузок (4–6), `bulk_create` метаданных, thumbnails
  отдельной задачей.

### P6. Ежедневный пересчёт хранения (Medium, M)
- `core/tasks.py:1446–1464` — все UNLOADED в задачи по 500. Сейчас их 43, но код
  пересчитывает `calculate_total_price` каждой. Фильтр «только с `unload_date`», и
  дни/стоимость хранения — одним SQL-UPDATE там, где ставка фиксирована.

### P7. ETA контейнеров и финализация передачи (Medium, S–M)
- `tasks.py:1028–1040` — ETA по одному HTTP последовательно → `group`/пул.
- `tasks.py:1430–1442` — после bulk transfer N отдельных regen одних и тех же
  инвойсов → собрать уникальные `invoice_ids`, регенить один раз.

### P8. `find_discrepancies` и `expense_analytics.get_top_items` (Medium, M)
- `comparison_service.py:408–444` — цикл по клиентам/складам с aggregate на каждом;
  `expense_analytics_service.py:113–141` — весь JSON чеков в Python. Агрегаты в SQL
  (`values().annotate`, JSONB) + кэш как у AI-insights.

### P9. Индексы (Low, S — после EXPLAIN на проде)
- `Car(status, unload_date)` partial `WHERE status='UNLOADED'`; `Car(client, unload_date)`;
  `BankTransaction` partial по «несверено»; `TransportRequest(client, status, -created_at)`.

### P10. Кэш (Low, S)
- `Company.get_default()` — кэшировать объект (сейчас только id); `{% cache %}` для
  навбара/новостей сайта; счётчики вкладок портала на 30–60 с.

### P11. Системный монитор (Low, S)
- `core/services/system_monitor.py:61` — `psutil.cpu_percent(interval=0.3)` и
  `pg_database_size` на каждый HTTP-запрос. Снимок брать из последнего
  `SystemMetric` (beat уже пишет каждые 5 мин), live-замер — только по кнопке.

---

## 6. Тесты под бизнес-сценарии (раздел T)

Покрытие ядра есть (FSM, lifecycle, billing partial pay, bank match, transport docs).
Не покрыто — именно то, где найдены риски:

- [ ] T1. Реген при ISSUED / PARTIALLY_PAID (B1, B2).
- [ ] T2. Каскад статуса контейнера на смешанный набор авто (B3).
- [ ] T3. `get_storage_days` — выходные/праздники, смена склада в середине хранения,
      авто без склада/контейнера (B4, B13).
- [ ] T4. Смена клиента у авто с открытым инвойсом (B5).
- [ ] T5. Смена `default_price` в каталоге → позиции ISSUED не дрейфуют (B1/B4-каталог).
- [ ] T6. Кредит-нота: полная/частичная, баланс (B7).
- [ ] T7. Один BT → несколько инвойсов; остаток в TOPUP; `bank_amount > remaining` (B9).
- [ ] T8. `TransportRequest` — недопустимые переходы, COMPLETED без документов (B10).
- [ ] T9. `is_important` + смена статуса одним save (B6).
- [ ] T10. Query-budget: `/admin/core/container/`, `/admin/core/transportrequest/`,
      `/admin/reconciliation/`, форма инвойса (Q2–Q5).

---

## Порядок исполнения (волны)

| Волна | Содержание | Срок | Эффект |
|---|---|---|---|
| **0** | Q1–Q12 | 1–2 дня | зелёный CI, −80 % запросов на 4 страницах, кабинет не пустой |
| **1. Деньги** | B1, B2, B3, B6, B5 + T1, T2, T4, T9 | 1 неделя | выставленные счета больше не мутируют тихо |
| **2. Видимость** | V1, V2, V5, V6, V7, P2, P3, Q8→V4 | 1 неделя | логист видит просрочки/ETA/дни без кликов; админка быстрее |
| **3. Клиент** | C1, C3, C4, C5 (часть: фото, инвойс), C6–C8, P1 | 1–2 недели | клиент узнаёт о событиях сам; фото через nginx |
| **4. Процессы** | B4, B8, B9, B10, B11, B12, B13 + T3, T5, T7, T8 | 2 недели | хранение по договору, напоминания, сверка ≥ 95 % |
| **5. Каркас** | U1, U2, U3, U4, U6, U7 | 2 недели | один email-UI, HTMX-пилот, меньше inline CSS/JS |
| **6. Хвосты** | B7, B14, C2, C9, P4–P11, U5, U8, U9, V3, V8, V9 | по мере | |

Критерий перехода между волнами: все пункты волны `[x]`, `pytest` зелёный,
прод задеплоен, в Sentry сутки без новых регрессий, запись в `CHANGELOG.md`.

## Открытые вопросы к бизнесу (решить до волны 4)

1. Хранение считается по календарным или рабочим дням и на каких складах (B4)?
2. Когда авто считается переданным — при LOADED автовоза или при фактическом
   отъезде (B12)?
3. Пени за просрочку — есть ли договорная ставка и для каких клиентов (B8)?
4. Адрес склада в публичном трекинге по VIN — оставить (C10)?
5. HTMX — внедряем (пилот в волне 5) или убираем (U2)?

## Что НЕ входит

- Инфраструктура, безопасность, архитектурные сплиты — закрыты в
  `docs/AUDIT_ROUND3.md` и `docs/ROADMAP_2026-05_high_medium.md`; из того roadmap
  остались H5b (CAPTCHA) и H5c (CSP; `X-Accel` для фото здесь — P1).
- Расширение AI-агента на денежные операции (`docs/AI_AGENT_PLAN.md`, фаза 5) —
  только read-only алерты в дайджесте (через B8).
- Онлайн-оплата инвойсов в портале, PWA/push, мульти-склад — продуктовые темы
  следующего цикла.
