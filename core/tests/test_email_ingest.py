"""Тесты email_ingest: обработка 404 от Gmail (письмо удалено из ящика).

Регрессия 2026-07-21: удалённые из ящика письма возвращали 404 на каждом
прогоне, ingest_errors не давал сдвинуть last_history_id, и те же письма
ретраились вечно (~20K ERROR-событий в Sentry за день).
"""

import httplib2
import pytest
from googleapiclient.errors import HttpError

from core.services.email_ingest import SyncReport, _ingest_one, _is_gmail_not_found


def _http_error(status: int) -> HttpError:
    return HttpError(resp=httplib2.Response({"status": str(status)}), content=b"error")


class TestIsGmailNotFound:
    def test_404_is_not_found(self):
        assert _is_gmail_not_found(_http_error(404)) is True

    def test_other_http_statuses_are_not(self):
        assert _is_gmail_not_found(_http_error(500)) is False
        assert _is_gmail_not_found(_http_error(403)) is False

    def test_non_http_error_is_not(self):
        assert _is_gmail_not_found(TimeoutError("read timed out")) is False


@pytest.mark.django_db
class TestIngestOneNotFound:
    def test_404_skips_without_ingest_error(self):
        """404 → not_found_skipped, ingest_errors=0 (курсор истории сдвинется)."""

        class FakeClient:
            def get_message(self, gmail_id):
                raise _http_error(404)

        report = SyncReport()
        _ingest_one(FakeClient(), "deadbeef", {}, report)

        assert report.not_found_skipped == 1
        assert report.ingest_errors == 0
        assert report.errors == []

    def test_transient_error_still_counts(self):
        """Не-404 ошибки по-прежнему блокируют сдвиг курсора (retry)."""

        class FakeClient:
            def get_message(self, gmail_id):
                raise _http_error(500)

        report = SyncReport()
        _ingest_one(FakeClient(), "deadbeef", {}, report)

        assert report.not_found_skipped == 0
        assert report.ingest_errors == 1
        assert len(report.errors) == 1
