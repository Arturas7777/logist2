"""
Подсчёт доходов по 4 банковским счетам за 2025 год для декларации
"Individuali veikla" (Литва).

Логика:
- Учитываются только КРЕДИТовые (входящие) операции.
- Исключаются переводы между собственными счетами пользователя
  (по IBAN/EVP и по имени владельца).
- Исключаются явно неденежные/служебные операции
  (обмен валюты внутри Revolut, кэшбэк, корректировки).
- Считается отдельно по каждому счёту, плюс общий итог.
- Дополнительно выводится по плательщикам, чтобы было удобно
  верифицировать список клиентов.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable

DOWNLOADS = Path(r"c:\Users\art-f\OneDrive\Загрузки")

SEB_FILE = DOWNLOADS / "Išrašas SEB bankas.csv"
PAYSERA_MAIN_FILE = DOWNLOADS / "Paysera_EVP6110015515781_2025-01-01_2025-12-31.csv"
PAYSERA_SECOND_FILE = DOWNLOADS / "Paysera_EVP1810015514196_2025-01-01_2025-12-31.csv"
REVOLUT_FILE = DOWNLOADS / "Revolut_account-statement_2025-01-01_2025-12-31_ru-ru_45ce88.csv"
REVOLUT_USD_FILE = DOWNLOADS / "account-statement_2025-01-01_2025-12-31_ru_02978f.csv"

# Курсы Lietuvos banko (ECB) на дату операции для конвертации валют в EUR.
# Используются для не-EUR зачислений. Источник: lb.lt / ECB euro reference rates.
# Формат: { "USD": { "YYYY-MM-DD": Decimal("1 EUR = X USD") } }
LB_RATES: dict[str, dict[str, Decimal]] = {
    "USD": {
        "2025-01-28": Decimal("1.0421"),  # ECB/LB на 28.01.2025
    },
}

# Собственные счета пользователя (для определения внутренних переводов)
OWN_IBANS = {
    "LT987044060008244498",  # SEB
    "LT533500010015515781",  # Paysera EVP6110015515781
    "LT713500010015514196",  # Paysera EVP1810015514196
    "LT893250037131367316",  # Revolut
}
OWN_EVPS = {
    "EVP6110015515781",
    "EVP1810015514196",
}
OWN_NAME_TOKENS = ("haizhutsis", "arturas")  # совпадает с любой формой записи


def is_self_counterparty(name: str, account: str) -> bool:
    """True, если контрагент — это сам пользователь."""
    if account:
        acc_clean = account.replace(" ", "").upper()
        if acc_clean in OWN_IBANS or acc_clean in OWN_EVPS:
            return True
    if name:
        n = name.lower()
        if "haizhutsis" in n and "arturas" in n:
            return True
    return False


@dataclass
class Tx:
    account_label: str
    date: str
    amount: Decimal  # всегда положительная (в EUR)
    counterparty: str
    counterparty_account: str
    description: str
    raw_type: str = ""

    # Исходная сумма в валюте операции (если не EUR)
    original_amount: Decimal = Decimal("0")
    original_currency: str = "EUR"
    fx_rate_note: str = ""  # пометка о курсе пересчёта

    is_income: bool = True
    exclude_reason: str = ""
    category: str = ""  # "income" / "self_transfer" / "fx" / "fee_refund" / ...


@dataclass
class AccountResult:
    label: str
    incomes: list[Tx] = field(default_factory=list)
    excluded: list[Tx] = field(default_factory=list)

    @property
    def total_income(self) -> Decimal:
        return sum((t.amount for t in self.incomes), Decimal("0"))


# --------------------------- парсеры ---------------------------

def parse_decimal(s: str, *, comma_decimal: bool) -> Decimal:
    s = (s or "").strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return Decimal("0")
    if comma_decimal:
        s = s.replace(".", "").replace(",", ".")
    return Decimal(s)


def parse_seb(path: Path) -> list[Tx]:
    txs: list[Tx] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        # У SEB первая строка — это заголовок выписки, реальный header — на 2-й строке.
        reader = csv.reader(f, delimiter=";", quotechar='"')
        rows = list(reader)
    header = rows[1]
    # Маппинг колонок
    idx = {name: i for i, name in enumerate(header)}
    col_date = idx["DATA"]
    col_amount = idx["SUMA"]
    col_name = idx["MOKĖTOJO ARBA GAVĖJO PAVADINIMAS"]
    col_account = idx["SĄSKAITA"]
    col_purpose = idx["MOKĖJIMO PASKIRTIS"]
    col_dc = idx["DEBETAS/KREDITAS"]
    col_type = idx["TRANSAKCIJOS TIPAS"]

    for row in rows[2:]:
        if not row or len(row) < len(header):
            continue
        dc = (row[col_dc] or "").strip().upper()
        if dc != "C":  # только зачисления
            continue
        amount = parse_decimal(row[col_amount], comma_decimal=True)
        if amount <= 0:
            continue
        tx = Tx(
            account_label="SEB (LT98...4498)",
            date=row[col_date],
            amount=amount,
            counterparty=row[col_name].strip(),
            counterparty_account=row[col_account].strip(),
            description=row[col_purpose].strip(),
            raw_type=row[col_type].strip(),
        )
        classify(tx)
        txs.append(tx)
    return txs


def parse_paysera(path: Path, label: str) -> list[Tx]:
    txs: list[Tx] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            kd = (row.get("Kreditas / Debetas") or "").strip().upper()
            if kd != "K":  # только зачисления (Kreditas)
                continue
            amount = parse_decimal(row.get("Suma ir valiuta", "0"), comma_decimal=False)
            if amount <= 0:
                continue
            tx = Tx(
                account_label=label,
                date=row.get("Data ir laikas", "").strip(),
                amount=amount,
                counterparty=(row.get("Gavėjas / Mokėtojas") or "").strip(),
                counterparty_account=(row.get("EVP / IBAN") or "").strip(),
                description=(row.get("Paskirtis") or "").strip(),
                raw_type=(row.get("Tipas") or "").strip(),
            )
            classify(tx)
            txs.append(tx)
    return txs


def parse_revolut(path: Path, *, account_label: str = "Revolut (LT89...7316)",
                  expected_currency: str = "EUR") -> list[Tx]:
    """Парсер Revolut. Если валюта операции ≠ EUR, конвертирует в EUR по курсу
    Lietuvos banko (ECB) на дату операции из таблицы LB_RATES."""
    txs: list[Tx] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            amount = parse_decimal(row.get("Сумма", "0"), comma_decimal=False)
            if amount <= 0:
                continue
            state = (row.get("State") or "").strip().upper()
            if state and state not in {"ВЫПОЛНЕНО", "COMPLETED"}:
                continue
            currency = (row.get("Валюта") or "EUR").strip().upper()
            date_str = row.get("Дата выполнения", "").strip()[:10]

            original_amount = amount
            original_currency = currency
            fx_note = ""
            if currency != "EUR":
                rates = LB_RATES.get(currency, {})
                rate = rates.get(date_str)
                if rate is None:
                    fx_note = (f"!!! НЕТ КУРСА LB для {currency} на {date_str} — "
                               f"подставьте вручную в LB_RATES")
                    eur_amount = Decimal("0")
                else:
                    # rate = 1 EUR в данной валюте → EUR = amount / rate
                    eur_amount = (amount / rate).quantize(Decimal("0.01"))
                    fx_note = (f"конвертация {currency}→EUR по курсу LB/ECB "
                               f"{date_str}: 1 EUR = {rate} {currency}")
                amount = eur_amount

            tx = Tx(
                account_label=account_label,
                date=date_str,
                amount=amount,
                counterparty=(row.get("Описание") or "").strip(),
                counterparty_account="",
                description=(row.get("Описание") or "").strip(),
                raw_type=(row.get("Тип") or "").strip(),
                original_amount=original_amount,
                original_currency=original_currency,
                fx_rate_note=fx_note,
            )
            classify(tx)
            txs.append(tx)
    return txs


# --------------------------- классификация ---------------------------

SELF_DESC_PATTERNS = (
    re.compile(r"перевод со счета", re.IGNORECASE),
    re.compile(r"между своими", re.IGNORECASE),
    re.compile(r"\bsau\b", re.IGNORECASE),
    re.compile(r"mokėjimas mobiliąja programėle", re.IGNORECASE),  # P2P между своими
    re.compile(r"lėšų pervedimas pagal paysera", re.IGNORECASE),  # пополнение Paysera наличными
)


def classify(tx: Tx) -> None:
    """Размечает транзакцию: доход / внутренний перевод / прочее не-доход."""
    desc = tx.description or ""
    name = tx.counterparty or ""
    rtype = tx.raw_type or ""

    # 1) Внутренний перевод по IBAN/EVP контрагента
    if is_self_counterparty(name, tx.counterparty_account):
        tx.is_income = False
        tx.category = "self_transfer"
        tx.exclude_reason = "перевод со своего счёта (контрагент = пользователь)"
        return

    # 2) Внутренние операции по описанию
    for p in SELF_DESC_PATTERNS:
        if p.search(desc):
            tx.is_income = False
            tx.category = "self_transfer"
            tx.exclude_reason = f"описание указывает на собственный перевод: {desc!r}"
            return

    # 3) Обмен валюты в Revolut (это не доход — это конвертация собственных средств)
    if "обмен валют" in rtype.lower() or "обмен валют" in desc.lower():
        tx.is_income = False
        tx.category = "fx"
        tx.exclude_reason = "обмен валюты в рамках своего счёта"
        return

    # 4) Возврат / возмещение / Cashback / Refund
    low = (desc + " " + rtype).lower()
    if any(k in low for k in ("cashback", "кэшбэк", "возврат", "refund", "reversal",
                              "atšaukim", "grąžinim", "graz inim", "grazinim")):
        tx.is_income = False
        tx.category = "refund"
        tx.exclude_reason = "возврат/кэшбэк/корректировка"
        return

    # 5) Государственные пособия / выплаты на ребёнка не относятся к доходу IV
    if "išmoka vaikui" in low or "išmoką vaik" in low or "ismoka vaik" in low:
        tx.is_income = False
        tx.category = "child_benefit"
        tx.exclude_reason = "детское пособие (не относится к доходу IV)"
        return

    # 6) Муниципальная компенсация / субсидия (не доход IV)
    if "kompensav" in low or "kompensacij" in low or "subsidij" in low:
        tx.is_income = False
        tx.category = "subsidy"
        tx.exclude_reason = "муниципальная компенсация/субсидия — не доход IV"
        return

    # 7) Выигрыши Optibet / лотереи — не доход IV
    if "laimejim" in low or "optibet" in low or "laimėjim" in low:
        tx.is_income = False
        tx.category = "lottery"
        tx.exclude_reason = "выигрыш в лотерее/ставках — не относится к доходу IV"
        return

    # 8) Пополнение наличными в банкомате — по решению пользователя учитывается
    #    как доход IV (наличная выручка от клиентов, внесённая в банк).
    #    Кассовая книга/чеки должны быть на руках для подтверждения.
    if "brink" in (name or "").lower() or "grynųjų pinigų įneš" in low or \
            "grynuju pinigu ines" in low or ("įnešimas" in low and "kortel" in low):
        tx.is_income = True
        tx.category = "cash_deposit_income"
        tx.exclude_reason = ""
        return

    # 9) Внутренние операции Revolut (Vault/Pockets/Savings) — это перемещение
    #    собственных средств в рамках одного банка, не доход.
    if name and name.strip().lower() in {"revolut bank uab", "revolut ltd", "revolut"}:
        tx.is_income = False
        tx.category = "revolut_internal"
        tx.exclude_reason = "внутренняя операция Revolut (Vault/Savings/Pockets)"
        return

    tx.is_income = True
    tx.category = "income"


# --------------------------- отчёт ---------------------------

def fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}".replace(",", " ")


def print_section(title: str, char: str = "=") -> None:
    print()
    print(char * 78)
    print(title)
    print(char * 78)


def main() -> None:
    accounts: list[AccountResult] = []

    seb = AccountResult("SEB (LT98...4498)")
    for tx in parse_seb(SEB_FILE):
        (seb.incomes if tx.is_income else seb.excluded).append(tx)
    accounts.append(seb)

    pay1 = AccountResult("Paysera EVP6110015515781")
    for tx in parse_paysera(PAYSERA_MAIN_FILE, pay1.label):
        (pay1.incomes if tx.is_income else pay1.excluded).append(tx)
    accounts.append(pay1)

    pay2 = AccountResult("Paysera EVP1810015514196")
    for tx in parse_paysera(PAYSERA_SECOND_FILE, pay2.label):
        (pay2.incomes if tx.is_income else pay2.excluded).append(tx)
    accounts.append(pay2)

    rev = AccountResult("Revolut EUR (LT89...7316)")
    for tx in parse_revolut(REVOLUT_FILE, account_label=rev.label):
        (rev.incomes if tx.is_income else rev.excluded).append(tx)
    accounts.append(rev)

    rev_usd = AccountResult("Revolut USD")
    for tx in parse_revolut(REVOLUT_USD_FILE, account_label=rev_usd.label,
                            expected_currency="USD"):
        (rev_usd.incomes if tx.is_income else rev_usd.excluded).append(tx)
    accounts.append(rev_usd)

    grand_total = Decimal("0")
    print_section("ИТОГИ ПО СЧЕТАМ")
    print(f"{'Счёт':35} {'Доход (EUR)':>15} {'Зачислений всего':>20} {'Из них исключено':>20}")
    print("-" * 92)
    for a in accounts:
        total = a.total_income
        grand_total += total
        n_total = len(a.incomes) + len(a.excluded)
        print(f"{a.label:35} {fmt(total):>15} {n_total:>20} {len(a.excluded):>20}")
    print("-" * 92)
    print(f"{'ИТОГО (доходы)':35} {fmt(grand_total):>15} EUR")

    # Подробно: доходы по каждому счёту
    for a in accounts:
        print_section(f"{a.label} — учтено как доход ({len(a.incomes)} операций, "
                      f"{fmt(a.total_income)} EUR)")
        # Группируем по контрагенту
        by_payer: dict[str, list[Tx]] = defaultdict(list)
        for t in a.incomes:
            by_payer[t.counterparty or "(без имени)"].append(t)
        for payer, items in sorted(by_payer.items(),
                                   key=lambda kv: -sum(t.amount for t in kv[1])):
            payer_total = sum((t.amount for t in items), Decimal("0"))
            print(f"\n  {payer}  —  {fmt(payer_total)} EUR  ({len(items)} оп.)")
            for t in items:
                desc = t.description.replace("\n", " ")[:90]
                print(f"    {t.date[:10]}  {fmt(t.amount):>10}  {desc}")

    # Подробно: исключённое (для верификации)
    for a in accounts:
        if not a.excluded:
            continue
        excl_total = sum((t.amount for t in a.excluded), Decimal("0"))
        print_section(f"{a.label} — ИСКЛЮЧЕНО ({len(a.excluded)} оп., "
                      f"{fmt(excl_total)} EUR)", char="-")
        for t in a.excluded:
            desc = t.description.replace("\n", " ")[:80]
            print(f"  [{t.category:14}] {t.date[:10]}  {fmt(t.amount):>10}  "
                  f"{t.counterparty[:25]:25}  {desc}")

    print_section("ДОХОД (для декларации Individuali veikla)")
    print(f"  Сумма всех зачислений от внешних плательщиков по 4 счетам:")
    print(f"     >>>  {fmt(grand_total)} EUR  <<<")

    # JSON-экспорт для canvas/визуализации
    import json
    out = {
        "accounts": [],
        "categories": defaultdict(lambda: {"count": 0, "amount": "0"}),
        "grand_total_income": str(grand_total),
    }
    cat_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    cat_counts: dict[str, int] = defaultdict(int)
    for a in accounts:
        acc_data = {
            "label": a.label,
            "income_total": str(a.total_income),
            "income_count": len(a.incomes),
            "excluded_count": len(a.excluded),
            "incomes": [
                {
                    "date": t.date[:10],
                    "amount": str(t.amount),
                    "counterparty": t.counterparty,
                    "description": t.description,
                }
                for t in a.incomes
            ],
            "ambiguous": [],  # будут заполнены ниже
            "excluded_summary": {},
        }
        # категории исключений
        excl_by_cat: dict[str, list[Tx]] = defaultdict(list)
        for t in a.excluded:
            excl_by_cat[t.category].append(t)
            cat_totals[t.category] += t.amount
            cat_counts[t.category] += 1
        for cat, items in excl_by_cat.items():
            acc_data["excluded_summary"][cat] = {
                "count": len(items),
                "amount": str(sum((t.amount for t in items), Decimal("0"))),
                "examples": [
                    {
                        "date": t.date[:10],
                        "amount": str(t.amount),
                        "counterparty": t.counterparty,
                        "description": t.description[:120],
                    }
                    for t in items[:5]
                ],
            }
        # «спорные» (ambiguous): пока — пополнения наличными (cash_deposit)
        for t in a.excluded:
            if t.category == "cash_deposit":
                acc_data["ambiguous"].append({
                    "date": t.date[:10],
                    "amount": str(t.amount),
                    "counterparty": t.counterparty,
                    "description": t.description,
                    "reason": "наличное пополнение через банкомат — может быть бизнес-доход",
                })
        out["accounts"].append(acc_data)

    out["category_totals"] = {
        cat: {"count": cat_counts[cat], "amount": str(cat_totals[cat])}
        for cat in cat_totals
    }

    json_path = Path(__file__).parent / "income_data.json"
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2,
                                    default=str), encoding="utf-8")
    print(f"\nJSON сохранён: {json_path}")


if __name__ == "__main__":
    main()
