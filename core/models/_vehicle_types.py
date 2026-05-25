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
    ('RV', 'Автодом (RV)'),
    ('CONSTRUCTION', 'Стр. техника'),
]
