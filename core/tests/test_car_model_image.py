"""Подбор картинки модели авто по марке и году поколения.

Запуск: pytest core/tests/test_car_model_image.py
"""

from __future__ import annotations

from types import SimpleNamespace

from core.services.car_model_image import select_car_model_image


def _rec(brand, year):
    return SimpleNamespace(brand=brand, year=year)


BMW_2018 = _rec("BMW 430I", 2018)
BMW_2024 = _rec("BMW 430I", 2024)
BMW_ANY = _rec("BMW 430I", None)
BMW_GENERIC = _rec("BMW", 2018)


class TestSelectCarModelImage:
    def test_exact_year_wins(self):
        match = select_car_model_image([BMW_2018, BMW_2024], 2024, "BMW 430I")
        assert match is BMW_2024

    def test_older_generation_for_in_between_year(self):
        match = select_car_model_image([BMW_2018, BMW_2024], 2020, "BMW 430I")
        assert match is BMW_2018

    def test_newer_photo_does_not_apply_to_older_car(self):
        match = select_car_model_image([BMW_2024], 2018, "BMW 430I")
        assert match is None

    def test_latest_generation_not_newer_than_car(self):
        match = select_car_model_image(
            [BMW_2018, _rec("BMW 430I", 2014), BMW_2024],
            2022,
            "BMW 430I",
        )
        assert match is BMW_2018

    def test_yearless_used_when_no_generation_fits(self):
        match = select_car_model_image([BMW_2024, BMW_ANY], 2015, "BMW 430I")
        assert match is BMW_ANY

    def test_generation_beats_yearless(self):
        match = select_car_model_image([BMW_2018, BMW_ANY], 2020, "BMW 430I")
        assert match is BMW_2018

    def test_exact_brand_beats_prefix(self):
        match = select_car_model_image([BMW_GENERIC, BMW_2018], 2018, "BMW 430I")
        assert match is BMW_2018

    def test_prefix_brand_with_generation(self):
        match = select_car_model_image([BMW_GENERIC], 2020, "BMW 430I")
        assert match is BMW_GENERIC

    def test_prefix_newer_generation_skipped(self):
        match = select_car_model_image([_rec("BMW", 2024)], 2018, "BMW 430I")
        assert match is None

    def test_case_insensitive_brand(self):
        match = select_car_model_image([BMW_2018], 2018, "bmw 430i")
        assert match is BMW_2018

    def test_empty_brand(self):
        assert select_car_model_image([BMW_2018], 2018, "") is None
        assert select_car_model_image([BMW_2018], 2018, None) is None

    def test_unrelated_brand_ignored(self):
        match = select_car_model_image([BMW_2018], 2018, "Toyota Camry")
        assert match is None
