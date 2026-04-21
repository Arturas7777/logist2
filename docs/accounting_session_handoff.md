# Accounting Cleanup — Handoff для нового диалога

> Дата: 2026-04-21. Версия: сессия 3 (завершён блок 12.12).
>
> **Назначение:** этот файл — самодостаточный контекст для продолжения работы по чистке бухгалтерии в новом диалоге. Читать от начала до конца.

---

## ⚡ QUICK START для новой сессии

### Что читать в первую очередь
1. **Этот файл** (секции 0, 1, 12.12, 13) — контекст + последнее состояние + TODO
2. **`.cursor/rules/accounting-context.mdc`** — постоянные правила проекта, модели, админка, site.pro API
3. **`.cursor/rules/project-overview.mdc`** — общий обзор проекта

### Главные бизнес-правила (формулировка пользователя 2026-04-21)

| Тип | Серия | direction | Транзакция | Attachment |
|---|---|---|:---:|:---:|
| `INVOICE_FACT` | FACT-XXX | INCOMING | ✅ обязательна | ✅ обязателен |
| `PROFORMA` | AV-XXX | OUTGOING | ❌ НЕ должна быть | (не критично) |
| `INVOICE` | PARDP-XXX | OUTGOING | ✅ обязательна | ✅ обязателен (PDF с site.pro) |

Проверка соблюдения: `scripts/debug/_business_rules_audit.py`.

### Текущее состояние (на 2026-04-21)

**Финансы:**
- BankTransaction: ~410 шт, покрытие сверки >99%
- PAYMENT: 334 шт, INVOICE_FACT: 244, INVOICE (PARDP): 103
- Расходы компании 236K€, главная категория «Логистика» (220K€)

**Нарушения бизнес-правил (осталось):**
| Правило | Нарушений | Статус |
|---|---:|---|
| FACT без транзакции | 1 | FACT-000002 Atlantic 340€ — свежий ISSUED, ждёт оплаты (норма) |
| FACT без файла | 41 | Все объяснимы: 16 госплатежей + 3 Cursor + 11 Revolut no-export + старые 2024 |
| AV с транзакцией | 1 | AV-000032 Смердов 590€ — оставлено по решению пользователя |
| PARDP без транзакции | 6 | ⚠ OVERDUE молдавские (6 300€) — **ждёт решения менеджера** |
| PARDP сумма не сходится | 2 | PARTIALLY_PAID (CAZACU 1025€, PRUTEAN 60€) |
| PARDP без файла | 0 | ✅ |

### Следующая задача: 6 OVERDUE PARDP

Все 6 — молдавские клиенты, пакетные проформы от 2026-02-03/04, без машин в CRM, без платежей, 0€ баланса. Похоже на «мёртвые» проформы. Пользователь уточняет у менеджера. Варианты действий (ждёт ответа):
- Отменить все 6 (CANCELLED)
- Отменить все 6 + синхронизировать с site.pro (cancel там тоже)
- Отменить только дубли DOVAGRUP (2×1020€)
- Держать OVERDUE

Скрипт для отмены — `scripts/debug/_cancel_overdue_pardp.py` (ещё не создан, шаблон в `_cancel_duplicates.py`).

### Незакрытые «большие» задачи
- **Клиентские долги**: ~18 590€ open invoices (15 клиентов, секция B в оригинальном аудите)
- **Старые AV/AVBLC/FACT/INCBLC без клиента**: ~47 000€ (секция C)
- **Cursor receipts** (3 шт): ручное скачивание с Cursor dashboard (Revolut API не берёт)
- **CREDIT_NOTE (KRE)** 5 шт без attachment: созданы локально, в site.pro не sync'ились
- **Старые AV** 14 шт без attachment: до появления site.pro-интеграции

### Последние важные скрипты

| Скрипт | Назначение |
|---|---|
| `_business_rules_audit.py` | **Главный** — проверка 3 бизнес-правил |
| `_missing_num_and_pdf.py` | Список инвойсов без номера контрагента / без файла |
| `_ocr_stage1b_pdfplumber.py` | OCR текстовых PDF (regex) |
| `_ocr_stage2_vision.py` | OCR скан-PDF/JPG (Claude Vision) |
| `_sitepro_download_pdfs.py` | Массовая выгрузка PDF с site.pro (AV/PARDP/INCBLC) |
| `_mass_import_expenses.py` | Массовый импорт Revolut expenses → FACT + PAYMENT |
| `_sync_attachments.py` | Треугольник BT ↔ Tx ↔ Invoice (раскладка файлов) |
| `_restore_pardp103.py` | Пример восстановления потерянной связи site.pro |

### Как запускать скрипты

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe manage.py shell --command "exec(open('scripts/debug/_ИМЯ.py',encoding='utf-8').read())"
```

Для apply-режима многих скриптов: `$env:APPLY='1'` перед вызовом.

### Актуальные константы

- `settings.COMPANY_NAME = 'Caromoto Lithuania, MB'` (**с запятой и MB** — критично для `NewInvoice.direction`)
- `Company.get_default_id()` должен возвращать валидный id (не None). Кэш-ключ: `company:default_id`.
- `ExpenseCategory #18` — «Логистика» (OPERATIONAL), главная для себестоимости перевозок
- `ExpenseCategory #17` — «Залоги и гарантии» (OTHER)
- `Client id=12` = CAROMOTO MOLDOVA (оптовик MD)
- `Client id=4` = CAROMOTO BELARUS (оптовик BY, через Daniel Soltys)

---

## 0. Контекст проекта

Django-проект **logist2** для логистической компании **Caromoto Lithuania, MB** (перевозка авто из портов в Молдову / Беларусь / Польшу).

Три параллельных источника финансовых данных:
1. **Django** (эта система) — первоисточник для CRM / операционной работы
2. **Revolut Business API** (auto-sync) — расчётный счёт EUR + USD + GBP
3. **Paysera** (XLSX-импорт) — два счёта: `5441` (main) и `5445` (card)
4. **site.pro** (API-sync) — внешний бухгалтерский сервис, куда уходят официальные инвойсы (PARDP / AV / FACT / KRE / NUR)

Цель всей работы (формулировка пользователя): «разгребсти эту долбаную бухгалтерию и все расставить по полочкам. И балансы с клиентами привести к реальности для дальнейшего правильного учета».

---

## 1. Ключевые бизнес-модели

### 1.1 CAROMOTO MOLDOVA (Client id=12) — оптовик
- У них есть **свои клиенты** (физлица в Молдове), которых они просят переводить деньги напрямую на наш счёт
- Деньги физлиц идут на **баланс CAROMOTO MOLDOVA** (не на их собственный долг)
- Мы выставляем автовозы как счёт на CAROMOTO MOLDOVA и списываем из их баланса
- Иногда мы выставляем `PARDP` физлицам (когда они сами просят) — это "управленческий" учёт
- Связь помечается через поле `Transaction.related_client` (→ id=12)

### 1.2 CAROMOTO BELARUS (Client id=4) — второй оптовик
- Работает через посредника **Daniel Soltys** (обычное физлицо), который делает переводы на наш счёт
- Ранее был дубль `"Caromoto-Bel", OOO` (id=123) — **слит в id=4** (18 инвойсов + 36 Tx, 161K€)
- Дубль `id=123` удалён

### 1.3 Мультивалютность
- Инвойсы могут быть в **EUR, USD, GBP** (поле `NewInvoice.currency`)
- Transaction тоже имеет `currency`
- USD-платежи Moldova-клиентов идут на Revolut USD, потом конвертируются в EUR пачками через `EXCHANGE`
- В XLSX Moldova писал **EUR-эквивалент после конвертации**, в Django — **USD номинал**

### 1.4 Наличные
- `method='CASH'` в Transaction
- Необязательно отражаются в Django — пользователь сказал что 3050€ наличных от Moldova "unofficial" (не проводим)

---

## 2. Ключевые IDs

### Clients
| ID | Name | Роль |
|---|---|---|
| **12** | CAROMOTO MOLDOVA | Оптовик, `related_client` для 128 Tx |
| **4** | CAROMOTO BELARUS | Оптовик |
| **123** | ~~Caromoto-Bel OOO~~ | УДАЛЁН (слит в id=4) |
| 80 | PRUTEAN EUGENIU | Молдова-физлицо |
| 81 | OLEINICENCO SERGHEI | Молдова-физлицо |
| 82 | LUNGU ECATERINA | Молдова-физлицо (муж платил — Andrei) |
| 83 | PAVALOI IULIAN | Молдова-физлицо (недоплата 163€ покрыта с баланса CM) |
| 87 | PLAUDIS AIGARS | KRE-000005 — 380€ |
| 92 | PINTEA DIONISIE | KRE-000004 — 1030€ |
| 99 | TATARENCO ANDREI | KRE-000003 — 1275€ |
| 100 | PINZARU CRISTIAN | USD |
| 101 | CATRINESCU IGOR | USD |
| 103 | DATILUXCONS SRL | USD |
| 106 | VALI PACALO | KRE-000002 — 510€ |
| 113 | CUCULEANU DANIEL-NICOLAE | KRE-000001 — 300€ + balance 1€ (advance) |
| 114 | FRUNZE GHEORGHI | USD |

### BankConnections
| ID | Bank | Описание |
|---|---|---|
| **1** | REVOLUT | Revolut Business (EUR/USD/GBP) |
| **2** | PAYSERA | Paysera 5441 (main) |
| **3** | PAYSERA | Paysera 5445 (card) |

### BankAccounts (Revolut)
| ID | Name | Currency | Balance |
|---|---|---|---|
| 1 | Main | EUR | 4020.80€ |
| 2 | Main | GBP | 0.00 |
| 3 | Main | USD | 0.00 |
| 4 | PLAIS collection | EUR | 0.00 |

---

## 3. Архитектурные решения (принятые и применённые)

### 3.1 Transaction.related_client (новое поле)
- Миграция `0160_transaction_related_client`
- `ForeignKey('Client', null=True, related_name='related_transactions')`
- Назначение: когда физлицо платит за оптовика — Tx помечается `related_client=<оптовик>` для управленческой отчётности
- **НЕ влияет на balance клиента**, только на теневую аналитику
- Применено: 128 Tx с `related_client=12` (CAROMOTO MOLDOVA), теневой оборот **75 728€**

### 3.2 KRE — Credit Notes (новая серия)
- Миграция `0161_add_credit_note_type`
- `NewInvoice.document_type = 'CREDIT_NOTE'` → префикс `KRE`
- Назначение: возврат продажи, когда PARDP был выписан, но клиент не заплатил (бухгалтер закрывает через KRE в site.pro)
- Механика в Django:
  - Оригинальный `PARDP` → `status=CANCELLED`, `paid_amount=0`
  - Новый `KRE` → `status=PAID`, `paid_amount=total`
  - Клиент `balance=0`, `open_invoices_debt=0`
- Создано: KRE-000001 ... KRE-000005 (5 штук)
- Добавлен бейдж в `core/admin_billing.py:812` (красный)
- **TODO (не сделано):** site.pro синк KRE при следующем импорте

### 3.3 Правило пары TOPUP + PAYMENT
Каноническая запись клиентского платежа в Django:
1. `Transaction(type='BALANCE_TOPUP', to_client=<client>, amount=X)` — пополнение баланса
2. `Transaction(type='PAYMENT', from_client=<client>, to_company=<company>, invoice=<inv>, amount=Y)` — списание на инвойс

Если X == Y → инвойс PAID, balance=0. Если X > Y → advance на balance.
Если X < Y → частичное закрытие, `PARTIALLY_PAID`.

### 3.4 ExpenseCategory
- Добавлена категория **"Залоги и гарантии"** (id=17, type=OTHER)
- Категория **"Топливо"** (id=8, type=OTHER) уже существовала

---

## 4. Что уже сделано (полный список)

### Техническая реконсиляция
- [x] Sync Revolut API / Paysera XLSX / site.pro API — всё подтянуто
- [x] **76 BT без Transaction** → создано 152 Tx (TOPUP+PAYMENT пар), 227 705€ реконсилировано
- [x] Слияние дубля Belarus (id=123 → id=4)
- [x] Cazacu Sergiu (id=76) → balance 0 (+5€ TOPUP с `related_client=12`)

### Блоки разбора (все применены)
- [x] **Блок 1** — 12 Tx (6 BT × 2) помечены `related_client=CM`: BT #53, #51, #52, #415, #411, #54
- [x] **Блок 2** — Pavaloi (BT#349 1037€ + 163€ с CM), Lungu (BT#345 1280€ муж→жена)
- [x] **Блок 3** — Frunze (BT#211 total 1375.96→1375.00 + TOPUP/PAYMENT), Cuculeanu (BT#207 +1€ advance)
- [x] **Блок 4** — Alauša BT#6 (−60.01€), Orlen BT#27 (−46.52€) помечены `reconciliation_skipped=True` (топливо)
- [x] **Блок 5** — Frolov 535€ подтверждён (правильная сумма)
- [x] **Блок 6** — 7 из 8 найдены:
  - BT#344 Oleinicenco Serghei (PARDP-000065)
  - BT#338 Prutean Eugeniu (PARDP-000066 PARTIAL, долг 60€)
  - BT#56 Surcov Roman — дотегирован
  - BT#270 Diulgher, #253 Datilux, #246 Catrinescu, #293 Pinzaru — уже были tagged
- [x] **Блок 7** — 9 AV-проформ Moldova (13 665€) → `CANCELLED` (были внутренним учётом)
- [x] **Блок 9** — ExpenseCategory "Залоги и гарантии" (id=17)

### Мультивалюта
- [x] 10 USD-Tx (5 TOPUP + 5 PAYMENT) приведены к `currency=USD`
- [x] В description добавлены EUR-эквиваленты по курсам Revolut
- [x] Инвойсы PARDP-000027/042/044/045/055 — в USD, с корректными балансами

### Серия KRE
- [x] Модель + миграция + админка + 5 документов KRE-000001..005

### Отменено по решению пользователя (не проводим)
- [x] **3050€ наличные** (unofficial, без плательщика/даты)
- [x] **NUR** (курсовые разницы в site.pro) — не трогаем

---

## 5. Что осталось (OPEN)

### 5.1 Блок 8 — 29 205€ недостающих счетов на Moldova
**Контекст:** В XLSX Moldova указаны 22+ автовоза (TRAL-101, 119-143, K8 контейнер −11 167€, PIESE VADIM/DRAGOS), за которые Moldova нам должна. В Django для них **нет PARDP-инвойсов**.

**Решение пользователя:** "В django не вёлся строгий учёт, поэтому многих счетов там может и не быть". Требуется решение **с бухгалтером**:
- (а) Создать 20+ PARDP задним числом на каждый автовоз
- (б) Оставить только в XLSX (не заводить в Django)
- (в) Один сводный PARDP на 29 205€

**Статус:** ждёт консультации с бухгалтером.

### 5.2 22 Orphan BankTransactions
Это BT без `matched_transaction` и без `reconciliation_skipped`. Нужен скрипт для их финального разбора. Запуск:
```bash
.venv\Scripts\python.exe manage.py shell --command "from core.models_banking import BankTransaction; qs = BankTransaction.objects.filter(matched_transaction__isnull=True, reconciliation_skipped=False); [print(f'BT#{bt.id} {bt.created_at.date()} {bt.amount} {bt.currency} cp={bt.counterparty_name!r} descr={(bt.description or \"\")[:60]!r}') for bt in qs]"
```

### 5.3 Chitoroag Vasilie 1080€
В XLSX Moldova помечен зелёным (получено), VIN `WBA13AG07PCL98367`. **В Django нет BT с этим именем или суммой.** Возможно:
- Другой банк (не Paysera 5441/5445, не Revolut)
- Наличные, забыл пометить
- Неправильное имя плательщика

**Нужно:** спросить пользователя и/или проверить у Moldova.

### 5.4 site.pro синк серии KRE
Новая серия `CREDIT_NOTE`/`KRE` добавлена в Django, но site.pro-integration пока не знает про неё. При следующей синхронизации надо:
- Проверить что KRE подтягиваются из site.pro (или пушатся туда)
- Искать в `core/services/` или `core/management/commands/` файлы с `sitepro` в имени

### 5.5 Полноценный учёт топлива
Сейчас BT#6 (Alauša) и BT#27 (Orlen) просто `skipped`. Для полноценного учёта нужно:
- Создать `Counterparty`/`Company` для Alauša и Orlen
- Создать `NewInvoice document_type='INVOICE_FACT'` на каждый заправочный BT
- Создать `Transaction type='PAYMENT' method='CARD'` с `expense_category_id=8` (Топливо)

Отдельная большая задача — наверняка там десятки BT за весь период.

### 5.6 Расхождения XLSX vs Django по суммам USD-платежей
В XLSX Moldova пользователь писал суммы в EUR (пост-конверсия), в Django — в USD (номинал). Это **НЕ ошибка**, разница объяснена:
- Diulgher $1400 vs XLSX €1190 = рейт 0.85
- Datilux $1725 vs XLSX €1460 = рейт 0.85
- Catrinescu $1455 vs XLSX €1220 = рейт 0.84
- Pinzaru $1375 vs XLSX €1165 = рейт 0.85
- Surcov $1425 vs XLSX €1452 = (Surcov в EUR был, там разница 27€ — возможно комиссия)

Для XLSX-аналитики эта разница нормальна, в Django должно быть `currency=USD`.

---

## 6. Текущее состояние данных

### CAROMOTO MOLDOVA (id=12)
```
balance            = -163.00 EUR (от недоплаты Pavaloi BT#349)
open_invoices_debt = 0.00
total_balance      = -163.00
Tx with related_client=12: 128 записей
Теневой оборот: 75 728€ (sum(amount) где type=PAYMENT)
```

### Документы NewInvoice по типам
| Тип | Префикс | Кол-во |
|---|---|---|
| INVOICE | PARDP | 103 (из них 5 CANCELLED через KRE, 9 AV тоже CANCELLED — wait, AV — отдельный тип) |
| PROFORMA | AV | 20 (из них 9 CANCELLED) |
| PROFORMA_BLC | AVBLC | 6 |
| INVOICE_FACT | FACT | 14 |
| INVOICE_INCBLC | INCBLC | 2 |
| **CREDIT_NOTE** | **KRE** | **5** (новая серия) |

### BankTransactions
- Всего: **430**
- Matched: 105
- Skipped: 303
- Orphaned: **22** (TODO: разобрать)

### USD Transactions
- 10 штук (5 пар TOPUP + PAYMENT)
- TOPUP = $7330, PAYMENT = $7330 (balance 0)

---

## 7. Инструменты и структура

### Django management commands (важные)
- `manage.py auto_reconcile` — автоматическое сопоставление BT ↔ NewInvoice
- `manage.py load_all_revolut` — принудительная подгрузка Revolut
- `manage.py makemigrations core` / `migrate core`

### Скрипты из этой сессии (в `scripts/debug/`)
Все скрипты поддерживают `--apply` флаг (без него — dry-run):
- `_block1_tag.py` — тегирование 6 BT
- `_block2_fix.py` — Pavaloi + Lungu
- `_block3_fix.py` — Frunze + Cuculeanu
- `_block6_fix.py` — Oleinicenco + Prutean + Surcov
- `_blocks_7_4.py` — AV-отмены + топливо
- `_create_kre.py` — KRE-документы
- `_fix_usd_tx.py` — USD-валюта
- `_final_summary.py` — итоговая сводка
- `_moldova_summary.py` — сводка CM
- `_cm_xlsx_analyze.py` — анализ XLSX Moldova
- `_block6_deep_search.py` — поиск по XLSX Paysera
- `_block6_amount_scan.py` — поиск BT по сумме

Запуск:
```bash
.venv\Scripts\python.exe manage.py shell --command "exec(open('scripts/debug/<name>.py', encoding='utf-8').read())"
```

С apply:
```bash
.venv\Scripts\python.exe manage.py shell --command "import sys; sys.argv=['','--apply']; exec(open('scripts/debug/<name>.py', encoding='utf-8').read())"
```

### Файлы-источники (на диске пользователя)
- `C:\Users\art-f\OneDrive\Загрузки\EVP1110016765441_2023-01-01_2026-04-21.xlsx` — Paysera 5441 (42 строки, **неполный** экспорт)
- `C:\Users\art-f\OneDrive\Загрузки\EVP1310017825445_2023-01-01_2026-04-21.xlsx` — Paysera 5445 (33 строки)
- `C:\Users\art-f\OneDrive\Загрузки\UAB CAROMOTO.xlsx` — управленческая таблица Moldova (автовозы, плательщики, зелёные = confirmed)
- `C:\Users\art-f\OneDrive\Загрузки\account-statement_01-Jan-2025_31-Dec-2025 (1).csv` — Revolut USD за 2025 (9 транзакций)

### Модели (ссылки)
- `core/models_billing.py:1238` — Transaction (TYPE_CHOICES, METHOD_CHOICES)
- `core/models_billing.py:209` — NewInvoice.DOCUMENT_TYPE_CHOICES
- `core/models_billing.py:198` — NewInvoice.STATUS_CHOICES
- `core/models_billing.py:1411` — Transaction.related_client (новое поле)
- `core/admin_billing.py:810` — doc_type_badge (KRE-бейдж)
- `core/models.py:232` — Client.open_invoices_debt (property)
- `core/services/balance_manager.py:30` — BalanceManager.recalculate_entity_balance

### Вспомогательные
- `docs/accounting_cleanup_2026.md` — старый чеклист (обновлён, статусы проставлены)
- `docs/accounting_moldova_markup.md` — разметка пользователя CM-клиентов
- `.cursor/rules/accounting-context.mdc` — правила для AI (стоит обновить — см. 5.7 ниже)

### 5.7 Обновить accounting-context.mdc
Добавить в правило:
- Поле `Transaction.related_client` — когда использовать
- Серия KRE (`CREDIT_NOTE` → префикс `KRE`) — для возвратов
- Мультивалютность Transaction (currency обязательно!)
- Правила CAROMOTO MOLDOVA / BELARUS / Daniel Soltys
- Правило пары TOPUP + PAYMENT

---

## 8. Правила учёта (итоговые)

### Когда клиент платит за себя (обычный случай)
1. BankTransaction импортирован
2. `auto_reconcile` находит совпадение с NewInvoice
3. Создаются 2 Transaction: TOPUP + PAYMENT
4. `BankTransaction.matched_transaction = <PAYMENT>`

### Когда клиент платит за оптовика (CM, Belarus)
1. То же самое
2. **ОБЕ Transaction получают `related_client=<оптовик_id>`**
3. Оптовик сохраняет свой баланс в `balance` (через обычные Tx на его имя)
4. Физлицо видит свой закрытый инвойс

### Когда клиент платит за другого физлица (муж-жена)
1. TOPUP на **фактического получателя услуги** (жену)
2. PAYMENT от жены на её инвойс
3. В description пометка "перевод от мужа <имя>"

### Когда инвойс не оплачен и закрывается кредитной нотой
1. PARDP → `status=CANCELLED`, `paid_amount=0`
2. Создаётся KRE-документ на ту же сумму
3. Клиент `balance=0`

### Когда мультивалютный платёж
1. BankTransaction с `currency=<USD/GBP>`
2. Transactions создаются в **той же валюте**, что и BT
3. Инвойс может быть в любой валюте (обычно та же, что BT)
4. Для EUR-отчётности в `description` — эквивалент по курсу обмена

### Когда это расход компании (топливо, аренда и т.п.)
1. BankTransaction с отрицательной суммой
2. Либо `reconciliation_skipped=True` + `reconciliation_note`
3. Либо полноценно: `NewInvoice INVOICE_FACT` + `Transaction PAYMENT CARD` с `expense_category`

---

## 9. Как стартовать новый диалог

**Первое сообщение в новом диалоге:**

> Привет! Мы продолжаем чистку бухгалтерии в проекте logist2. Прочти `docs/accounting_session_handoff.md` — там полный контекст, что сделано и что осталось. Сейчас хочу заняться: **[укажи что]**.

Варианты что можно делать дальше (по приоритету):

1. **22 orphan BankTransactions** — финальный разбор (см. 5.2)
2. **Chitoroag Vasilie 1080€** — выяснить канал получения (см. 5.3)
3. **Блок 8 — 29 205€ Moldova** — после консультации с бухгалтером (см. 5.1)
4. **Обновить `.cursor/rules/accounting-context.mdc`** (см. 5.7)
5. **site.pro синк KRE** (см. 5.4)
6. **Полноценный учёт топлива через INVOICE_FACT** (см. 5.5)

---

## 10. Критически важно для нового диалога

- **НЕ создавай новые поля/миграции без явной просьбы**
- **Все изменения данных — сначала dry-run, потом `--apply`**
- **Balance пересчитывать через `BalanceManager.recalculate_entity_balance(client)`**
- **paid_amount инвойса — через `inv.recalculate_paid_amount()`**
- **Номера Transaction — автогенерация формата `TRX-YYYYMMDD-NNNNN`** (см. `next_num()` в скриптах)
- **При конфликте dry-run выходных и apply — сначала читать код, потом обновлять**
- **Все скрипты пиши в `scripts/debug/_<name>.py`, UTF-8**
- **`description` на NewInvoice не существует — используй `notes`**
- **На Transaction — поле `description` (TextField)**
- **Поле expense-категории на `Transaction` называется `category` (не `expense_category`)**
- **В `BankTransaction` нет полей `bank_connection_id`/`account` — используй `connection_id` (и `account_id` отсутствует как отдельное поле)**

---

## 11. Ключевой контекст из оригинального диалога (восстановлено в сессии 3)

### 11.1 Caromoto Lithuania — НЕ-НДС-плательщик
Важный бизнес-факт, влияющий на все решения по бухгалтерии:
- Компания **не является НДС-плательщиком** и **не подаёт инвойсы в налоговую ежемесячно**.
- Следствие: можно гибко манипулировать инвойсами (`CANCELLED`, `KRE`, пересборка) без фискальных последствий.
- `site.pro` хранит инвойсы для бухгалтерского/клиентского учёта, не для фискальной отчётности.
- Это оправдывает подход "исправить историю" (перевыставить на CM вместо физлица и т.д.) — делать можно.

### 11.2 Бизнес-логика CAROMOTO MOLDOVA (точная цитата пользователя)
> «Мы собираем и отправляем автовоз и выставляем CAROMOTO MOLDOVA общий счёт за этот автовоз. Их клиенты делают переводы на **произвольные суммы** (которые скажет им Caromoto Moldova) на наш счёт. Эти деньги поступают на **баланс CM** в нашей компании. Уже из этого баланса мы вычитаем суммы за автовоз или, если денег нет, увеличиваем их долг.
>
> Иногда это просто **рандомный клиент**, которого Caromoto Moldova попросил сделать перевод на наш счёт. Иногда **VIN, который их клиент указывает в назначении платежа — это машина, которой мы вообще не занимались**, не имеем к ней отношения.»

**Вывод для учёта:**
- Назначение платежа (VIN) физлица-отправителя — **не критерий** для поиска инвойса.
- Сумма тоже произвольная (не совпадает с total инвойса).
- Правильный путь: все такие Tx помечать `related_client=12`, деньги на balance CM (через TOPUP на имя CM, либо на имя физлица + теневая метка).

### 11.3 Семантика таблицы XLSX `UAB CAROMOTO.xlsx` (пользователь)
- **Зелёная заливка** строки → средства **получены** (подтверждено владельцем)
- **Сумма с минусом** → это **наш счёт им** (= PARDP на авто, долг CM)
- **Сумма без минуса** → это **их оплата** (= BT от физлица-плательщика)
- **Строка в самом низу** → актуальный **баланс CM** по версии владельца
- Во многих строках указана фамилия отправителя (плательщик), которую мы матчим к BT

### 11.4 USD-платежи Moldova-клиентов
- В XLSX владелец писал **EUR-эквивалент после конвертации**, в Django — **USD номинал**. Разница ~15% — это не расхождение, а разные представления.
- Конвертация USD→EUR происходит пачками через Revolut EXCHANGE, курсы 0.84–0.85 в 2024–2025.

### 11.5 Chitoroag Vasilie 1080€ — нерешённый вопрос
- В XLSX Moldova зелёный (получено), VIN `WBA13AG07PCL98367`.
- В Django нет BT ни с этим именем, ни с этой суммой (Paysera 5441/5445, Revolut).
- Варианты: другой банк, наличные без пометки, неправильное имя плательщика.
- **Статус:** ждёт выяснения у Moldova/владельца.

---

## 12. Сессия 3 (2026-04-21 продолжение) — текущий прогресс

### 12.1 Сделано в сессии 3

**Orphan BankTransactions: 22 → 10** (минус 12 штук):

1. **A1 — TARP SĄSKAITŲ (5 штук)** — внутренние переводы Revolut ↔ Paysera-5441 ↔ Paysera-5445. У каждого парная сторона уже была `reconciliation_skipped=True`, не хватало такой же пометки на встречной. Помечены `skipped=True` с нотой о парном BT.
   - BT#116 (Paysera→Revolut 440€) ↔ BT#392
   - BT#372 (5441→5445 10€) ↔ BT#375
   - BT#376 (Revolut→5441 30€) ↔ BT#190
   - BT#356 (Revolut→5441 30€) ↔ BT#305
   - BT#350 (Revolut→5441 60€) ↔ BT#19

2. **A6.1 — BT#329 Ruslan Tofan 1085€ → PARDP-000073** (клиент #73). Инвойс уже был PAID, но без Transactions-цепочки. Создана пара (TOPUP 1085, PAYMENT 1080), `balance Tofan = +5€ advance`.

3. **A6.2 — BT#424 Ilya Frolov 535€ → PARDP-000103** (клиент #126). Инвойс был `ISSUED` с `total=0`. Выставлен `total=subtotal=535`, создана пара Tx, инвойс → `PAID`, `balance=0`.

4. **A3.1 — AVN Paslaugos пара (BT#193 / BT#234)** — залог за закрытие T1 (VW 3VWEM7BU2NM014664). Создана `Company #19 AVN Paslaugos, MB`, `FACT-000015` (external `AVNL25 000010`, 3000€ ISSUED → PAID). Tx#279 PAYMENT (Caromoto→AVN, category #17 Залоги) + Tx#280 REFUND (AVN→Caromoto, без инвойса). BT#193 → matched PAYMENT+FACT, BT#234 → matched REFUND.

5. **A4 — 3 «тёмных» BT оформлены через FACT+PAYMENT+category** (все счета расшифрованы через Revolut receipts API + OCR pdfplumber / приложенный пользователем PDF):
   - **BT#4 −405.35€ 2026-02-04** `26/00022` — **UAB Westtransit** (аренда + коммун. + электроэнергия январь 2026, Mainų g. 31). FACT-000016 (items: 300+10+25, total 405.35 с НДС 21%). Tx#281 PAYMENT + category #2 Аренда.
   - **BT#9 −29€ 2026-01-21** `22759` — **Grometa, UAB** (создана Company #20, код 300632711): тонер Brother TN-2510XL. FACT-000017 (external `GROME26 0020463`, 29€ с НДС). Tx#282 PAYMENT + category #15 Техника.
   - **BT#262 −161.37€ 2025-09-05** `ES09286261` — **Kesko Senukai Digital, UAB** (создана Company #21, код 303686899): принтер Brother DCP-L2620DW. FACT-000018 (external `2KD0003318273`, 161.37€ с НДС). Tx#283 PAYMENT + category #15 Техника.

**Технические заметки:**
- `revolut_service.RevolutService._download_receipt(bt, expense_id, receipt_id)` можно дёргать принудительно (см. `_dark_redownload.py`). Это полезно если `receipt_file` ссылается на удалённый файл.
- OCR чеков через `pdfplumber` работает отлично для стандартных литовских PVM sąskaita-faktūra PDF.
- **JPG-чеки читаются через `Read` tool (vision) в Claude** — Tesseract не требуется. Все 4 топливных чека распознаны автоматически.
- Впервые в базе появился тип `REFUND` (Tx#280) и `PAYMENT + category` на банковский расход (Tx#281,282,283) — до этого был только `ADJUSTMENT/CASH` для личных расходов.

6. **Массовое обогащение банковских расходов (аудит + автофикс)** — 24 исходящих BT приведены к единому стандарту:
   - Re-download 6 receipts через Revolut API (BT#426, #425, #45, #39, #36, #10)
   - Прикреплено **14 attachments** к FACT-инвойсам (automatic via `_auto_enrich_expenses.py`)
   - OCR PDF-чеков (pdfplumber) + JPG (Claude vision) → **заполнено 7 external_number** (BT#421 Maersk, BT#413/414 Revolut, BT#36/419 EMSI, BT#423 Alauša, BT#425 Jozita)
   - Проставлена `category=#8 Топливо` для 3 Tx (топливо EMSI/Alauša/Jozita)
   - Создан FACT-000019 для orphan BT#36 EMSI (63€, дизель)
   - Создан FACT-000020 для orphan BT#10 Printera (40€, картридж TN2510)
   - Прикреплён user-provided PDF к FACT-000018 Kesko Senukai (BT#262)

**Открытие: Revolut-инвойсы.** BT#413 (8€ People on expenses) и BT#414 (35€ Grow plan fee) — **это части одного фискального Invoice #3577611 Revolut Bank UAB на 43€**. В нашей базе они как 2 разных FACT. В будущем стоит объединить FACT-000013+FACT-000014 в один.

### 12.2 Остаток — 8 orphan BT + 3 BT без PDF по группам
Пользователь выбрал подход **«полноценный учёт всех»** (FACT + Tx).

**Аудит состояния всех 24 исходящих BT (на конец сессии 3):**
- Полный комплект (Tx + FACT + ext_num + PDF): **18 / 24 (75%)**
- Без PDF: 3 (BT#193 AVN залог — банк-перевод без чека; BT#420/422 Cursor — нет receipt в Revolut API)
- Без ext_number: 0
- Orphan (нет Tx): 3 (BT#393 Arturas 12€, BT#201 Logispace 2050€, BT#199 Jozita 60€)

**Скрипт аудита:** `scripts/debug/_audit_bank_expenses.py`

**A2 — Владелец Arturas Haizhutsis (3 BT, ~620€ in + −12€)**
- BT#400 +300 EUR (Paysera-5441, «Papildymas perlo terminale» — пополнение через Perlas terminal)
- BT#399 +320 EUR (Paysera-5445, «Lėšų pervedimas pagal Paysera tarpininkavimo sutartį»)
- BT#393 −12.26 EUR (Paysera-5445, без описания — возможно комиссия Paysera)
- Сценарий: внесение наличных директора в кассу компании через Paysera-терминалы

**A3 — Залоги / таможенные гарантии (ОСТАЁТСЯ 1 BT)**
- BT#201 −2050 EUR 2025-06-17 «Užstatas reeksporto procedūrai. Auto CHEVROLET 2GNAXKEV5K6238038»
- Получатель: **Logispace, UAB (Company #14, уже есть)** — склад, получил залог за re-export Chevrolet.
- Статус: залог **ещё не вернулся** (висит в дебиторской задолженности Logispace).
- Модель учёта (утверждена пользователем): **FACT на выход + PAYMENT category=17, возврат будет отдельным REFUND Tx без инвойса.**
- Для BT#201 — только этап "выход": FACT от Logispace на 2050€, PAYMENT Caromoto→Logispace, `category=17`. Возврат оформим когда придёт.
- Парный пример уже реализован: BT#193/234 (AVN) — см. 12.1.4.

**A4 — Карточные расходы бизнеса (ОСТАЁТСЯ 1 BT)**
- BT#199 −60.03 «Jozita» — похоже на АЗС-заправку (как BT#425), но чек Revolut не скачал (нет expense_id). Можно оформить как топливо по аналогии, но без чека.
- *(BT#4, BT#9, BT#10, BT#36, BT#262, BT#419, BT#423, BT#425 — все оформлены в 12.1.5, 12.1.6)*

**A5 — Рефанды/возвраты (3 BT, +717€)**
- BT#141 +467 «UAB OTRAS permokos grąžinimas 24OTR-0950» (**OTRAS — нет в Company**)
- BT#35 +250 «Transfer to Uab Westimport refund» (**Westimport — нет в Company**)
- BT#2 +0.32 «Revolut Company Basic plan partial usage refund» (Revolut UAB уже есть)

**A2 — Владелец Arturas Haizhutsis (3 BT, ~620€ in + −12€)** — без изменений.

### 12.3 Дополнительные открытия сессии 3

**Существующие Company (на 2026-04-21, сессия 3):**
- #1 Caromoto Lithuania (наша)
- #7 Тестовая компания
- #8 WestTransit, UAB, #9 Alauša, UAB, #10 Emsi, UAB, #11 ORLEN Baltics Retail, #12 Inchcape Auto, #13 Atlantic Express, #14 Logispace, #15 Vilties spindulelis, #16 Jozita, #17 Cursor, #18 Revolut
- **#19 AVN Paslaugos, MB** (создано в 12.1.4)
- **#20 Grometa, UAB** (создано в 12.1.5, код 300632711)
- **#21 Kesko Senukai Digital, UAB** (создано в 12.1.5, код 303686899)
- **#22 Printera, UAB** (создано в 12.1.6, код 304506368)

**Отсутствуют в Company, нужно завести при учёте:**
- `UAB Westimport` (для A5 BT#35)
- `UAB OTRAS` (для A5 BT#141)

**Паттерны в базе (факт):**
- В Transaction с `category` сейчас **только ADJUSTMENT/CASH личные расходы** директора (Tx#64–100).
- **НИ ОДНОГО `PAYMENT + category` с банковской карты/перевода** — прокладываем новый путь.
- **Тип `REFUND` вообще не используется** (count=0) — для A5 это первое применение.

**Небольшие corrections к handoff:**
- Поле на Transaction называется `category`, не `expense_category`.
- Поле на BankTransaction — `connection_id`, не `bank_connection_id`; поля `account` / `account_id` нет.
- NewInvoice: поле `total` (не `total_amount`), клиент — `recipient_client` (не `client`), номер — `number` (не `document_number`).

### 12.4 Актуальные балансы (2026-04-21 после A1+A6)

**Клиенты с ненулевым balance:**
- #73 TOFAN RUSLAN — `+5.00` (advance после overpay BT#329)
- #113 CUCULEANU DANIEL-NICOLAE — `+1.00` (advance после KRE-000001)
- #12 CAROMOTO MOLDOVA — `-163.00` (недоплата Pavaloi BT#349)

**Клиенты с `open_invoices_debt > 0` (суммарно ~18 590€):**
ANDREI SAVANNA 2475, DOVAGRUP 2040, SURCOV 1425, MESTER 1250, COSTOV 1250, UNTILA 1245, AFANASENCO 1150, MAMTEVA/AFANASII/STRATANENCO/IURCIUC 1070 каждый, CAZACU 1025 (partial), OSTAPENCO/MARIN 1020 каждый, PRUTEAN 60 (partial).

Большинство — кандидаты на KRE (если не заплатят) или реальные ожидающиеся платежи. **Требуется решение пользователя по каждому.**

### 12.5 План продолжения

По приоритету:

1. **A3 BT#201 (Chevrolet Logispace 2050€)** — FACT от Logispace + PAYMENT category=17. Шаблон из `_a3_fix_avn_pair.py` / `_a4_fix_dark_bts.py` почти готов.
2. **A2 Arturas Haizhutsis (3 BT)** — оформить как ADJUSTMENT (внесение наличных в кассу). Скорее всего `Transaction type=ADJUSTMENT, method=CASH/TRANSFER`, без инвойсов.
3. **A4 остатки (BT#10 Printera, BT#36 EMSI, BT#199 Jozita)** — FACT + PAYMENT + category по каждому. Создать `Company Printera` (EMSI и Jozita уже есть).
4. **A5 рефанды (3 BT)** — REFUND Tx (новый паттерн, см. 12.1.4 — REFUND Tx#280 уже есть как пример). Создать `Company UAB OTRAS`, `Company UAB Westimport`.
5. **Разобрать 15 клиентских долгов (секция B отчёта)** — определить: KRE или ждём платёж.
6. **Старые AV/AVBLC/FACT/INCBLC без клиента** (~47K€ документов) — CANCELLED или закрепить клиентов.

**Ключевые сформировавшиеся паттерны (применять далее):**

| Тип операции | Tx-паттерн | Инвойс |
|---|---|---|
| Расход по карте/счёту (аренда, канцелярия, техника) | `PAYMENT, method=TRANSFER, from=Caromoto, to=поставщик, category=X` | FACT от поставщика (ISSUED → PAID после paymnt) |
| Залог (выход) | `PAYMENT, method=TRANSFER, category=17` | FACT от получателя залога |
| Залог (возврат) | `REFUND, method=TRANSFER, category=17, from=получатель, to=Caromoto` | Без инвойса (или ссылка в описании на FACT выхода) |
| Клиентский платёж | `BALANCE_TOPUP` + `PAYMENT method=BALANCE` | Существующий PARDP/AV |
| Внутренний перевод | `reconciliation_skipped=True` на обе стороны | Без инвойса |
| Личные расходы директора | `ADJUSTMENT, method=CASH, category=9/10/11/…` | Без инвойса |

### 12.6 Скрипты сессии 3 (в `scripts/debug/`)
- `_session3_status.py` — snapshot текущего состояния (orphan BT, balances, open invoices)
- `_a1_analyze.py` / `_a1_neighbors.py` — поиск пар для TARP переводов
- `_a1_fix.py` — пометка A1 как skipped (применён)
- `_a6_probe.py` — probe BT#329/#424
- `_a6_fix_tofan.py` — восстановление Tx для Tofan (применён)
- `_a6_frolov_ctx.py` / `_a6_fix_frolov.py` — Frolov (применён)
- `_dark_bt_probe.py` / `_dark_bt_full.py` — детали "тёмных" BT
- `_dark_redownload.py` — принудительный re-download Revolut чеков через API
- `_dark_pdf_text.py` — OCR скачанных PDF-чеков через pdfplumber
- `_dark_search_numbers.py` — поиск номеров из description в базе (invoices/Tx)
- `_a3_fix_avn_pair.py` — AVN пара 193/234 (применён)
- `_a4_fix_dark_bts.py` — 3 тёмных BT (4/9/262) — применён
- `_audit_bank_expenses.py` — **ключевой аудит всех исходящих BT** (5 бакетов: полный комплект / нет PDF / нет ext_num / нет invoice / orphan)
- `_audit_fixable.py` — какие из проблем можно починить автоматически
- `_auto_enrich_expenses.py` — re-download receipts + attach to invoices + OCR анализ external_number
- `_apply_ext_numbers.py` — применение OCR-derived external_number
- `_a4_fuel_receipts.py` — оформление 4 топливных BT (EMSI/Alauša/Jozita) + создание FACT для orphan BT#36
- `_finalize_two.py` — BT#10 Printera FACT + BT#262 Kesko attachment
- `_expense_pattern.py` — проверка существующих паттернов расходов
- `_check_counterparties.py` — проверка недостающих Company

### 12.7 Массовый импорт Revolut Expenses (2026-04-21, "big bang")

**Триггер:** пользователь заметил что «большая часть расходов серая и без FACT». Причина: исторически **232 из 255 BT-расходов Revolut были помечены `reconciliation_skipped=True`** без оформления.

**Входные данные:** 8 ZIP-архивов из Revolut Business Portal (Expenses export за период 2024-06-10 … 2026-04-21) = **255 строк CSV + 206 чеков** (PDF/JPEG). Распакованы в `revolut_expenses/<period>/`.

**Инструменты:**
- `_revolut_expenses_scan.py` — разбор и сопоставление CSV с BT по `external_id` (100% match)
- `_build_supplier_map.py` — анализ 47 уникальных поставщиков + предложение маппинга
- `_supplier_map.py` — модуль с маппингом `supplier → (Company name, ExpenseCategory id)`
- `_mass_import_expenses.py` — массовый импорт (идемпотентен, dry-run/apply через ENV)
- `_final_audit.py` — финальный аудит после импорта

**Решения пользователя:**
- Создать новую **ExpenseCategory #18 «Логистика»** (category_type=OPERATIONAL) для себестоимости перевозок (MSC, Neto, OTRAS, Transtrade, Atlantic, FAAS, Maersk, DHL, Logispace, MainBaltic, Sargu-Trans)
- `external_number` для авто-импорта FACT: формат `YYYY-MM-DD_<amount>` (напр. `2024-09-23_9385`)
- Подход: сначала dry-run, потом apply без фильтра

**Результат импорта (из 255 CSV):**
- **191 новых FACT + PAYMENT** (categories заполнены по маппингу)
- **18 новых Company** (NETO Terminalas, OTRAS, Transtrade Group, FAAS, DHL, Revolut Bank UAB, MainBaltic, Sargu-Trans, VSDF, VMI, Registrų centras, Viada LT, Circle K, Baltic Petroleum, Maersk, B1.lt, ORLEN, Westimport)
- **157 чеков приложено** (как receipt→invoice.attachment)
- **61 пропущено** (уже были обработаны в предыдущих подсекциях)
- **3 пропущено как SKIP_INTERNAL** (CAROMOTO LT Paysera MAIN — трансферы между нашими счетами)

**Финальное состояние (после всех сессий):**

```
Outgoing BT (amount<0): 327, sum=-255 456€
  matched_transaction:     241 ✅
  reconciliation_skipped:   85 (внутренние переводы Paysera/Revolut между счетами)
  orphan (no tx, no skip):   1 → БЫЛО 22 (BT#393 закрыт как владельческий)
  matched но без receipt:    3 (Cursor — Revolut не даёт PDF через API)

INVOICE_FACT: 240 шт, sum=240 801€
  с attachment (чеком):    204 ✅
  с external_number:       240 ✅

PAYMENT Tx: 334 шт, с category: 229
```

**Расходы компании по категориям (2024-06 … 2026-04):**

| # | Категория | Операций | Сумма |
|---|-----------|---------:|------:|
| 18 | **Логистика** | 109 | **220 681€** |
| 2 | Аренда | 17 | 6 536€ |
| 17 | Залоги и гарантии | 1 | 3 000€ |
| 8 | Топливо | 45 | 2 611€ |
| 15 | Техника | 16 | 1 232€ |
| 6 | Налоги и сборы | 12 | 1 207€ |
| 16 | Банковские услуги | 29 | 303€ |

**FACT без чека (36 шт, все объяснимы):**
- VSDF/VMI/Registrų centras — налоговые платежи в PDF не приходят
- Revolut Bank UAB — ежемесячные комиссии, чеков нет
- Cursor (3 шт) — Revolut не может скачать PDF с биллинга Stripe через API (нужно вручную с Cursor dashboard)
- AVN Paslaugos — ручная обработка депозита (пара BT#193/234)
- 8 операций DHL/Alauša/Viada/Baltic Petroleum/Transtrade/OTRAS/B1.lt — Revolut не приложил receipt в CSV (чеки остались у поставщиков)

**BT#393 -12.26€ Paysera** — переведен владельцу Arturas Haizhutsis, не расход компании → помечен skipped с note.

**TODO / оставшиеся вопросы:**
- Догрузить PDF от Cursor вручную (3 FACT) — нужен доступ к их дашборду
- Решить судьбу 85 skipped=True трансферов Paysera/Revolut (скорее всего они правильно pointing в skipped; стоит ли полным аудитом пройтись?)
- **Главное осталось незакрытым:** клиентские долги (секция B: ~18 590€ open invoices по 15 клиентам) и старые AV/AVBLC/FACT/INCBLC без клиента (~47K€)

**Скрипты сессии 3 (массовый импорт):**
- `_revolut_expenses_scan.py`
- `_build_supplier_map.py`
- `_supplier_map.py` (модуль)
- `_mass_import_expenses.py`
- `_final_audit.py`
- `_check_remaining.py`
- `_close_bt393.py`

### 12.8 Перекрёстная синхронизация вложений (2026-04-21)

**Проблема:** после массового импорта файлы были только в `NewInvoice.attachment` + `BankTransaction.receipt_file`, но не в `Transaction.attachment`. Пользователь попросил, чтобы чеки были прикреплены везде.

**Скрипт:** `_sync_attachments.py` — по треугольнику BT ↔ Tx ↔ Invoice ищет файл в любом из полей (+ в `revolut_expenses/<period>/receipt_*_expenseID=<uuid>.*`) и раскладывает во все три.

**Результат (apply):**
- `Transaction.attachment`:   0 → **203** из 241 PAYMENT
- `BankTransaction.receipt_file`: 238/241 (99%)
- `NewInvoice.attachment`:    204/240 (85%)

**38 FACT окончательно без attachment (все объяснимы):**
- 16 госплатежей Sodra/VMI/Registrų centras (PDF нет в природе)
- 3 Cursor (Stripe-биллинг, нужен Cursor dashboard вручную)
- 3 Revolut fees (нет чеков)
- 1 AVN депозит (ручная обработка)
- ~15 старых 2024 операций DHL/Alauša/Viada/Orlen/Emsi/Transtrade/OTRAS/Maersk/Logispace/Sargu-Trans/B1.lt/MSC — Revolut не приложил receipt в export CSV

### 12.9 Выгрузка PDF клиентских фактур с site.pro (2026-04-21)

**Задача:** подгрузить PDF клиентских инвойсов серии AV (INVOICE) с сайта site.pro в `NewInvoice.attachment`.

**Обнаруженный баг (ИСПРАВЛЕН 2026-04-21):** метод `SiteProService.get_invoice_pdf_url()` ссылался на несуществующую константу `self.SALE_PDF` → возвращал пустую строку для всех инвойсов. Помимо этого, метод ожидал JSON с полем `url`, хотя API отдаёт PDF напрямую в теле ответа.

**Правильный endpoint (найден методом перебора):**
```
POST /warehouse/invoices/get-sale
Body: {"id": <external_id>}
Response: application/pdf (бинарные данные PDF напрямую)
```

Константа `SALE_INVOICE_GET = '/warehouse/invoices/get-sale'` уже определена в `SiteProService`, её и нужно использовать.

**Скрипты:**
- `_sitepro_probe_pdf.py` — зондаж endpoint'ов (нашёл правильный)
- `_sitepro_download_pdfs.py` — массовая выгрузка PDF с site.pro

**Результат:**
| Тип | До | После |
|---|---:|---:|
| INVOICE (AV) | 1/103 | **103/103** ✅ |
| INVOICE_INCBLC | 2/2 | 2/2 |

**Отбросили по решению пользователя:**
- PROFORMA (PARDP): «не нужны с site.pro» — остаются 14/20 без attachment
- PROFORMA_BLC (AVBLC): аналогично

**Без attachment и скачать нельзя** (нет `SiteProInvoiceSync.external_id`):
- CREDIT_NOTE (KRE): 5 шт — созданы локально, не отправлены в site.pro
- 14 AV (старые номера AV-0000XX) — не sync'ились
- 2 AVBLC — не sync'ились

**Исправление (2026-04-21):** в `core/services/sitepro_service.py`:
- Добавлен новый метод `download_invoice_pdf(invoice) -> bytes` — делает POST на `SALE_INVOICE_GET` и возвращает бинарный PDF. Проверяет, что ответ реально начинается с `%PDF`, обновляет `SiteProInvoiceSync.sync_status = 'PDF_READY'`.
- Добавлен хелпер `save_invoice_pdf_to_attachment(invoice, overwrite=False)` — скачивает PDF и сохраняет в `NewInvoice.attachment` (filename = `{number}_sitepro.pdf`). По умолчанию не перезаписывает уже прикреплённый файл.
- Старый `get_invoice_pdf_url(invoice)` оставлен как deprecated back-compat alias: возвращает `data:application/pdf;base64,...` URL, собранный из скачанного PDF (чтобы любой старый код не упал, но плавно мигрировал).

Теперь в UI/админке можно добавить кнопку «Скачать PDF с site.pro» одним вызовом:
```python
SiteProService(connection).save_invoice_pdf_to_attachment(invoice)
```

### 12.10 Баг: `settings.COMPANY_NAME` ≠ имени в БД (2026-04-21)

**Симптом (замечено пользователем):** «все PARDP помечены как входящие, а все FACT — как внутренние».

**Корневая причина:** `settings.COMPANY_NAME = 'Caromoto Lithuania'`, но реально в БД компания называется **`'Caromoto Lithuania, MB'`** (с запятой и MB). `Company.get_default()` искал по точному `name=` → возвращал `None` → `Company.get_default_id()` возвращал `None`.

Property `NewInvoice.direction` сравнивает `issuer_company_id` / `recipient_company_id` с `default_id`. При `default_id=None` сравнения давали непредсказуемый результат:
- PARDP (`issuer_company_id=1`, `recipient_company_id=None`) → `recipient_company_id == None` → помечался **INCOMING**
- FACT (`issuer_company_id=контрагент`, `recipient_company_id=1`) → обе проверки `!= None` → помечался **INTERNAL**

**Фикс:** `logist2/settings/base.py` — `COMPANY_NAME = 'Caromoto Lithuania, MB'`. После сброса Django-кэша (`cache.delete('company:default_id')`) вся картина исправилась:

| document_type | OUTGOING | INCOMING | INTERNAL |
|---|---:|---:|---:|
| INVOICE (AV) | **103** ✅ | 0 | 0 |
| CREDIT_NOTE (KRE) | **5** ✅ | 0 | 0 |
| INVOICE_FACT | 0 | **240** ✅ | 0 |
| INVOICE_INCBLC | 0 | **2** ✅ | 0 |
| PROFORMA (PARDP) | 12 ✅ | 8 ⚠ | 0 |
| PROFORMA_BLC (AVBLC) | 2 ✅ | 4 ⚠ | 0 |

**Остаётся 12 «неправильных» PROFORMA/PROFORMA_BLC** (выглядят как входящие от складов/поставщиков). Это не настоящие PARDP, а ошибочно классифицированные входящие счета от контрагентов:

| Номер | Поставщик | Сумма | Предполагаемый правильный тип |
|---|---|---:|---|
| AV-000012 | WestTransit, UAB | 538.45€ | INVOICE_FACT |
| AV-000014 | Alauša, UAB | 60.01€ | INVOICE_FACT |
| AV-000018 | ORLEN Baltics Retail | 46.52€ | INVOICE_FACT |
| AV-000027 | NETO (склад) | 9056.00€ | INVOICE_FACT или INVOICE_INCBLC |
| AV-000029 | NETO | 3005.00€ | то же |
| AV-000031 | NETO | 7221.00€ | то же |
| AV-000034 | ATLANTIC | 260.00€ | то же |
| AV-000042 | NETO | 8261.00€ | то же |
| AVBLC-000003..006 | NETO (4 шт) | 14160.01€ сумм. | INVOICE_INCBLC (неоф.) |

**TODO → ЧАСТИЧНО ВЫПОЛНЕНО (2026-04-21):**
- 4 записи WestTransit/Alauša/ORLEN/ATLANTIC (AV-000012/014/018/034) переклассифицированы в `INVOICE_FACT` скриптом `_reclassify_to_fact.py`. Номера AV-XXX сохранены (решение пользователя: оставить исторические номера).
- 8 записей NETO (5 PROFORMA + 3 PROFORMA_BLC) оставлены как есть по решению пользователя.

**Итоговая картина направлений (после всех фиксов):**

| document_type | OUTGOING | INCOMING |
|---|---:|---:|
| CREDIT_NOTE (KRE) | 5 ✅ | 0 |
| INVOICE (AV) | 103 ✅ | 0 |
| INVOICE_FACT | 0 | **244** ✅ |
| INVOICE_INCBLC | 0 | 2 ✅ |
| PROFORMA (PARDP) | 12 ✅ | 4 (NETO — намеренно оставлено) |
| PROFORMA_BLC (AVBLC) | 2 ✅ | 4 (NETO — намеренно оставлено) |

### 12.11 OCR реальных номеров счетов контрагентов (2026-04-21, ✅ ВЫПОЛНЕНО)

**Задача:** извлечь настоящие номера счетов из чеков Revolut и записать в `NewInvoice.external_number`. Изначально там был суррогат `YYYY-MM-DD_amount` (дата+сумма).

**Итог:** 204/244 FACT (84%) получили настоящие номера счетов.

#### Этап 1: pdfplumber + regex (скрипт `_ocr_stage1b_pdfplumber.py`)

- Читает текстовые PDF через `pdfplumber`
- Многострочный анализ с паттернами под реальные форматы:
  - Logispace: `Serija AVL Nr. 0077922` → `AVL 0077922`
  - Westtransit: `PVM SĄSKAITA-FAKTŪRA NR. 24/01201` → `24/01201`
  - DHL: `Sąskaitos numeris: VNOR000701006` → `VNOR000701006`
  - MSC: 2-я колонка в строке под `Client no: Invoice no: ...`
  - B1.lt: номер на строке ниже `Išankstinė sąskaita`
  - Cursor: `Invoice number QUTH8TV1 0019` → `QUTH8TV1-0019`
  - OTRAS: `Serija 24OTR Nr. 1005` → `24OTR 1005`
- **Результат:** 121/121 текстовых PDF (100%) распознаны
- Покрытие по поставщикам: MSC/Westtransit/FAAS/Logispace/NETO/Maersk/Atlantic/DHL/Revolut/B1.lt/MainBaltic/Cursor/OTRAS — **все 100%**

#### Этап 2: Claude Vision (скрипт `_ocr_stage2_vision.py`)

- Для 53 скан-PDF (нет текстового слоя) и 11 JPG
- PyMuPDF (`fitz`) рендерит первую страницу PDF → PNG (dpi=150), без Poppler
- Отправка в Claude Sonnet 4 (`claude-sonnet-4-20250514`) с кастомным системным промптом, JSON-ответ: `{invoice_number, date, supplier_name}`
- **Результат:** 61/64 распознаны (95%)
- 3 NO_MATCH — кассовые чеки заправок (Alauša/VIADA), где инвойс-номера просто нет

#### Что осталось с суррогатом (38 записей):

| Поставщик | Кол-во | Причина |
|---|---:|---|
| VSDF (Sodra) | 6 | Налоговые/соцстрах платежи — нет инвойс-номеров |
| Registrų centras | 5 | Госрегистр — нет инвойс-номеров |
| VMI | 1 | Налоговая — нет инвойс-номеров |
| Alauša/Viada/Orlen/Baltic Petroleum | 9 | Кассовые чеки заправок без invoice-номера или без PDF |
| DHL/Revolut/Transtrade/OTRAS/Cursor/MSC/Sargu-Trans/Maersk/Logispace/Emsi/B1.lt | 17 | Нет приложенного чека (Revolut не экспортировал PDF) |

Эти 38 — «правильный» суррогат, т.к. настоящего номера счёта для них не существует (госплатежи) или исходный документ недоступен.

#### Скрипты

- `scripts/debug/_ocr_stage1_pdfplumber.py` — первая итерация (отброшена)
- `scripts/debug/_ocr_stage1b_pdfplumber.py` — финальная версия Stage 1
- `scripts/debug/_ocr_stage2_vision.py` — Stage 2 через Claude Vision
- `scripts/debug/_ocr_final_audit.py` — аудит покрытия

#### Стоимость

~64 запроса в Claude Sonnet 4 (vision, ~2000 tokens каждый) ≈ $0.40-0.80. Время ~4 минуты.

### 12.12 Бизнес-правила связи "инвойс ↔ транзакция ↔ документ" (2026-04-21)

Пользователь сформулировал три строгих правила:

1. **FACT (`document_type='INVOICE_FACT'`, direction=INCOMING):**
   - Должна быть привязана хотя бы одна `Transaction(type=PAYMENT, status=COMPLETED)` — мы реально оплатили
   - Должен быть `attachment` (подтверждающий документ: счёт или чек)

2. **AV (`document_type='PROFORMA'`, direction=OUTGOING):**
   - Это коммерческое предложение, а не обязательство — **транзакций быть НЕ должно**. Если есть — это ошибка классификации или привязки

3. **PARDP (`document_type='INVOICE'`, direction=OUTGOING):**
   - Это официальный счёт клиенту — должен быть оплачен (входящая транзакция)
   - Должна быть привязана `Transaction(type=PAYMENT, status=COMPLETED)` на сумму ≥ total
   - Должен быть `attachment` (PDF с site.pro)

**Скрипт аудита:** `scripts/debug/_business_rules_audit.py` (итерирует по всем инвойсам, т.к. `direction` — property, не поле БД).

#### Первый прогон аудита

| Правило | Нарушений |
|---|---:|
| FACT без транзакции | 3 |
| FACT без файла | 41 (известный список) |
| AV с транзакцией (!) | 1 (AV-000032 Смердов 590€ ↔ Tx#29) |
| PARDP без транзакции | 11 (5 CANCELLED + 6 OVERDUE) |
| PARDP с несовпадающей суммой | 2 (PARTIALLY_PAID: CAZACU, PRUTEAN) |
| PARDP без attachment | 1 (PARDP-000103) |

#### Действия

1. **PARDP-000103 ILYA FROLOV 535€** — attachment в БД был указан, но файл отсутствовал на диске, `SiteProInvoiceSync.external_id=''` (статус FAILED с ошибкой "Sales document already registered").
   - Нашёл sale в site.pro через `search_sales(number='000103')` → `id=196, series='PARDP'`.
   - Вручную прописал `sync.external_id='196'`, `sync_status='SENT'`.
   - Скачал PDF через `service.save_invoice_pdf_to_attachment(invoice, overwrite=True)` — файл 77385 байт.
   - **Bug fix в `sitepro_service.py`:** расширил fallback в `push_invoice` — теперь ловит не только "already exists", но и "already registered" и "sales document already" (реальный текст ошибки site.pro). Это защитит от повторения ситуации.
   - Скрипты: `_resync_pardp103.py`, `_find_pardp103_v2.py`, `_restore_pardp103.py`.

2. **AV-000014 и AV-000018 → CANCELLED как дубликаты** (скрипт `_cancel_duplicates.py`):
   - AV-000014 (Alauša 60.01€): дубликат FACT-000216 (создан массовым импортом, BT#6, Tx#481, ext_num=136988)
   - AV-000018 (ORLEN 46.52€): дубликат FACT-000208 (BT#27, Tx#473, ext_num=263981)
   - Причина дублирования: эти AV были созданы ранее как «черновики» от поставщиков, а массовый импорт Revolut создал рядом FACT с реальными BT/Tx. Номера AV оставили для исторической связи, статус = CANCELLED с notes объясняющими дубликат.

3. **FACT-000002 Atlantic 340€** — свежий (2026-04-03) ISSUED, пока без оплаты. В банке платёж не найден (0 BT на 340€ или с "atlantic"). Оставлено: ждёт оплаты — это нормальное состояние только что полученного счёта.

4. **AV-000032 Смердов 590€ с Tx#29** — оставлено как есть по решению пользователя (возможно исторически привязанная транзакция; к правилу 2 не добавлено CANCELLED).

#### Итоговое состояние после фиксов

| Правило | Было | Стало | Остаток объясним? |
|---|---:|---:|---|
| FACT без транзакции | 3 | **1** | Да: FACT-000002 свежий ISSUED, ждёт оплаты |
| FACT без файла | 41 | 41 | Да: 16 госплатежей + 3 Cursor + 11 Revolut no-export + ... |
| AV с транзакцией | 1 | 1 | Пользователь решил оставить AV-000032 |
| PARDP без транзакции | 11 | **6 OVERDUE** | ⚠ ТРЕБУЕТ РЕШЕНИЯ (см. ниже) |
| PARDP сумма не сходится | 2 | 2 | Оставлено PARTIALLY_PAID |
| PARDP без attachment | 1 | **0** | ✅ Полный успех |

#### 6 OVERDUE PARDP без транзакции (6 300€ дебиторки) — ТРЕБУЕТ РЕШЕНИЯ

| PARDP | Клиент | Сумма | Дата |
|---|---|---:|---|
| PARDP-000092 | DOVAGRUP SRL | 1 020€ | 2026-02-04 |
| PARDP-000093 | DOVAGRUP SRL | 1 020€ | 2026-02-04 |
| PARDP-000086 | AFANASENCO GHENNADI | 1 150€ | 2026-02-03 |
| PARDP-000089 | AFANASII ANDREI | 1 070€ | 2026-02-03 |
| PARDP-000090 | OSTAPENCO MARC | 1 020€ | 2026-02-03 |
| PARDP-000091 | MARIN VALENTIN | 1 020€ | 2026-02-03 |

**Картина:** все 6 импортированы из site.pro (`notes: [site.pro import]`), PDF есть, но:
- 0 InvoiceItem (в site.pro позиции не заполнены)
- 0 связанных cars
- 0 транзакций
- 0 платежей от клиентов за всё время (`client.balance = 0€`)
- У каждого клиента это **единственный инвойс**

**Вывод:** это несостоявшиеся сделки (клиент запросил проформу, но не оплатил и не забрал автомобиль). Требуется решение: cancel или держать как дебиторку.

#### Углублённое расследование 6 OVERDUE (2026-04-21)

Проверили через `_overdue_details.py`, `_overdue_moldova_check.py`, `_overdue_cash_search.py`, `_overdue_crm_check.py`:

**Claude Vision прочитал все 6 PDF**: все клиенты `MD`, услуга `Expedition services`, без VIN/car/route (в site.pro позиции — стандартная «Paslauga (vnt.)» без привязки к конкретному авто).

**Даты создания в site.pro** (все пакетом за 2 дня):
- 2026-02-03 09:49 / 09:56 / 09:57 / 09:59 — 4 проформы физлицам
- 2026-02-04 07:06 / 07:07 — 2 проформы DOVAGRUP SRL (дубль, разница 1 минута)

**Оплаты соседних PARDP в тот же период (9 шт, все через CAROMOTO MOLDOVA):** PARDP-000083/084/085/087/088/095/096/097/098 оплачены от разных физлиц Молдовы (TURCAN, CAZAC, BOTNARI, CULAVA, COSTOV, UNTILA, MESTER, COLESNIC, CEBAN) через пары `BALANCE_TOPUP + PAYMENT`. То есть схема работала.

**Проверки, исключающие «зависшие» деньги:**
- Все BT на 1020/1070/1150/2040€ в Feb-Apr 2026 — 100% привязаны к другим PARDP
- `BALANCE_TOPUP` без инвойса на CAROMOTO MOLDOVA: 0
- `PAYMENT` с `related_client=CM` без инвойса: 0
- Автомобили в CRM у всех 5 клиентов: 0 (ни одного)

**Финальный диагноз:** это «мёртвые» проформы — клиенты запросили ознакомительный счёт, но заказ не реализовался. Машины в операционный учёт не заносились. DOVAGRUP 000092/000093 — технический дубль при создании в site.pro.

**Решение пользователя 2026-04-21:** «я сам проверю с менеджером — пока оставь». Все 6 остаются OVERDUE до уточнения.

#### Скрипты сессии 3.12

- `_business_rules_audit.py` — главный аудит по 3 правилам
- `_fix_3_orphans.py` — поиск BT для 3 сирот по сумме+keyword
- `_analyze_duplicates.py` — детальный анализ AV vs FACT с одинаковыми поставщиками
- `_resync_pardp103.py`, `_find_pardp103_v2.py`, `_restore_pardp103.py` — восстановление PARDP-000103
- `_cancel_duplicates.py` — отмена AV-000014/018
- `_analyze_overdue_pardp.py`, `_pardp_cars_balance.py` — анализ 6 OVERDUE
- `_overdue_details.py` — Claude Vision анализ содержимого PDF
- `_overdue_moldova_check.py` — проверка оплат через CAROMOTO MOLDOVA
- `_overdue_cash_search.py` — поиск зависших денег в банке
- `_overdue_crm_check.py` — проверка наличия машин в CRM

#### Изменения в core-коде

**`core/services/sitepro_service.py`** — расширен fallback в `push_invoice`:
```python
err_text = str(create_err).lower()
if create_err.status_code == 400 and (
    'already exists' in err_text
    or 'already registered' in err_text
    or 'sales document already' in err_text
):
    existing_id = self._find_existing_sale_id(...)
```
Ранее ловил только «already exists», из-за чего для ответов site.pro типа «Sales document already registered» fallback не срабатывал и `SiteProInvoiceSync.external_id` оставался пустым. Это привело к проблеме с PARDP-000103 (PDF не скачивался).

---

## 13. Что делать дальше (TODO roadmap)

Блоки упорядочены по приоритету. Каждый следующий диалог должен двигаться сверху вниз.

### 13.1 Блокированные (ждут внешнего решения)

- [ ] **6 OVERDUE PARDP** (6 300€) — ждём ответа менеджера. Как будет решение:
  - Если «отменить» → запустить скрипт `_cancel_overdue_pardp.py` (шаблон: `_cancel_duplicates.py`) с `APPLY=1`. Статус → CANCELLED, notes с объяснением.
  - Если «отменить + sync site.pro» → добавить вызов API site.pro (метод cancel/delete sale если он есть, иначе через update sale status).
  - Если «держать» → ничего не делать, добавить note в invoice.notes про причину.

### 13.2 Активные задачи чистки

- [ ] **Клиентские долги** (~18 590€ по 15 клиентам) — см. секции раннего аудита. Нужно: аудит каждого клиента, понять есть ли реальный долг или ошибка привязки платежей.
- [ ] **Старые AV/AVBLC/FACT/INCBLC без клиента** (~47 000€) — «подвешенные» инвойсы, которые не привязаны ни к одному клиенту. Решить: либо привязать, либо пометить как INVALID/CANCELLED.
- [ ] **NETO PROFORMA/PROFORMA_BLC** (8 шт, ~40K€ суммарно) — пользователь оставил «я сам решу позже». Когда вернётся к теме: нужно понять, официальные это счета от NETO или неофициальные (нал). Возможно переклассифицировать в `INVOICE_FACT` или `INVOICE_INCBLC`.

### 13.3 Ручная доработка attachments

- [ ] **3 Cursor FACT** (FACT-000011/012/236) — скачать PDF вручную с Cursor dashboard → прикрепить через админку или скрипт.
- [ ] **5 CREDIT_NOTE (KRE)** — созданы локально, в site.pro не sync'ились → нет PDF. Решить: либо отправить в site.pro через `push_invoice` (если API поддерживает credit-notes), либо оставить без PDF.
- [ ] **14 старых AV** (до появления sync) — либо сделать batch-push в site.pro, либо пометить как исторические без PDF.

### 13.4 Возможные улучшения (опционально)

- [ ] **Cronjob на `_business_rules_audit.py`** — запускать раз в день, логировать нарушения, алертить если появляются новые FACT без транзакции.
- [ ] **UI-кнопка «Скачать PDF с site.pro»** в админке NewInvoice для PARDP — вызывает `SiteProService.save_invoice_pdf_to_attachment(invoice)`.
- [ ] **OCR Stage 3 для CREDIT_NOTE / PROFORMA** — если понадобится, аналогично FACT.
- [ ] **Monthly report** — P&L по месяцам на основе категорий (Логистика, Аренда, Зарплаты и т.д.).
- [ ] **Дашборд соответствия бизнес-правилам** — виджет в главной админке с текущими счётчиками нарушений.

### 13.5 Чистовая уборка репозитория

- [ ] Папка `scripts/debug/` содержит ~100+ одноразовых скриптов. Можно:
  - Оставить как есть (исторический архив)
  - Перенести в `scripts/archive/session_3/` и в корне держать только «используемые в работе» (business_rules_audit, missing_num_and_pdf, sitepro_download_pdfs)
- [ ] Промежуточные JSON-файлы в `scripts/debug/_*.json` — удалить или добавить в `.gitignore`

### Ключевые принципы работы в новых сессиях

1. **Прежде чем что-то менять** — запустить `_business_rules_audit.py` и посмотреть текущее состояние.
2. **Dry-run всегда первым** — apply только после подтверждения пользователя. В скриптах используется паттерн `APPLY = os.environ.get("APPLY") == "1"`.
3. **Перед удалением/отменой** — записать в `invoice.notes` причину с датой.
4. **При работе с site.pro** — сначала `search_sales(number=...)` чтобы убедиться что запись там есть, потом манипуляции.
5. **Для direction в ORM** — итерировать в Python (`direction` — property, не поле).
6. **Backup перед большими изменениями** — `python manage.py dumpdata core.NewInvoice core.Transaction > backup_YYYYMMDD.json`.

