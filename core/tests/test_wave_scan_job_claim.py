"""L6: скан-задача захватывается условным UPDATE, зависшие PROCESSING чистятся."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from core.models.scans import ScanProcessingJob
from core.tasks import process_scan_job, reset_stuck_scan_jobs

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


def _job(status=ScanProcessingJob.STATUS_PENDING):
    job = ScanProcessingJob(scan_type=ScanProcessingJob.SCAN_TYPE_TITLE, status=status)
    job.original_file.save("scan.pdf", ContentFile(b"%PDF-1.4"), save=False)
    job.save()
    return job


def test_second_worker_does_not_process_claimed_job():
    job = _job()
    original_filter = ScanProcessingJob.objects.filter

    def racing_filter(*args, **kwargs):
        # Между чтением задачи и захватом её успел взять другой воркер.
        if "status__in" in kwargs:
            ScanProcessingJob.objects.all().update(status=ScanProcessingJob.STATUS_PROCESSING)
        return original_filter(*args, **kwargs)

    with (
        patch.object(ScanProcessingJob.objects, "filter", side_effect=racing_filter),
        patch("core.services.scan_extractor.extract_title") as extract,
    ):
        result = process_scan_job.run(job.pk)

    assert result == {"ok": False, "reason": "already_claimed"}
    extract.assert_not_called()


def test_claim_sets_processing_started_at():
    job = _job()
    seen = {}

    def fake_extract(path):
        row = ScanProcessingJob.objects.get(pk=job.pk)
        seen["status"], seen["started"] = row.status, row.processing_started_at
        return {}

    with patch("core.services.scan_extractor.extract_title", side_effect=fake_extract):
        process_scan_job.run(job.pk)

    assert seen["status"] == ScanProcessingJob.STATUS_PROCESSING
    assert seen["started"] is not None


def test_reset_stuck_scan_jobs():
    old = timezone.now() - timedelta(hours=1)
    stuck = _job(ScanProcessingJob.STATUS_PROCESSING)
    legacy = _job(ScanProcessingJob.STATUS_PROCESSING)
    fresh = _job(ScanProcessingJob.STATUS_PROCESSING)
    review = _job(ScanProcessingJob.STATUS_NEEDS_REVIEW)
    ScanProcessingJob.objects.filter(pk=stuck.pk).update(processing_started_at=old)
    ScanProcessingJob.objects.filter(pk=legacy.pk).update(created_at=old)
    ScanProcessingJob.objects.filter(pk=fresh.pk).update(processing_started_at=timezone.now(), created_at=old)
    ScanProcessingJob.objects.filter(pk=review.pk).update(processing_started_at=old)

    assert reset_stuck_scan_jobs() == 2

    statuses = dict(ScanProcessingJob.objects.values_list("pk", "status"))
    assert statuses[stuck.pk] == ScanProcessingJob.STATUS_ERROR
    assert statuses[legacy.pk] == ScanProcessingJob.STATUS_ERROR
    assert statuses[fresh.pk] == ScanProcessingJob.STATUS_PROCESSING
    assert statuses[review.pk] == ScanProcessingJob.STATUS_NEEDS_REVIEW
