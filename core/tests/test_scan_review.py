"""
Тесты сверки сканов: отчёт «документ ↔ система» и решения по спорному VIN.

Покрывают:
- сравнение полей тайтла с карточкой машины;
- список машин контейнера, отсортированный по похожести VIN на тайтл;
- разбор машин Dock Receipt (совпала / похожая / чужая / новая);
- расхождение номера контейнера и машины, которых нет в документе;
- защиту от дублей при применении Dock Receipt (похожий VIN уже в базе);
- действия attach / fix_car_vin / force_new и их проверки доступа.

Запуск: pytest core/tests/test_scan_review.py
"""

from __future__ import annotations

import pytest

from core.models import Car, Container
from core.models.scans import ScanProcessingJob
from core.services.scan_applier import apply_job, evaluate_auto_apply
from core.services.scan_review import (
    SEVERITY_ERROR,
    SEVERITY_OK,
    SEVERITY_WARN,
    VEHICLE_ELSEWHERE,
    VEHICLE_FUZZY,
    VEHICLE_MATCHED,
    VEHICLE_NEW,
    build_scan_review,
    resolve_vin_conflict,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def container():
    return Container.objects.create(number="MSDU1234567", status="FLOATING")


def _make_car(vin, container=None, **kwargs):
    defaults = {"year": 2020, "brand": "BMW X5", "status": "FLOATING"}
    defaults.update(kwargs)
    return Car.objects.create(vin=vin, container=container, **defaults)


def _title_job(vins, *, target=None, status=ScanProcessingJob.STATUS_NEEDS_REVIEW, extra=None):
    data = {"vins": vins, "year": 2020, "make": "BMW", "model": "X5"}
    if extra:
        data.update(extra)
    return ScanProcessingJob.objects.create(
        scan_type=ScanProcessingJob.SCAN_TYPE_TITLE,
        status=status,
        extracted_data=data,
        target_container=target,
    )


def _dock_job(data, *, target=None, status=ScanProcessingJob.STATUS_NEEDS_REVIEW):
    return ScanProcessingJob.objects.create(
        scan_type=ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT,
        status=status,
        extracted_data=data,
        target_container=target,
    )


def _field(report, label):
    for row in report["fields"]:
        if row["label"] == label:
            return row
    raise AssertionError(f"В отчёте нет поля {label!r}: {[r['label'] for r in report['fields']]}")


# ── TITLE ──────────────────────────────────────────────────────────────────


def test_title_report_compares_fields_with_matched_car(container):
    car = _make_car("AAA11111111111111", container, year=2019, brand="BMW X3")
    job = _title_job([car.vin], target=container, extra={"title_number": "TX-77", "title_state": "CA"})

    report = build_scan_review(job)

    assert report["matched_car"]["car_id"] == car.id
    assert _field(report, "VIN")["state"] == "same"
    assert _field(report, "Год выпуска")["state"] == "diff"
    # Разное написание модели тревогой не считаем — только показываем.
    assert _field(report, "Марка / модель")["state"] == "diff_soft"
    # Номер тайтла в системе не хранится — справочная строка, не расхождение.
    assert _field(report, "Номер тайтла")["state"] == "info"
    assert report["diff_count"] == 1


def test_title_report_treats_nhtsa_model_as_reference(container):
    car = _make_car("AAA11111111111111", container, year=2022, brand="CHEVROLET Equinox")
    job = _title_job(
        [car.vin],
        target=container,
        extra={
            "year": 2022,
            "make": "CHEVY",
            "model": "EQUINOX LT",
            "vin_validations": [
                {
                    "vin": car.vin,
                    "nhtsa": {"ok": True, "make": "CHEVROLET", "model": "Equinox", "year": 2022},
                    "warnings": [],
                }
            ],
        },
    )

    report = build_scan_review(job)

    assert _field(report, "Марка / модель")["state"] == "info"
    assert _field(report, "NHTSA")["sys"] == "CHEVROLET Equinox (2022)"
    assert report["diff_count"] == 0


def test_title_report_lists_container_cars_by_similarity(container):
    close = _make_car("AAA11111111111111", container)
    far = _make_car("ZZZ99999999999999", container)
    job = _title_job(["AAA11111111111112"], target=container)

    report = build_scan_review(job)

    vins = [row["vin"] for row in report["container_cars"]]
    assert vins == [close.vin, far.vin]
    assert report["container_cars"][0]["distance"] == 1
    # Единственная похожая машина считается целевой, поэтому кнопок на ней нет,
    # а по остальным можно прикрепить тайтл вручную.
    assert report["container_cars"][0]["is_target"] is True
    assert [a["action"] for a in report["container_cars"][1]["actions"]] == ["attach"]


def test_title_report_offers_both_sides_of_vin_conflict(container):
    car = _make_car("AAA11111111111111")
    job = _title_job(
        ["AAA11111111111112"],
        extra={
            "vin_mismatch_review": {
                "extracted_vin": "AAA11111111111112",
                "candidates": [{"vin": car.vin, "car_id": car.id, "hamming_distance": 1}],
            }
        },
    )

    report = build_scan_review(job)

    assert report["severity"] == SEVERITY_ERROR
    candidate = report["conflict"]["candidates"][0]
    assert [a["action"] for a in candidate["actions"]] == ["attach", "fix_car_vin"]
    # Пока конфликт не разрешён, «применить как есть» недоступно.
    apply_action = next(a for a in report["actions"] if a["action"] == "apply")
    assert apply_action["disabled"] is True


def test_title_report_after_apply_is_green(container):
    car = _make_car("AAA11111111111111", container, has_title=True)
    job = _title_job([car.vin], target=container, status=ScanProcessingJob.STATUS_APPLIED)
    job.linked_car = car
    job.save(update_fields=["linked_car"])

    report = build_scan_review(job)

    assert report["severity"] == SEVERITY_OK
    assert car.vin in report["headline"]
    assert report["actions"] == []


def test_title_report_warns_when_vin_not_in_container(container):
    _make_car("AAA11111111111111", container)
    job = _title_job(["ZZZ99999999999999"], target=container)

    report = build_scan_review(job)

    assert report["severity"] == SEVERITY_WARN
    assert "не совпал" in report["headline"]
    assert report["matched_car"] is None


# ── DOCK RECEIPT ───────────────────────────────────────────────────────────


def test_dock_report_classifies_vehicles(container):
    matched = _make_car("AAA11111111111111", container)
    fuzzy = _make_car("BBB22222222222222", container)
    other_container = Container.objects.create(number="TCLU7654321", status="FLOATING")
    elsewhere = _make_car("CCC33333333333333", other_container)

    job = _dock_job(
        {
            "container_number": container.number,
            "vehicles": [
                {"vin": matched.vin},
                {"vin": "BBB22222222222223"},
                {"vin": elsewhere.vin},
                {"vin": "DDD44444444444444"},
            ],
        },
        target=container,
    )

    report = build_scan_review(job)
    states = {row["vin_doc"]: row["state"] for row in report["vehicles"]}

    assert states[matched.vin] == VEHICLE_MATCHED
    assert states["BBB22222222222223"] == VEHICLE_FUZZY
    assert states[elsewhere.vin] == VEHICLE_ELSEWHERE
    assert states["DDD44444444444444"] == VEHICLE_NEW
    fuzzy_row = next(row for row in report["vehicles"] if row["state"] == VEHICLE_FUZZY)
    assert fuzzy_row["vin_sys"] == fuzzy.vin
    assert report["severity"] == SEVERITY_ERROR


def test_dock_report_flags_container_number_mismatch(container):
    _make_car("AAA11111111111111", container)
    job = _dock_job(
        {"container_number": "TCLU0000000", "vehicles": [{"vin": "AAA11111111111111"}]},
        target=container,
    )

    report = build_scan_review(job)

    assert report["severity"] == SEVERITY_ERROR
    assert _field(report, "Номер контейнера")["state"] == "diff"
    assert "не совпадает" in report["headline"]


def test_dock_report_does_not_flag_carrier_naming(container):
    # В документе перевозчик пишется развёрнуто («MSCU, MSC MAUREEN»), в
    # карточке — коротким названием линии. Это не расхождение.
    from core.models import Line

    container.line = Line.objects.create(name="MSC")
    container.save(update_fields=["line"])
    _make_car("AAA11111111111111", container)
    job = _dock_job(
        {
            "container_number": container.number,
            "exporting_carrier": "MSCU, MSC MAUREEN, NH628R",
            "vehicles": [{"vin": "AAA11111111111111"}],
        },
        target=container,
    )

    report = build_scan_review(job)

    assert _field(report, "Морская линия")["state"] == "same"
    assert report["diff_count"] == 0


def test_dock_report_lists_cars_missing_from_document(container):
    in_doc = _make_car("AAA11111111111111", container)
    forgotten = _make_car("BBB22222222222222", container)
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": in_doc.vin}]},
        target=container,
    )

    report = build_scan_review(job)

    assert [row["vin"] for row in report["missing_in_doc"]] == [forgotten.vin]


def test_dock_report_shows_weight_difference(container):
    car = _make_car("AAA11111111111111", container, weight_kg=1500)
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": car.vin, "weight_kg": 1800}]},
        target=container,
    )

    report = build_scan_review(job)
    row = report["vehicles"][0]

    assert row["weight_diff"] is True
    assert row["weight_doc"] == "1800"
    assert row["weight_sys"] == "1500"


# ── Решения по спорному VIN ────────────────────────────────────────────────


def test_resolve_attach_keeps_existing_car_vin(container):
    car = _make_car("AAA11111111111111", container)
    job = _title_job(["AAA11111111111112"], target=container)

    ok, message = resolve_vin_conflict(job, "attach", car_id=car.id)

    job.refresh_from_db()
    car.refresh_from_db()
    assert ok is True
    assert car.vin in message
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert job.linked_car_id == car.id
    assert car.vin == "AAA11111111111111"
    assert car.has_title is True


def test_resolve_fix_car_vin_rewrites_card(container):
    car = _make_car("AAA11111111111111", container)
    job = _title_job(["AAA11111111111112"], target=container)

    ok, _ = resolve_vin_conflict(job, "fix_car_vin", car_id=car.id)

    job.refresh_from_db()
    car.refresh_from_db()
    assert ok is True
    assert car.vin == "AAA11111111111112"
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert job.linked_car_id == car.id


def test_resolve_fix_car_vin_refuses_on_duplicate(container):
    car = _make_car("AAA11111111111111", container)
    _make_car("AAA11111111111112")  # VIN из тайтла уже занят другой карточкой
    job = _title_job(["AAA11111111111112"], target=container)

    ok, message = resolve_vin_conflict(job, "fix_car_vin", car_id=car.id)

    car.refresh_from_db()
    assert ok is False
    assert "уже занят" in message
    assert car.vin == "AAA11111111111111"


def test_resolve_force_new_creates_car(container):
    _make_car("AAA11111111111111", container)
    job = _title_job(["AAA11111111111112"], target=container)

    ok, _ = resolve_vin_conflict(job, "force_new")

    job.refresh_from_db()
    assert ok is True
    assert job.created_new_car is True
    assert Car.objects.get(vin="AAA11111111111112").container_id == container.id


def test_resolve_rejects_car_outside_allowed_set(container):
    stranger = _make_car("ZZZ99999999999999")  # ни в контейнере, ни в кандидатах
    job = _title_job(["AAA11111111111112"], target=container)

    ok, message = resolve_vin_conflict(job, "attach", car_id=stranger.id)

    job.refresh_from_db()
    assert ok is False
    assert "не из списка" in message
    assert job.status == ScanProcessingJob.STATUS_NEEDS_REVIEW


def test_resolve_rejects_already_applied_job(container):
    car = _make_car("AAA11111111111111", container)
    job = _title_job([car.vin], target=container, status=ScanProcessingJob.STATUS_APPLIED)

    ok, message = resolve_vin_conflict(job, "attach", car_id=car.id)

    assert ok is False
    assert "применять нечего" in message


# ── Dock Receipt не создаёт дубли ──────────────────────────────────────────


def test_dock_receipt_does_not_create_duplicate_for_similar_vin(container):
    """Сценарий «машины завели руками, потом пришёл документ».

    Оператор ошибся в одном символе VIN. Раньше applier не находил точного
    совпадения и молча заводил вторую карточку на ту же машину.
    """
    car = _make_car("AAA11111111111111", container)
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": "AAA11111111111112"}]},
        target=container,
    )

    apply_job(job)

    job.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_NEEDS_REVIEW
    assert Car.objects.filter(container=container).count() == 1
    assert "похож на существующий" in job.error_message
    assert job.extracted_data["vin_conflicts"][0]["extracted_vin"] == "AAA11111111111112"
    assert job.extracted_data["vin_conflicts"][0]["candidates"][0]["car_id"] == car.id


def test_dock_receipt_creates_car_when_no_similar_vin(container):
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": "AAA11111111111111"}]},
        target=container,
    )

    apply_job(job)

    job.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert Car.objects.get(vin="AAA11111111111111").container_id == container.id


def test_dock_conflict_report_offers_both_sides(container):
    car = _make_car("AAA11111111111111", container)
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": "AAA11111111111112"}]},
        target=container,
    )
    apply_job(job)
    job.refresh_from_db()

    report = build_scan_review(job)

    assert report["severity"] == SEVERITY_ERROR
    assert len(report["conflicts"]) == 1
    actions = report["conflicts"][0]["candidates"][0]["actions"]
    assert [a["action"] for a in actions] == ["attach", "fix_car_vin"]
    assert all(a["doc_vin"] == "AAA11111111111112" for a in actions)
    # Пока конфликт не разобран, «Применить как есть» недоступно.
    apply_action = next(a for a in report["actions"] if a["action"] == "apply")
    assert apply_action["disabled"] is True
    assert car.vin == "AAA11111111111111"


def test_dock_resolve_attach_binds_document_vin_to_existing_car(container):
    car = _make_car("AAA11111111111111", container)
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": "AAA11111111111112", "weight_kg": 1700}]},
        target=container,
    )
    apply_job(job)
    job.refresh_from_db()

    ok, message = resolve_vin_conflict(job, "attach", car_id=car.id, doc_vin="AAA11111111111112")

    job.refresh_from_db()
    car.refresh_from_db()
    assert ok is True, message
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert car.vin == "AAA11111111111111"
    assert car.weight_kg == 1700
    assert Car.objects.filter(container=container).count() == 1


def test_dock_resolve_fix_car_vin_rewrites_card(container):
    car = _make_car("AAA11111111111111", container)
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": "AAA11111111111112"}]},
        target=container,
    )
    apply_job(job)
    job.refresh_from_db()

    ok, message = resolve_vin_conflict(job, "fix_car_vin", car_id=car.id, doc_vin="AAA11111111111112")

    car.refresh_from_db()
    job.refresh_from_db()
    assert ok is True, message
    assert car.vin == "AAA11111111111112"
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert Car.objects.filter(container=container).count() == 1


def test_dock_resolve_force_new_creates_second_car(container):
    _make_car("AAA11111111111111", container)
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": "AAA11111111111112"}]},
        target=container,
    )
    apply_job(job)
    job.refresh_from_db()

    ok, message = resolve_vin_conflict(job, "force_new")

    job.refresh_from_db()
    assert ok is True, message
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert Car.objects.filter(container=container).count() == 2


def test_dock_resolve_waits_until_every_conflict_is_settled(container):
    first = _make_car("AAA11111111111111", container)
    _make_car("BBB22222222222222", container)
    job = _dock_job(
        {
            "container_number": container.number,
            "vehicles": [{"vin": "AAA11111111111112"}, {"vin": "BBB22222222222223"}],
        },
        target=container,
    )
    apply_job(job)
    job.refresh_from_db()
    assert len(job.extracted_data["vin_conflicts"]) == 2

    ok, message = resolve_vin_conflict(job, "attach", car_id=first.id, doc_vin="AAA11111111111112")

    job.refresh_from_db()
    assert ok is True
    assert "Осталось разобрать спорных VIN: 1" in message
    assert job.status == ScanProcessingJob.STATUS_NEEDS_REVIEW


def test_dock_auto_apply_refuses_on_similar_vin(container):
    _make_car("AAA11111111111111", container)
    job = _dock_job(
        {"container_number": container.number, "vehicles": [{"vin": "AAA11111111111112"}]},
        target=container,
    )

    ok, reason = evaluate_auto_apply(job)

    assert ok is False
    assert "похож на существующий" in reason


# ── Экраны ─────────────────────────────────────────────────────────────────


def test_container_panel_renders_document_summary(admin_client, container):
    _make_car("AAA11111111111111", container)
    _make_car("BBB22222222222222", container, has_title=True)

    response = admin_client.get(f"/admin/core/container/{container.pk}/change/")

    assert response.status_code == 200
    assert "Документы и сверка" in response.content.decode()


def test_container_scan_jobs_endpoint_returns_summary_and_headline(admin_client, container):
    _make_car("AAA11111111111111", container)
    job = _title_job(["ZZZ99999999999999"], target=container)

    payload = admin_client.get(f"/admin/core/container/{container.pk}/scan-jobs/").json()

    assert payload["summary"]["cars_total"] == 1
    assert payload["summary"]["needs_review"] == 1
    assert payload["summary"]["dock_receipt"] is False
    item = next(row for row in payload["jobs"] if row["id"] == job.pk)
    assert item["severity"] == SEVERITY_WARN
    assert "не совпал" in item["headline"]


def test_container_review_fragment_renders(admin_client, container):
    car = _make_car("AAA11111111111111", container)
    job = _title_job(["AAA11111111111112"], target=container)

    response = admin_client.get(f"/admin/core/container/{container.pk}/scan-jobs/{job.pk}/review/")

    assert response.status_code == 200
    html = response.content.decode()
    assert "Машины контейнера" in html
    assert car.vin[:6] in html


def test_container_review_fragment_rejects_foreign_job(admin_client, container):
    other = Container.objects.create(number="TCLU0000002", status="FLOATING")
    job = _title_job(["AAA11111111111111"], target=other)

    response = admin_client.get(f"/admin/core/container/{container.pk}/scan-jobs/{job.pk}/review/")

    assert response.status_code == 404


def test_job_page_renders_review_block(admin_client, container):
    car = _make_car("AAA11111111111111")
    job = _title_job(
        ["AAA11111111111112"],
        extra={
            "vin_mismatch_review": {
                "extracted_vin": "AAA11111111111112",
                "candidates": [{"vin": car.vin, "car_id": car.id, "hamming_distance": 1}],
            }
        },
    )

    response = admin_client.get(f"/admin/core/scanprocessingjob/{job.pk}/change/")

    assert response.status_code == 200
    html = response.content.decode()
    assert "Спорный VIN" in html
    assert "Верен VIN в базе" in html


@pytest.mark.parametrize(
    "file_name,expected",
    [
        ("scans/title.pdf", "pdf"),
        ("scans/TITLE.PDF", "pdf"),
        ("scans/photo.jpg", "image"),
        ("scans/photo.PNG", "image"),
        ("scans/doc.docx", "other"),
        ("", "other"),
    ],
)
def test_report_marks_file_kind(file_name, expected):
    """Просмотрщику нужно заранее знать, чем открывать скан."""
    job = _title_job(["AAA11111111111111"])
    if file_name:
        job.original_file.name = file_name
        job.save(update_fields=["original_file"])

    assert build_scan_review(job)["file_kind"] == expected


def test_job_page_renders_zoomable_doc_viewer(admin_client):
    """Скан отдаётся просмотрщику с зумом, а не голому iframe."""
    job = _title_job(["AAA11111111111111"])
    job.original_file.name = "scans/title.pdf"
    job.save(update_fields=["original_file"])

    html = admin_client.get(f"/admin/core/scanprocessingjob/{job.pk}/change/").content.decode()

    assert "js-sr-doc" in html
    assert 'data-file-kind="pdf"' in html
    assert "scan_doc_viewer.js" in html
    # iframe остаётся только как запасной вариант для браузера без JS.
    assert html.count("cm-sr-doc-frame") == 1
    assert "<noscript>" in html


def test_panel_resolve_action_applies_job(admin_client, container):
    car = _make_car("AAA11111111111111", container)
    job = _title_job(["AAA11111111111112"], target=container)

    response = admin_client.post(
        f"/admin/core/container/{container.pk}/scan-jobs/{job.pk}/action/",
        {"action": "attach", "car_id": car.id},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    job.refresh_from_db()
    car.refresh_from_db()
    assert job.status == ScanProcessingJob.STATUS_APPLIED
    assert car.has_title is True
