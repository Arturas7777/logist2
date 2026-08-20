"""Проверка автомобиля на санкции ЕС при транзите через Литву.

Источник правил — памятка литовской таможни «LENTELĖ SANKCIJOS / REIKALINGI
DOKUMENTAI»: таблица «код КН → нужные документы → стоимость». Она отвечает на
один вопрос: пропустят машину транзитом или нет, и какой пакет документов
попросят на границе.

Как читается таблица (и, соответственно, устроен этот модуль):

* Санкционные позиции ``Ex 8703 23/24/32/33/40/50/60/70/80`` описаны с
  оговоркой «просвет (клиренс) не меньше 165 мм». То есть под запрет попадают
  внедорожники и кроссоверы, а обычная легковая машина с низким клиренсом в
  санкционную позицию не входит вовсе — её везут по обычному пакету.
* Внутри санкционной позиции решает возраст: машина старше 5 лет проходит,
  но к пакету добавляется справка о высоте просвета от официального
  представителя бренда в Литве; машина новее 5 лет — санкции (нужна
  декларация производителя, практически это отказ).
* Мелкие двигатели вне санкций: бензин до 1900 см³ (позиции 8703 21/22 и
  первая ветка 8703 23), дизель до 1900 см³, грузовые до 1900 см³.
* ``Ex 8711`` (мотоциклы) и ``9705`` (коллекционные, от 30 лет) — не
  санкционные, но с ограничением по стоимости (5 000 и 50 000 EUR).

Стоимость по решению бизнеса не блокирует вывод: если она выше порога из
памятки, добавляется предупреждение «уточните у брокера» — реальные лимиты
меняются чаще, чем сама таблица.

Возраст считается по году выпуска: ``текущий год − год выпуска > 5``. Точной
даты первой регистрации у нас нет (NHTSA отдаёт только model year).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# Страны клиентов, которым нужен раздел: памятка описывает транзит через
# Литву в Беларусь. Отсюда же берётся видимость пункта меню в кабинете
# (``Client.sees_sanctions_check``).
AVAILABLE_FOR_COUNTRIES = frozenset({"BY"})


def is_available_for_country(country: str | None) -> bool:
    """Доступна ли проверка клиенту с такой страной."""
    return (country or "") in AVAILABLE_FOR_COUNTRIES


# ── Справочники для формы и разбора данных NHTSA ──────────────────────────

# Категория товара = группа кода КН.
CATEGORIES = [
    ("CAR", "Легковой автомобиль (до 10 мест)"),
    ("CARGO", "Грузовой автомобиль / фургон"),
    ("MOTO", "Мотоцикл, мопед, квадроцикл"),
    ("COLLECTIBLE", "Коллекционный (от 30 лет, в оригинальном состоянии)"),
]

# Тип силовой установки. Разделение именно такое, потому что у каждой
# комбинации свой код КН в таблице.
ENGINE_TYPES = [
    ("PETROL", "Бензиновый"),
    ("DIESEL", "Дизельный"),
    ("HYBRID_PETROL", "Гибрид бензин + электро (без зарядки от сети)"),
    ("HYBRID_DIESEL", "Гибрид дизель + электро (без зарядки от сети)"),
    ("PLUGIN_PETROL", "Гибрид бензин + электро с зарядкой от сети (PHEV)"),
    ("PLUGIN_DIESEL", "Гибрид дизель + электро с зарядкой от сети (PHEV)"),
    ("ELECTRIC", "Полностью электрический"),
]

# Клиренс: клиент отвечает «да / нет / не знаю».
CLEARANCE_CHOICES = [
    ("YES", "Да, 165 мм и больше (кроссовер, внедорожник, пикап)"),
    ("NO", "Нет, меньше 165 мм (седан, хэтчбек, купе)"),
    ("UNKNOWN", "Не знаю"),
]

# Вердикты. Порядок — от «всё хорошо» к «нельзя».
ALLOWED = "ALLOWED"
ALLOWED_WITH_EXTRA = "ALLOWED_WITH_EXTRA"
SANCTIONED = "SANCTIONED"
NEED_DATA = "NEED_DATA"

VERDICT_LABELS = {
    ALLOWED: "Можно везти",
    ALLOWED_WITH_EXTRA: "Можно везти, но нужен дополнительный документ",
    SANCTIONED: "Под санкциями — брать не стоит",
    NEED_DATA: "Не хватает данных об автомобиле",
}

# Базовый пакет документов из колонки «REIKALINGI DOKUMENTAI» — одинаковый
# во всех строках таблицы.
BASE_DOCUMENTS = (
    "Инвойс (счёт-фактура)",
    "Тайтл (Certificate of Title)",
    "Копия платёжного поручения",
    "Копия паспорта получателя",
    "Гарантийное письмо отправителя, что авто не будет вывезено в РФ",
    "Гарантийное письмо получателя, что авто не будет вывезено в РФ",
    "Договор перевозки",
)

CLEARANCE_DOCUMENT = "Справка о высоте просвета (клиренса) от официального представителя бренда в Литве"

MANUFACTURER_DECLARATION = "Декларация производителя (обязательна для санкционной позиции)"

# Пороги стоимости из колонки «PREKIŲ VERTĖ». Не блокируют вывод —
# см. модуль-docstring.
VALUE_LIMIT_EUR = {
    "CAR": Decimal("50000"),
    "CARGO": Decimal("50000"),
    "MOTO": Decimal("5000"),
    "COLLECTIBLE": Decimal("50000"),
}

# Возраст, с которого санкционная позиция становится проходимой.
SANCTION_AGE_YEARS = 5

# Минимальный возраст коллекционного авто по позиции 9705.
COLLECTIBLE_AGE_YEARS = 30

# Порог просвета из памятки, мм.
CLEARANCE_THRESHOLD_MM = 165

# Объём двигателя, до которого позиция вне санкций (бензин, дизель, грузовые).
SMALL_ENGINE_CC = 1900

_ENGINE_LABELS = dict(ENGINE_TYPES)


@dataclass
class CheckInput:
    """Данные об авто, по которым считается вердикт."""

    category: str = "CAR"
    engine_type: str = "PETROL"
    displacement_cc: int | None = None
    clearance: str = "UNKNOWN"
    year: int | None = None
    price_eur: Decimal | None = None
    vin: str = ""


@dataclass
class CheckResult:
    """Ответ клиенту: вердикт, код КН, пакет документов и пояснения."""

    verdict: str
    cn_code: str = ""
    cn_note: str = ""
    documents: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABELS.get(self.verdict, "")

    @property
    def is_blocked(self) -> bool:
        return self.verdict == SANCTIONED


def car_age(year: int | None, *, today: date | None = None) -> int | None:
    """Возраст авто в годах по году выпуска."""
    if not year:
        return None
    return (today or date.today()).year - int(year)


def check(data: CheckInput, *, today: date | None = None) -> CheckResult:
    """Главная точка входа: вердикт по одному автомобилю."""
    age = car_age(data.year, today=today)

    if data.category == "COLLECTIBLE":
        result = _check_collectible(age)
    elif data.category == "MOTO":
        result = _check_moto()
    elif data.category == "CARGO":
        result = _check_cargo(data)
    else:
        result = _check_car(data, age)

    _add_value_warning(result, data)
    return result


# ── Ветки по категориям ───────────────────────────────────────────────────


def _check_collectible(age: int | None) -> CheckResult:
    """9705 — коллекционные авто вне санкций, но критерии строгие."""
    result = CheckResult(
        verdict=ALLOWED,
        cn_code="9705",
        cn_note="Коллекционные автомобили исторической ценности",
        documents=list(BASE_DOCUMENTS),
    )
    result.reasons.append("Позиция 9705 не санкционирована.")
    if age is not None and age < COLLECTIBLE_AGE_YEARS:
        result.verdict = NEED_DATA
        result.reasons.append(
            f"Для позиции 9705 нужен возраст от {COLLECTIBLE_AGE_YEARS} лет, "
            f"а по году выпуска получается {age}."
        )
        result.missing.append("Подтверждение возраста от 30 лет")
        return result
    result.warnings.append(
        "Таможня признаёт авто коллекционным только если оно в оригинальном состоянии, "
        "без переделок кузова и двигателя, а модель больше не выпускается. Решение принимает инспектор."
    )
    return result


def _check_moto() -> CheckResult:
    """Ex 8711 — мотоциклы и мопеды: вне санкций."""
    return CheckResult(
        verdict=ALLOWED,
        cn_code="8711",
        cn_note="Мотоциклы, мопеды и подобные транспортные средства",
        documents=list(BASE_DOCUMENTS),
        reasons=["Позиция 8711 не входит в санкционный перечень."],
    )


def _check_cargo(data: CheckInput) -> CheckResult:
    """Ex 8704 — грузовые: проходят только двигатели до 1900 см³."""
    result = CheckResult(cn_code="8704", cn_note="Транспортные средства для перевозки грузов", verdict=NEED_DATA)
    if data.displacement_cc is None:
        result.missing.append("Объём двигателя, см³")
        result.reasons.append("Для грузовых решает объём двигателя: до 1900 см³ — можно, больше — санкции.")
        return result
    if data.displacement_cc <= SMALL_ENGINE_CC:
        result.verdict = ALLOWED
        result.documents = list(BASE_DOCUMENTS)
        result.reasons.append(f"Объём {data.displacement_cc} см³ — до 1900 см³, позиция вне санкций.")
        return result
    result.verdict = SANCTIONED
    result.reasons.append(f"Объём {data.displacement_cc} см³ больше 1900 см³ — грузовые под санкциями.")
    return result


def _check_car(data: CheckInput, age: int | None) -> CheckResult:
    """8703 — легковые: код по типу двигателя и объёму, дальше клиренс и возраст."""
    code, note, sanctionable, missing = _car_position(data)
    result = CheckResult(verdict=NEED_DATA, cn_code=code, cn_note=note)
    if missing:
        result.missing.extend(missing)
        result.reasons.append("Без объёма двигателя нельзя определить код КН.")
        return result

    if not sanctionable:
        result.verdict = ALLOWED
        result.documents = list(BASE_DOCUMENTS)
        result.reasons.append(f"Позиция {code} вне санкционного перечня.")
        return result

    # Санкционная позиция начинается с оговорки про просвет: низкая машина в
    # неё не попадает независимо от возраста и объёма.
    if data.clearance == "NO":
        result.verdict = ALLOWED
        result.documents = list(BASE_DOCUMENTS)
        result.reasons.append(
            f"Санкции в позиции {code} касаются машин с просветом от {CLEARANCE_THRESHOLD_MM} мм. "
            "У этой машины просвет меньше, поэтому она не попадает в санкционную позицию."
        )
        return result

    if data.clearance == "UNKNOWN":
        result.missing.append(f"Просвет (клиренс): {CLEARANCE_THRESHOLD_MM} мм и больше или меньше")
        result.reasons.append(
            f"Позиция {code} санкционная только для машин с просветом от {CLEARANCE_THRESHOLD_MM} мм — "
            "уточните клиренс, иначе вердикт не однозначен."
        )
        return result

    if age is None:
        result.missing.append("Год выпуска")
        result.reasons.append("Для санкционной позиции решает возраст: старше 5 лет — можно, новее — нельзя.")
        return result

    if age > SANCTION_AGE_YEARS:
        result.verdict = ALLOWED_WITH_EXTRA
        result.documents = [*BASE_DOCUMENTS, CLEARANCE_DOCUMENT]
        result.reasons.append(
            f"Машина старше {SANCTION_AGE_YEARS} лет (возраст {age}), поэтому проходит, "
            "но таможня потребует справку о высоте просвета."
        )
        return result

    result.verdict = SANCTIONED
    result.reasons.append(
        f"Машина новее {SANCTION_AGE_YEARS} лет (возраст {age}) с просветом от {CLEARANCE_THRESHOLD_MM} мм — "
        f"позиция {code} под санкциями."
    )
    result.warnings.append(
        f"Формально провоз возможен только с документом «{MANUFACTURER_DECLARATION}», "
        "но производители его практически не выдают."
    )
    return result


def _car_position(data: CheckInput) -> tuple[str, str, bool, list[str]]:
    """Код КН для легковой машины: ``(код, описание, санкционная ли, чего не хватает)``."""
    engine = data.engine_type
    cc = data.displacement_cc

    if engine == "ELECTRIC":
        return "8703 80", "Легковые только с электрическим двигателем", True, []
    if engine == "HYBRID_PETROL":
        return "8703 40", "Гибрид бензин + электро без зарядки от сети", True, []
    if engine == "HYBRID_DIESEL":
        return "8703 50", "Гибрид дизель + электро без зарядки от сети", True, []
    if engine == "PLUGIN_PETROL":
        return "8703 60", "Гибрид бензин + электро с зарядкой от сети", True, []
    if engine == "PLUGIN_DIESEL":
        return "8703 70", "Гибрид дизель + электро с зарядкой от сети", True, []

    if cc is None:
        label = _ENGINE_LABELS.get(engine, engine)
        return "", f"{label} двигатель", True, ["Объём двигателя, см³"]

    if engine == "DIESEL":
        if cc <= SMALL_ENGINE_CC:
            return "8703 31/32", "Дизель до 1900 см³", False, []
        if cc <= 2500:
            return "8703 32", "Дизель от 1900 до 2500 см³", True, []
        return "8703 33", "Дизель больше 2500 см³", True, []

    # Бензин (и всё, что не распознали как дизель/гибрид/электро).
    if cc <= 1000:
        return "8703 21", "Бензин до 1000 см³", False, []
    if cc <= 1500:
        return "8703 22", "Бензин от 1000 до 1500 см³", False, []
    if cc <= SMALL_ENGINE_CC:
        return "8703 23", "Бензин от 1500 до 1900 см³", False, []
    if cc <= 3000:
        return "8703 23", "Бензин от 1900 до 3000 см³", True, []
    return "8703 24", "Бензин больше 3000 см³", True, []


# ── Данные NHTSA → поля формы ─────────────────────────────────────────────

# Типы кузова, у которых просвет почти всегда от 165 мм. Это подсказка, а не
# истина: клиент видит подставленное значение и может его поправить.
_HIGH_CLEARANCE_BODIES = (
    "sport utility",
    "suv",
    "crossover",
    "pickup",
    "truck",
    "van",
    "minivan",
    "cargo van",
    "incomplete",
)

_LOW_CLEARANCE_BODIES = (
    "sedan",
    "coupe",
    "hatchback",
    "convertible",
    "roadster",
    "wagon",
    "liftback",
    "limousine",
)


def guess_from_nhtsa(details: dict) -> dict:
    """Поля формы проверки по ответу :func:`vin_validator.decode_vin_details`.

    Возвращает то, что можно подставить в форму: категорию, тип двигателя,
    объём, год и предположение о просвете. Всё — с расчётом на правку
    клиентом: NHTSA не отдаёт ни клиренс, ни таможенную категорию.
    """
    body = (details.get("body_class") or "").lower()
    vehicle_type = (details.get("vehicle_type") or "").lower()
    fuel = (details.get("fuel_primary") or "").lower()
    fuel_secondary = (details.get("fuel_secondary") or "").lower()
    electrification = (details.get("electrification") or "").lower()

    return {
        "category": _guess_category(vehicle_type, body),
        "engine_type": _guess_engine(fuel, fuel_secondary, electrification),
        "displacement_cc": details.get("displacement_cc"),
        "year": details.get("year"),
        "clearance": _guess_clearance(body),
        "clearance_hint": details.get("body_class") or "",
    }


def _guess_category(vehicle_type: str, body: str) -> str:
    if "motorcycle" in vehicle_type or "moped" in body:
        return "MOTO"
    # Пикапы и внедорожники по КН всё равно легковые (8703): в 8704 попадают
    # именно грузовые фургоны и шасси с кабиной.
    if "truck" in vehicle_type and ("cargo van" in body or "incomplete" in body or "chassis" in body):
        return "CARGO"
    return "CAR"


def _guess_engine(fuel: str, fuel_secondary: str, electrification: str) -> str:
    is_diesel = "diesel" in fuel or "diesel" in fuel_secondary
    is_plugin = "phev" in electrification or "plug" in electrification
    is_hybrid = "hev" in electrification or "hybrid" in electrification or "hybrid" in fuel
    if "bev" in electrification or (fuel.startswith("electric") and not is_hybrid):
        return "ELECTRIC"
    if is_plugin:
        return "PLUGIN_DIESEL" if is_diesel else "PLUGIN_PETROL"
    if is_hybrid:
        return "HYBRID_DIESEL" if is_diesel else "HYBRID_PETROL"
    return "DIESEL" if is_diesel else "PETROL"


def _guess_clearance(body: str) -> str:
    if any(token in body for token in _HIGH_CLEARANCE_BODIES):
        return "YES"
    if any(token in body for token in _LOW_CLEARANCE_BODIES):
        return "NO"
    return "UNKNOWN"


def _add_value_warning(result: CheckResult, data: CheckInput) -> None:
    """Предупреждение о стоимости выше порога из памятки."""
    limit = VALUE_LIMIT_EUR.get(data.category)
    if limit is None or data.price_eur is None:
        return
    if data.price_eur > limit:
        result.warnings.append(
            f"В памятке для этой позиции указана стоимость до {limit:,.0f} EUR".replace(",", " ")
            + f", а у вас {data.price_eur:,.0f} EUR".replace(",", " ")
            + ". Стоимость выше порога — уточните у брокера, пропустят ли такую машину."
        )
