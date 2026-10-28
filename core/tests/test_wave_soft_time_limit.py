"""L6: SoftTimeLimitExceeded не глотается широким except.

Раньше лимит времени Celery внутри разбора письма считался «неудачей
письма»: ингест засчитывал здоровому письму попытку в счёт карантина и
продолжал работу до жёсткого kill, а агент помечал неразобранное письмо
проанализированным.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from core.models_email import ContainerEmail, GmailIngestFailure
from core.services.email_ingest import SyncReport, _ingest_one

pytestmark = pytest.mark.django_db


class _SlowClient:
    def get_message(self, gmail_id):
        raise SoftTimeLimitExceeded()


def test_ingest_get_message_time_limit_propagates_without_failure_record():
    report = SyncReport()
    with pytest.raises(SoftTimeLimitExceeded):
        _ingest_one(_SlowClient(), "slow", {}, report)

    assert report.ingest_errors == 0
    assert not GmailIngestFailure.objects.filter(gmail_id="slow").exists()


def test_ingest_save_time_limit_propagates_without_failure_record():
    from core.tests.test_email_ingest import _FakeMessageClient, _message

    client = _FakeMessageClient(_message("slow-save"))
    report = SyncReport()
    with (
        patch.object(ContainerEmail.objects, "get_or_create", side_effect=SoftTimeLimitExceeded()),
        pytest.raises(SoftTimeLimitExceeded),
    ):
        _ingest_one(client, "slow-save", {}, report)

    assert report.ingest_errors == 0
    assert not GmailIngestFailure.objects.filter(gmail_id="slow-save").exists()


def test_client_email_masked_in_logs():
    from core.services.email_service import mask_email

    assert mask_email("jonas@client.lt") == "j***@client.lt"
    assert mask_email("broken") == "***"
    assert mask_email("") == "***"


def test_analyzer_time_limit_leaves_email_unanalyzed():
    from core.models import AgentRun
    from core.services.agent import email_analyzer

    email = ContainerEmail.objects.create(
        message_id="<slow-analyze@test>",
        thread_id="t-slow",
        direction=ContainerEmail.DIRECTION_INCOMING,
        from_addr="line@example.com",
        subject="Arrival notice",
        received_at=timezone.now(),
    )
    with (
        patch("core.services.agent.llm_client.AgentLLMClient") as llm_cls,
        patch("core.services.agent.context_builder.build_system_context", return_value=""),
    ):
        llm_cls.return_value.complete_json.side_effect = SoftTimeLimitExceeded()
        with pytest.raises(SoftTimeLimitExceeded):
            email_analyzer.analyze_email(email)

    email.refresh_from_db()
    assert email.agent_analyzed_at is None
    assert AgentRun.objects.filter(status=AgentRun.STATUS_ERROR).exists()
