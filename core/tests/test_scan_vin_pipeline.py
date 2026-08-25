"""
Тесты пайплайна распознавания VIN со сканов (vin_corrector + scan_applier).

Покрывают:
- нормализацию запрещённых символов I/O/Q;
- автокоррекцию VIN по контрольной цифре (таблица OCR-путаниц);
- скоринг уверенности high/medium/low;
- матчинг тайтла по машинам контейнера (target_container);
- решение об авто-применении (evaluate_auto_apply).

NHTSA в тестах не вызывается (use_nhtsa=False / данные подкладываются
готовыми dict-ами).

Запуск: pytest core/tests/test_scan_vin_pipeline.py
"""

from __future__ import annotations

import pytest

from core.models import Car, Container
from core.models.scans import ScanProcessingJob
from core.services.scan_applier import apply_title_job, evaluate_auto_apply, find_similar_vins
from core.services.vin_corrector import (
    assess_vin_confidence,
    correct_vin_by_checksum,
    hamming_distance,
    normalize_vin,
    process_extracted_vin,
)
from core.services.vin_validator import is_vin_checksum_valid, vin_check_digit


def make_valid_vin(base: str = "1HGCM82633A004352") -> str:
    """Строит VIN с гарантированно валидной контрольной цифрой."""
    cd = vin_check_digit(base)
    assert cd is not None
    return base[:8] + cd + base[9:]


# ── normalize_vin ──────────────────────────────────────────────────────────


def test_normalize_vin_replaces_forbidden_chars():
    vin, changes = normalize_vin(" 1hgcm82633aO0435I ")
    assert vin == "1HGCM82633A004351"
    # O → 0 и I → 1 зафиксированы в changes
    assert any("O → 0" in c for c in changes)
    assert any("I → 1" in c for c in changes)


def test_normalize_vin_no_changes_for_clean_vin():
    vin, changes = normalize_vin("1HGCM82633A004352")
    assert vin == "1HGCM82633A004352"
    assert changes == []


def test_hamming_distance():
    assert hamming_distance("ABC", "ABD") == 1
    assert hamming_distance("ABC", "AB") is None
    assert hamming_distance("ABC", "ABC") == 0


# ── correct_vin_by_checksum ────────────────────────────────────────────────


def test_checksum_correction_recovers_ocr_error():
    valid = make_valid_vin()
    assert is_vin_checksum_valid(valid)
    # Испортим символ типичной OCR-путаницей: 5 → S (в хвосте VIN).
    pos = valid.rindex("5")
    corrupted = valid[:pos] + "S" + valid[pos + 1 :]
    assert not is_vin_checksum_valid(corrupted)

    res = correct_vin_by_checksum(corrupted)
    assert res["applicable"] is True
    assert valid in res["candidates"]


def test_checksum_correction_not_applicable_for_valid_vin():
    valid = make_valid_vin()
    res = correct_vin_by_checksum(valid)
    assert res["applicable"] is False
    assert res["corrected"] is None


def test_checksum_correction_not_applicable_for_eu_vin():
    # W* (Германия) — не North American, контрольная цифра не обязана сходиться.
    res = correct_vin_by_checksum("WBAJA5C58JG123456")
    assert res["applicable"] is False


# ── assess_vin_confidence ──────────────────────────────────────────────────


def _validation(**overrides):
    base = {
        "vin": "1HGCM82633A004352",
        "length_ok": True,
        "checksum_ok": True,
        "region_north_american": True,
        "nhtsa": {"ok": True, "make": "HONDA", "model": "Accord", "year": 2003, "raw_failed": False},
        "warnings": [],
        "suggested_vin": "",
    }
    base.update(overrides)
    return base


def test_confidence_high_with_checksum_and_nhtsa():
    res = assess_vin_confidence(_validation())
    assert res["level"] == "high"


def test_confidence_medium_when_nhtsa_unavailable():
    res = assess_vin_confidence(
        _validation(
            nhtsa={"raw_failed": True, "make": None, "year": None},
            warnings=["NHTSA API недоступен — пропустили проверку."],
        )
    )
    assert res["level"] == "medium"


def test_confidence_low_on_blocking_warning():
    res = assess_vin_confidence(_validation(warnings=["Год не совпадает: AI прочитал 2024, VIN → 2017."]))
    assert res["level"] == "low"


def test_confidence_low_on_second_pass_disagreement():
    res = assess_vin_confidence(
        _validation(
            nhtsa={"raw_failed": True, "make": None, "year": None},
            warnings=["NHTSA API недоступен — пропустили проверку."],
        ),
        second_pass_agrees=False,
    )
    assert res["level"] == "low"


def test_confidence_medium_when_second_pass_disagrees_but_nhtsa_confirms():
    res = assess_vin_confidence(_validation(), second_pass_agrees=False)
    assert res["level"] == "medium"


def test_confidence_corrected_vin_requires_nhtsa_for_high():
    # Автокоррекция + NHTSA подтвердил → high.
    assert assess_vin_confidence(_validation(), was_corrected=True)["level"] == "high"
    # Автокоррекция без NHTSA → не выше medium.
    res = assess_vin_confidence(
        _validation(
            nhtsa={"raw_failed": True, "make": None, "year": None},
            warnings=["NHTSA API недоступен — пропустили проверку."],
        ),
        was_corrected=True,
    )
    assert res["level"] == "medium"


def test_confidence_low_without_validation():
    assert assess_vin_confidence(None)["level"] == "low"
    assert assess_vin_confidence({"length_ok": False})["level"] == "low"


# ── process_extracted_vin (offline) ────────────────────────────────────────


def test_process_extracted_vin_normalizes_and_corrects():
    valid = make_valid_vin()
    pos = valid.rindex("5")
    corrupted = valid[:pos] + "S" + valid[pos + 1 :]
    res = process_extracted_vin(corrupted, use_nhtsa=False)
    # Если кандидат единственный — VIN исправлен; иначе остаётся как есть,
    # но кандидаты перечислены.
    if res["was_corrected"]:
        assert res["vin"] == valid
    else:
        assert valid in res["checksum_candidates"]


def test_process_extracted_vin_second_pass_agreement():
    valid = make_valid_vin()
    res = process_extracted_vin(valid, second_pass_vin=valid.lower(), use_nhtsa=False)
    assert res["second_pass_agrees"] is True
    res2 = process_extracted_vin(valid, second_pass_vin="9" + valid[1:], use_nhtsa=False)
    assert res2["second_pass_agrees"] is False


def _nhtsa_chevrolet_equinox(vin, *, timeout=5):
    return {
        "ok": True,
        "make": "CHEVROLET",
        "model": "Equinox",
        "year": 2022,
        "error_code": "0",
        "error_text": "",
        "suggested_vin": "",
        "raw_failed": False,
    }


def test_chevy_ocr_does_not_lower_vin_confidence(monkeypatch):
    monkeypatch.setattr("core.services.vin_validator.decode_vin_nhtsa", _nhtsa_chevrolet_equinox)
    from core.services.vin_validator import cross_check_with_ai_data

    result = cross_check_with_ai_data(
        "2GNAXKEV7N6140570",
        ai_make="CHEVY",
        ai_model="EQUINOX LT",
        ai_year=2022,
    )
    assert result["warnings"] == []
    assert assess_vin_confidence(result)["level"] == "high"


def test_model_in_make_field_does_not_count_as_mismatch(monkeypatch):
    monkeypatch.setattr("core.services.vin_validator.decode_vin_nhtsa", _nhtsa_chevrolet_equinox)
    from core.services.vin_validator import cross_check_with_ai_data

    result = cross_check_with_ai_data("2GNAXKEV7N6140570", ai_make="EQUINOX", ai_year=2022)
    assert result["warnings"] == []


def test_real_make_mismatch_is_still_reported(monkeypatch):
    monkeypatch.setattr("core.services.vin_validator.decode_vin_nhtsa", _nhtsa_chevrolet_equinox)
    from core.services.vin_validator import cross_check_with_ai_data

    result = cross_check_with_ai_data("2GNAXKEV7N6140570", ai_make="TOYOTA", ai_year=2022)
    assert any("Производитель не совпадает" in w for w in result["warnings"])


# ── Хелперы для DB-тестов ──────────────────────────────────────────────────


@pytest.fixture
def container(db):
    return Container.objects.create(number="MSDU1234567", status="FLOATING")


def _make_car(vin, container=None, **kwargs):
    defaults = {"year": 2020, "brand": "BMW X5", "status": "FLOATING"}
    defaults.update(kwargs)
    return Car.objects.create(vin=vin, container=container, **defaults)


def _make_title_job(vins, *, target=None, status=ScanProcessingJob.STATUS_NEEDS_REVIEW, extra=None):
    data = {"vins": vins, "year": 2020, "make": "BMW", "model": "X5"}
    if extra:
        data.update(extra)
    return ScanProcessingJob.objects.create(
        scan_type=ScanProcessingJob.SCAN_TYPE_TITLE,
        status=status,
        extracted_data=data,
        target_container=target,
    )


# ── find_similar_vins с queryset ───────────────────────────────────────────


def test_find_similar_vins_respects_queryset(db, container):
    in_container = _make_car("AAA11111111111111", container)
    _make_car("AAA11111111111119")  # похожий, но вне контейнера

    similar_all = find_similar_vins("AAA11111111111112")
    assert len(similar_all) == 2

    similar_ctx = find_similar_vins("AAA11111111111112", queryset=container.container_cars.all())
    assert len(similar_ctx) == 1
    assert similar_ctx[0][1] == in_container.id


# ── apply_title_job: контекст контейнера ───────────────────────────────────


def test_title_fuzzy_match_within_container(db, container):
    car = _make_car("AAA11111111111111", container)
    # OCR прочитал VIN с одной ошибкой; в контейнере ровно одна похожая машина.
    job = _make_title_job(["AAA11111111111112"], target=container)

    apply_title_job(job)

    job.refresh_from_db()
    car.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert job.linked_car_id == car.id
    assert job.created_new_car is False
    assert car.has_title is True
    assert job.extracted_data["vins"][0] == car.vin
    assert "vin_context_match" in job.extracted_data


def test_title_no_match_in_container_goes_to_review(db, container):
    _make_car("AAA11111111111111", container)
    # VIN совсем другой — ни точного, ни fuzzy-совпадения нигде.
    job = _make_title_job(["ZZZ99999999999999"], target=container)

    apply_title_job(job)

    job.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_NEEDS_REVIEW
    assert "не совпал" in job.error_message
    assert not Car.objects.filter(vin="ZZZ99999999999999").exists()


def test_title_force_new_creates_car_in_target_container(db, container):
    job = _make_title_job(["ZZZ99999999999999"], target=container, extra={"skip_vin_check": True})

    apply_title_job(job)

    job.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    new_car = Car.objects.get(vin="ZZZ99999999999999")
    assert new_car.container_id == container.id


def test_title_new_car_uses_nhtsa_brand_over_ocr(db):
    job = _make_title_job(
        ["ZZZ99999999999999"],
        extra={
            "skip_vin_check": True,
            "make": "CHEVY",
            "model": "EQUINOX LT",
            "year": 2021,
            "vin_validations": [
                {
                    "vin": "ZZZ99999999999999",
                    "nhtsa": {"ok": True, "make": "CHEVROLET", "model": "Equinox", "year": 2022},
                }
            ],
        },
    )

    apply_title_job(job)

    car = Car.objects.get(vin="ZZZ99999999999999")
    assert car.brand == "CHEVROLET Equinox"
    assert car.year == 2022


def test_title_apply_does_not_touch_title_notes(db, container):
    # title_notes — поле только для ручных заметок оператора: применение
    # тайтла ставит has_title, но ничего не дописывает в заметку.
    car = _make_car("AAA11111111111111", container, title_notes="моя ручная заметка")
    job = _make_title_job([car.vin], target=container, extra={"title_number": "X1", "title_state": "CA"})

    apply_title_job(job)

    car.refresh_from_db()
    assert car.has_title is True
    assert car.title_notes == "моя ручная заметка"


def test_title_exact_match_ignores_container(db, container):
    # Точное совпадение VIN работает и без контекста контейнера.
    car = _make_car("BBB22222222222222")
    job = _make_title_job(["BBB22222222222222"])

    apply_title_job(job)

    job.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert job.linked_car_id == car.id


# ── evaluate_auto_apply ────────────────────────────────────────────────────


def test_auto_apply_title_exact_match_in_container(db, container):
    car = _make_car("AAA11111111111111", container)
    job = _make_title_job(
        [car.vin],
        target=container,
        extra={"vin_confidences": [{"vin": car.vin, "level": "high", "reasons": []}]},
    )
    ok, reason = evaluate_auto_apply(job)
    assert ok is True
    assert car.vin in reason


def test_auto_apply_title_rejects_low_confidence(db, container):
    car = _make_car("AAA11111111111111", container)
    job = _make_title_job(
        [car.vin],
        target=container,
        extra={"vin_confidences": [{"vin": car.vin, "level": "low", "reasons": []}]},
    )
    ok, _ = evaluate_auto_apply(job)
    assert ok is False


def test_auto_apply_title_low_confidence_ok_when_nhtsa_confirms(db, container):
    car = _make_car("2GNAXKEV7N6140570", container, brand="CHEVROLET EQUINOX", year=2022)
    job = _make_title_job(
        [car.vin],
        target=container,
        extra={
            "make": "CHEVROLET",
            "model": "EQUINOX LT",
            "year": 2022,
            "vin_confidences": [
                {
                    "vin": car.vin,
                    "level": "low",
                    "reasons": ["повторное посимвольное чтение дало другой VIN"],
                }
            ],
            "vin_validations": [
                {
                    "vin": car.vin,
                    "nhtsa": {"ok": True, "make": "CHEVROLET", "model": "Equinox", "year": 2022},
                }
            ],
        },
    )
    ok, reason = evaluate_auto_apply(job)
    assert ok is True
    assert car.vin in reason


def test_auto_apply_title_rejects_car_from_other_container(db, container):
    other = Container.objects.create(number="TCLU7654321", status="FLOATING")
    car = _make_car("AAA11111111111111", other)
    job = _make_title_job(
        [car.vin],
        target=container,
        extra={"vin_confidences": [{"vin": car.vin, "level": "high", "reasons": []}]},
    )
    ok, reason = evaluate_auto_apply(job)
    assert ok is False
    assert "вне контейнера" in reason


def test_auto_apply_title_fuzzy_unique_match(db, container):
    # В БД валидный NA-VIN; OCR ошибся в одном символе.
    db_vin = make_valid_vin()
    _make_car(db_vin, container)
    ocr_vin = db_vin[:-1] + ("3" if db_vin[-1] != "3" else "4")
    job = _make_title_job([ocr_vin], target=container)
    ok, reason = evaluate_auto_apply(job)
    assert ok is True
    assert db_vin in reason


def test_auto_apply_title_rejects_new_car(db):
    job = _make_title_job(
        ["ZZZ99999999999999"],
        extra={"vin_confidences": [{"vin": "ZZZ99999999999999", "level": "high", "reasons": []}]},
    )
    ok, reason = evaluate_auto_apply(job)
    assert ok is False
    assert "не найден" in reason


def _make_dock_job(vehicles, *, target, container_number=None):
    data = {
        "container_number": container_number if container_number is not None else (target.number if target else ""),
        "vehicles": vehicles,
    }
    return ScanProcessingJob.objects.create(
        scan_type=ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT,
        status=ScanProcessingJob.STATUS_NEEDS_REVIEW,
        extracted_data=data,
        target_container=target,
    )


def test_auto_apply_dock_receipt_all_high(db, container):
    vehicles = [
        {"vin": "AAA11111111111111", "vin_confidence": {"level": "high"}},
        {"vin": "BBB22222222222222", "vin_confidence": {"level": "high"}},
    ]
    job = _make_dock_job(vehicles, target=container)
    ok, _ = evaluate_auto_apply(job)
    assert ok is True


def test_auto_apply_dock_receipt_rejects_number_mismatch(db, container):
    vehicles = [{"vin": "AAA11111111111111", "vin_confidence": {"level": "high"}}]
    job = _make_dock_job(vehicles, target=container, container_number="XXXU0000000")
    ok, reason = evaluate_auto_apply(job)
    assert ok is False
    assert "не совпадает" in reason


def test_auto_apply_dock_receipt_rejects_uncertain_new_vin(db, container):
    vehicles = [{"vin": "AAA11111111111111", "vin_confidence": {"level": "medium"}}]
    job = _make_dock_job(vehicles, target=container)
    ok, reason = evaluate_auto_apply(job)
    assert ok is False
    assert "уверенность" in reason


def test_auto_apply_dock_receipt_allows_existing_car_medium(db, container):
    _make_car("AAA11111111111111")
    vehicles = [{"vin": "AAA11111111111111", "vin_confidence": {"level": "medium"}}]
    job = _make_dock_job(vehicles, target=container)
    ok, _ = evaluate_auto_apply(job)
    assert ok is True


def test_auto_apply_dock_receipt_requires_target(db):
    vehicles = [{"vin": "AAA11111111111111", "vin_confidence": {"level": "high"}}]
    job = _make_dock_job(vehicles, target=None, container_number="MSDU1234567")
    ok, reason = evaluate_auto_apply(job)
    assert ok is False
    assert "без привязки" in reason


# ── AJAX-действия из панели документов карточки контейнера ────────────────


def _action_url(container, job) -> str:
    return f"/admin/core/container/{container.pk}/scan-jobs/{job.pk}/action/"


def test_panel_action_apply(admin_client, container):
    car = _make_car("AAA11111111111111", container)
    job = _make_title_job([car.vin], target=container)

    resp = admin_client.post(_action_url(container, job), {"action": "apply"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    job.refresh_from_db()
    car.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert car.has_title is True


def test_panel_action_apply_force_creates_car(admin_client, container):
    job = _make_title_job(["ZZZ99999999999999"], target=container)

    resp = admin_client.post(_action_url(container, job), {"action": "apply_force"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    job.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    new_car = Car.objects.get(vin="ZZZ99999999999999")
    assert new_car.container_id == container.pk


def test_panel_action_apply_stays_in_review_on_conflict(admin_client, container):
    # VIN не совпал с машинами контейнера → applier откладывает в review,
    # эндпоинт честно возвращает ok=False с причиной.
    _make_car("AAA11111111111111", container)
    job = _make_title_job(["ZZZ99999999999999"], target=container)

    resp = admin_client.post(_action_url(container, job), {"action": "apply"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    job.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_NEEDS_REVIEW


def test_panel_action_ignore(admin_client, container):
    job = _make_title_job(["AAA11111111111111"], target=container)

    resp = admin_client.post(_action_url(container, job), {"action": "ignore"})

    assert resp.status_code == 200
    job.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_IGNORED


def test_panel_action_rejects_foreign_job(admin_client, container, db):
    other = Container.objects.create(number="TCLU0000001", status="FLOATING")
    job = _make_title_job(["AAA11111111111111"], target=other)

    resp = admin_client.post(_action_url(container, job), {"action": "apply"})

    assert resp.status_code == 404


def test_panel_action_rejects_applied_job(admin_client, container):
    job = _make_title_job(["AAA11111111111111"], target=container, status=ScanProcessingJob.STATUS_APPLIED)

    resp = admin_client.post(_action_url(container, job), {"action": "apply"})

    assert resp.status_code == 400
