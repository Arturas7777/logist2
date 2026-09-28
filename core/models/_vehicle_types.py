"""Общие choices для типов транспортных средств.

Используется в :class:`core.models.cars.Car`, :class:`core.models.lines.LineTHSCoefficient`
и :class:`core.models.clients.ClientTariffRate`.
"""

VEHICLE_TYPE_CHOICES = [
    ('SEDAN', 'Легковой'),
    ('CROSSOVER', 'Кроссовер'),
    ('SUV', 'Джип'),
    ('PICKUP', 'Пикап'),
    ('NEW_CAR', 'Новая машина'),
    ('MOTO', 'Мотоцикл'),
    ('BIG_MOTO', 'Большой мотоцикл'),
    ('ATV', 'Квадроцикл/Багги'),
    ('BOAT', 'Лодка'),
    ('JETSKI', 'Гидроцикл'),
    ('SNOWMOBILE', 'Снегоход'),
    ('RV', 'Автодом (RV)'),
    ('CONSTRUCTION', 'Стр. техника'),
]

# У гидроцикла номер корпуса (HIN) обычно 12 символов, у снегохода серийный
# номер тоже короче автомобильного VIN. Для этих типов 8–17 символов — норма.
SHORT_VIN_VEHICLE_TYPES = frozenset({"JETSKI", "SNOWMOBILE"})
SHORT_VIN_MIN_LENGTH = 8
STANDARD_VIN_LENGTH = 17


def vin_length_error(vin: str | None, vehicle_type: str | None) -> str | None:
    """Сообщение, если длина номера недопустима для типа ТС. Иначе None."""
    if not vin:
        return None
    length = len(vin)
    if vehicle_type in SHORT_VIN_VEHICLE_TYPES:
        if SHORT_VIN_MIN_LENGTH <= length <= STANDARD_VIN_LENGTH:
            return None
        return f"Номер содержит {length} символов. Для гидроцикла и снегохода нужно от 8 до 17."
    if length != STANDARD_VIN_LENGTH:
        return "VIN должен содержать ровно 17 символов."
    return None
