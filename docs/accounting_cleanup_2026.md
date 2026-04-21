# Accounting Cleanup 2026 — Итоговый чеклист

Дата обновления: 2026-04-21 (конец сессии 2).

> **📌 Главный документ для нового диалога:** [`accounting_session_handoff.md`](./accounting_session_handoff.md) — открой его первым.
>
> Этот файл — исторический чеклист по блокам. Все блоки ниже ЗАВЕРШЕНЫ кроме отмеченных как "ОТКРЫТО".

---

## Сделано (Этапы 1-2)

- [x] **Sync Revolut / Paysera / site.pro** — все источники загружены, 4020.80€ баланс Revolut сверен
- [x] **Paysera XLSX импорт** — 4 недостающие комиссии добавлены (BT #427-430)
- [x] **76 BT-без-Transaction** → создано 152 Tx (TOPUP+PAYMENT пар), 227 705€ реконсилировано
- [x] **Слияние дубля Belarus**: Client id=123 (Caromoto-Bel OOO) → id=4 (CAROMOTO BELARUS)
- [x] **Добавлено поле `Transaction.related_client`** (миграция 0160)
- [x] **Размечены 50 CM-клиентов** → 100 Tx помечены `related_client=CAROMOTO MOLDOVA`
- [x] **Cazacu Sergiu (id=76)** — balance = 0,00 (+5€ TOPUP, related=CM)

---

## Блок 1: Дотегировать 6 существующих Tx — ✅ СДЕЛАНО

12 Tx (6 BT × 2 пары) помечены `related_client=12`:
- BT #53 Untila Valeriu 1245€
- BT #51 Costov Alexandru 1250€
- BT #52 Mester Vadim 1250€
- BT #415 Iurciuc Vladislav 1070€
- BT #411 Stratanenco Alexandru 1070€
- BT #54 Cristina Mamteva 1070€

Скрипт: `scripts/debug/_block1_tag.py`

---

## Блок 2: BT+инвойс есть, но Tx не создан — ✅ СДЕЛАНО

### BT#349 Pavaloi Iulian 1037€ — PARDP-000063 (total 1200€)
- Обнулён фальшивый PAID, создан TOPUP 1037€ + TRANSFER 163€ (CM→Pavaloi) + PAYMENT 1200€
- Инвойс PAID, balance клиента 0, **balance CM = −163€**

### BT#345 Andrei Lungu 1280€ — PARDP-000064 LUNGU ECATERINA
- Платёж мужа привязан к инвойсу жены, TOPUP+PAYMENT 1280€
- Инвойс PAID, balance 0

Скрипт: `scripts/debug/_block2_fix.py`

### BT#329 Ruslan Tofan 1085€ — PARDP-000073 TOFAN RUSLAN
_Не было в решении пользователя, но идентичная структура. Оставлено на следующую итерацию._

---

## Блок 3: Копеечные расхождения — ✅ СДЕЛАНО

### BT#211 FRUNZE GHEORGHI (PARDP-000027)
- Total уменьшен 1375.96 → 1375.00 (выровнено округление)
- TOPUP + PAYMENT по 1375 USD (валюта исправлена)

### BT#207 CUCULEANU DANIEL-NICOLAE (PARDP-000031)
- TOPUP 1271 + PAYMENT 1270 → PAID + 1€ advance (balance=1€)

Скрипт: `scripts/debug/_block3_fix.py`

---

## Блок 4: Alauša/Orlen — ✅ СДЕЛАНО

Подтверждено: это оплаты топлива с карты. Знак правильный (отрицательный).
- BT#6 Alauša −60.01€ → `reconciliation_skipped=True`
- BT#27 Orlen −46.52€ → `reconciliation_skipped=True`
- Категория "Топливо" (id=8) уже существовала

**Не сделано:** полноценный учёт через INVOICE_FACT — это отдельная задача (наверняка там десятки BT за весь период).

Скрипт: `scripts/debug/_blocks_7_4.py`

---

## Блок 5: Frolov 535€ — ✅ СДЕЛАНО

Подтверждено пользователем: 535€ правильная сумма инвойса.

---

## Блок 6: 9 подтверждённых оплат XLSX — ✅ 7 ИЗ 9 СДЕЛАНО

| XLSX | Сумма | Статус |
|---|---|---|
| Chitoroag vasilie | 1080€ | 🔴 **НЕ НАЙДЕН** — см. handoff 5.3 |
| SRL Datilux Com | 1460€ | ✅ BT#253 ($1725 USD, XLSX в EUR) |
| Наличные 3050€ | 3050€ | ❌ НЕ ПРОВОДИМ (unofficial) |
| Igor Catrinescu | 1220€ | ✅ BT#246 ($1455 USD, XLSX в EUR) |
| Cristian Pinzaru | 1165€ | ✅ BT#293 ($1375 USD) |
| Vitali Diulgher | 1190€ | ✅ BT#270 ($1400 USD) |
| Oleinicenco Sergiu | 1270€ | ✅ BT#344 → PARDP-000065 создан + TOPUP/PAYMENT |
| Eugeniu P | 1285€ | ✅ BT#338 → PARDP-000066 PARTIAL (долг 60€) |
| Roman Surcov | 1452€ | ✅ BT#56 (1425€ EUR, XLSX +27€ расхождение) |

Скрипт: `scripts/debug/_block6_fix.py`

---

## Блок 7: 9 AV-проформ Moldova — ✅ СДЕЛАНО

Отменены (status=CANCELLED): AV-000002, 000006, 000019, 000020, 000026, 000033, 000043, 000045, 000050. 
Всего **13 665€**. `CAROMOTO MOLDOVA.open_invoices_debt` упал с 13 665€ до **0€**.

Причина: пользователь подтвердил, что это был внутренний учёт (не настоящие счета).

Скрипт: `scripts/debug/_blocks_7_4.py`

---

## Блок 8: РАСХОЖДЕНИЕ 29 205€ — 🟡 ОТКРЫТО (для бухгалтера)

**Контекст:** В XLSX Moldova 22+ автовоза (TRAL-101, 119-143, K8 контейнер, запчасти), за которые Moldova должна 29 205€. В Django **нет PARDP** на них.

**Решение пользователя:** "В django не вёлся строгий учёт, поэтому многих счетов там может и не быть". 

**Требует:** консультации с бухгалтером. Варианты:
- (а) Создать 20+ PARDP задним числом на каждый автовоз
- (б) Оставить только в XLSX
- (в) Один сводный PARDP на 29 205€

---

## Блок 9: Категория «Залоги и гарантии» — ✅ СДЕЛАНО

`ExpenseCategory.objects.create(name="Залоги и гарантии", category_type="OTHER", id=17)`. 

Проставлять на существующие Tx — позже отдельной задачей.

---

## Блок 10: USD валюта — ✅ СДЕЛАНО (не было в исходном плане)

5 клиентов платили в USD через Revolut. 10 Tx (5 TOPUP + 5 PAYMENT) исправлены на `currency=USD`:
- Frunze $1375 → €1202 (курс 0.875)
- Datilux $1725 → €1476 (курс 0.856)
- Catrinescu $1455 → €1239 (курс 0.851)
- Pinzaru $1375 → €1184 (курс 0.861, пачка)
- Diulgher $1400 → €1206 (курс 0.861, пачка)

Инвойсы PARDP-000027/042/044/045/055 — в USD.

Скрипт: `scripts/debug/_fix_usd_tx.py`

---

## Блок 11: Серия KRE (Credit Notes) — ✅ СДЕЛАНО (не было в исходном плане)

Добавлен тип `CREDIT_NOTE` → префикс `KRE` (миграция 0161).
Бейдж в админке: `core/admin_billing.py:812`.

Созданы 5 KRE-документов на клиентов, которым выписали PARDP, но платежа так и не поступило:

| KRE | Клиент | Сумма | Оригинальный PARDP |
|---|---|---|---|
| KRE-000001 | CUCULEANU DANIEL | 300€ | PARDP-000032 → CANCELLED |
| KRE-000002 | VALI PACALO | 510€ | PARDP-000038 → CANCELLED |
| KRE-000003 | TATARENCO ANDREI | 1275€ | PARDP-000046 → CANCELLED |
| KRE-000004 | PINTEA DIONISIE | 1030€ | PARDP-000054 → CANCELLED |
| KRE-000005 | PLAUDIS AIGARS | 380€ | PARDP-000059 → CANCELLED |

Скрипт: `scripts/debug/_create_kre.py`

---

## Блок 12: NUR — ❌ НЕ ТРОГАЕМ

По решению пользователя (подтверждено 2026-04-21): NUR в site.pro — это курсовые разницы от USD→EUR конверсии, в Django не переносим.

---

## Блок 13: Обновить accounting-context.mdc — 🟡 ОТКРЫТО

Записать в правило архитектурные решения:
- `Transaction.related_client` — для оптовиков/посредников
- Серия KRE (`CREDIT_NOTE`)
- Мультивалютность (currency обязательно)
- Правила CAROMOTO MOLDOVA / BELARUS / Daniel Soltys
- Алгоритм пары TOPUP + PAYMENT
- Dead-documents: когда отменять AV/PARDP

См. [handoff 5.7](./accounting_session_handoff.md#57-обновить-accounting-contextmdc).

---

## Сводка на 2026-04-21 (конец сессии)

### CAROMOTO MOLDOVA (id=12)
```
balance            = -163.00 EUR  (от Pavaloi недоплаты)
open_invoices_debt = 0.00          (AV отменены)
total_balance      = -163.00
Tx with related_client=12: 128
Теневой оборот: 75 728€
```

### NewInvoice по типам
- INVOICE (PARDP): 103 (5 CANCELLED через KRE)
- PROFORMA (AV): 20 (9 CANCELLED)
- **CREDIT_NOTE (KRE): 5** (новые)
- INVOICE_FACT: 14, INVOICE_INCBLC: 2, PROFORMA_BLC: 6

### BankTransactions: 430 всего
- Matched: 105
- Skipped: 303
- **Orphaned: 22** (к разбору)

### USD
- 10 Tx, $7330 TOPUP = $7330 PAYMENT
- USD Revolut balance = 0 (всё обменяно)

---

## Оставшиеся задачи (см. handoff)

- **22 orphan BankTransactions** — финальный разбор
- **Chitoroag Vasilie 1080€** — не найден в банках
- **Блок 8 — 29 205€ Moldova** — для бухгалтера
- **site.pro синк KRE** — при следующей синхронизации
- **Обновить accounting-context.mdc**
- **Полноценный учёт топлива через INVOICE_FACT** — большая отдельная задача
