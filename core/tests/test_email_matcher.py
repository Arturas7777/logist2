"""Тесты сопоставления писем с контейнерами + парсера Gmail payload."""

from __future__ import annotations

import base64
from datetime import datetime, timezone as dt_timezone

from django.test import TestCase

from core.models import Container, Line
from core.models_email import ContainerEmail
from core.services.email_matcher import (
    build_booking_index,
    match_email_to_containers,
)
from core.services.gmail_client import ParsedMessage, parse_gmail_message


def _make_parsed(
    *,
    subject: str = '',
    body_text: str = '',
    thread_id: str = '',
    message_id: str = '',
    in_reply_to: str = '',
    gmail_id: str = 'gid1',
    labels: list[str] | None = None,
) -> ParsedMessage:
    return ParsedMessage(
        gmail_id=gmail_id,
        thread_id=thread_id,
        history_id=None,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references='',
        subject=subject,
        from_addr='sender@example.com',
        to_addrs='to@example.com',
        cc_addrs='',
        received_at=datetime.now(tz=dt_timezone.utc),
        snippet='',
        body_text=body_text,
        body_html='',
        labels=list(labels or []),
        attachments=[],
    )


class EmailMatcherTest(TestCase):
    """Покрывает все пять веток матчера: тред / in-reply-to / контейнер / букинг / unmatched."""

    def setUp(self):
        self.line = Line.objects.create(name='MSC')
        self.container_a = Container.objects.create(
            number='MSKU1234567',
            booking_number='ABC12345',
            line=self.line,
        )
        self.container_b = Container.objects.create(
            number='CMAU7654321',
            booking_number='XYZ99999',
            line=self.line,
        )

    # ------------------------------------------------------------------
    # Контейнер по номеру
    # ------------------------------------------------------------------

    def test_match_by_container_number_in_subject(self):
        msg = _make_parsed(subject='Re: MSKU1234567 документы')
        res = match_email_to_containers(msg)
        self.assertEqual(res.primary_container_id, self.container_a.id)
        self.assertEqual(res.primary_matched_by, ContainerEmail.MATCHED_BY_CONTAINER_NUMBER)
        self.assertEqual(
            sorted(h.container_id for h in res.hits),
            [self.container_a.id],
        )

    def test_match_by_container_number_in_body(self):
        msg = _make_parsed(
            subject='ETA update',
            body_text='Dear customer, container CMAU7654321 is delayed by 2 days.',
        )
        res = match_email_to_containers(msg)
        self.assertEqual(res.primary_container_id, self.container_b.id)
        self.assertEqual(res.primary_matched_by, ContainerEmail.MATCHED_BY_CONTAINER_NUMBER)

    def test_match_multiple_container_numbers(self):
        """Если в теме упомянуты оба контейнера — должны вернуться оба."""
        msg = _make_parsed(
            subject='Containers MSKU1234567 , CMAU7654321 status update',
        )
        res = match_email_to_containers(msg)
        ids = sorted(h.container_id for h in res.hits)
        self.assertEqual(ids, sorted([self.container_a.id, self.container_b.id]))
        for hit in res.hits:
            self.assertEqual(
                hit.matched_by,
                ContainerEmail.MATCHED_BY_CONTAINER_NUMBER,
            )

    def test_no_match_when_container_number_not_in_db(self):
        msg = _make_parsed(subject='TCLU9999999 заметка')
        res = match_email_to_containers(msg)
        self.assertIsNone(res.primary_container_id)
        self.assertEqual(res.primary_matched_by, ContainerEmail.MATCHED_BY_UNMATCHED)
        self.assertFalse(res.is_matched)

    # ------------------------------------------------------------------
    # Букинг
    # ------------------------------------------------------------------

    def test_match_by_booking_number(self):
        msg = _make_parsed(
            subject='Booking ABC12345 confirmed',
            body_text='Your booking ABC12345 is ready',
        )
        res = match_email_to_containers(msg)
        self.assertEqual(res.primary_container_id, self.container_a.id)
        self.assertEqual(res.primary_matched_by, ContainerEmail.MATCHED_BY_BOOKING_NUMBER)

    def test_booking_does_not_match_substring(self):
        """ABC12345 не должен срабатывать на ABC123456."""
        msg = _make_parsed(subject='About ABC123456 something')
        res = match_email_to_containers(msg)
        self.assertIsNone(res.primary_container_id)

    def test_booking_is_case_insensitive(self):
        msg = _make_parsed(body_text='Referenced: abc12345')
        res = match_email_to_containers(msg)
        self.assertEqual(res.primary_container_id, self.container_a.id)

    # ------------------------------------------------------------------
    # Тред / In-Reply-To
    # ------------------------------------------------------------------

    def test_match_by_thread_id(self):
        parent = ContainerEmail.objects.create(
            message_id='<first@example.com>',
            thread_id='thread-123',
            direction=ContainerEmail.DIRECTION_INCOMING,
            from_addr='a@b.c',
            subject='Original',
            received_at=datetime.now(tz=dt_timezone.utc),
            matched_by=ContainerEmail.MATCHED_BY_CONTAINER_NUMBER,
        )
        parent.containers.add(self.container_a)
        msg = _make_parsed(
            thread_id='thread-123',
            subject='Re: no container number here',
        )
        res = match_email_to_containers(msg)
        self.assertEqual(res.primary_container_id, self.container_a.id)
        self.assertEqual(res.primary_matched_by, ContainerEmail.MATCHED_BY_THREAD)

    def test_match_by_in_reply_to(self):
        parent = ContainerEmail.objects.create(
            message_id='<parent@example.com>',
            thread_id='different-thread',
            direction=ContainerEmail.DIRECTION_INCOMING,
            from_addr='a@b.c',
            subject='Parent',
            received_at=datetime.now(tz=dt_timezone.utc),
            matched_by=ContainerEmail.MATCHED_BY_CONTAINER_NUMBER,
        )
        parent.containers.add(self.container_b)
        msg = _make_parsed(
            thread_id='fresh-thread',
            in_reply_to='<parent@example.com>',
            subject='Re: untracked',
        )
        res = match_email_to_containers(msg)
        self.assertEqual(res.primary_container_id, self.container_b.id)
        self.assertEqual(res.primary_matched_by, ContainerEmail.MATCHED_BY_THREAD)

    # ------------------------------------------------------------------
    # Fallback: UNMATCHED
    # ------------------------------------------------------------------

    def test_unmatched_when_nothing_found(self):
        msg = _make_parsed(subject='General question', body_text='Hi, how are you?')
        res = match_email_to_containers(msg)
        self.assertIsNone(res.primary_container_id)
        self.assertEqual(res.primary_matched_by, ContainerEmail.MATCHED_BY_UNMATCHED)

    # ------------------------------------------------------------------
    # Booking index
    # ------------------------------------------------------------------

    def test_build_booking_index_skips_empty(self):
        Container.objects.create(number='TEST1111111', booking_number='', line=self.line)
        index = build_booking_index()
        self.assertIn('abc12345', index)
        self.assertIn('xyz99999', index)
        self.assertEqual(index['abc12345'], self.container_a.id)


def _b64url(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode('utf-8')).decode('ascii').rstrip('=')


class GmailPayloadParserTest(TestCase):
    """Парсер payload: multipart, кириллица, аттачи."""

    def test_simple_plaintext_message(self):
        raw = {
            'id': 'abc123',
            'threadId': 'th-1',
            'historyId': '42',
            'snippet': 'Hello world',
            'labelIds': ['INBOX'],
            'internalDate': '1700000000000',
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'Subject', 'value': 'Test subject'},
                    {'name': 'From', 'value': 'Alice <alice@example.com>'},
                    {'name': 'To', 'value': 'bob@example.com'},
                    {'name': 'Message-ID', 'value': '<m1@example.com>'},
                    {'name': 'Date', 'value': 'Mon, 15 Apr 2024 10:00:00 +0000'},
                ],
                'body': {'data': _b64url('Hello, plaintext!')},
            },
        }
        parsed = parse_gmail_message(raw)
        self.assertEqual(parsed.gmail_id, 'abc123')
        self.assertEqual(parsed.thread_id, 'th-1')
        self.assertEqual(parsed.history_id, 42)
        self.assertEqual(parsed.subject, 'Test subject')
        self.assertEqual(parsed.from_addr, 'Alice <alice@example.com>')
        self.assertEqual(parsed.message_id, '<m1@example.com>')
        self.assertIn('Hello, plaintext!', parsed.body_text)
        self.assertFalse(parsed.is_outgoing)

    def test_multipart_html_and_text(self):
        raw = {
            'id': 'gid',
            'threadId': 'th',
            'payload': {
                'mimeType': 'multipart/alternative',
                'headers': [{'name': 'Subject', 'value': 'Multi'}],
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'headers': [{'name': 'Content-Type', 'value': 'text/plain; charset=UTF-8'}],
                        'body': {'data': _b64url('Привет, мир!')},
                    },
                    {
                        'mimeType': 'text/html',
                        'headers': [{'name': 'Content-Type', 'value': 'text/html; charset=UTF-8'}],
                        'body': {'data': _b64url('<p>Hello <b>world</b></p>')},
                    },
                ],
            },
        }
        parsed = parse_gmail_message(raw)
        self.assertIn('Привет, мир!', parsed.body_text)
        self.assertIn('<b>world</b>', parsed.body_html)

    def test_attachment_extracted(self):
        raw = {
            'id': 'gid2',
            'threadId': 'th2',
            'payload': {
                'mimeType': 'multipart/mixed',
                'headers': [{'name': 'Subject', 'value': 'With attach'}],
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _b64url('Body')},
                    },
                    {
                        'mimeType': 'application/pdf',
                        'filename': 'invoice.pdf',
                        'body': {'attachmentId': 'att-xyz', 'size': 12345},
                    },
                ],
            },
        }
        parsed = parse_gmail_message(raw)
        self.assertEqual(len(parsed.attachments), 1)
        self.assertEqual(parsed.attachments[0].filename, 'invoice.pdf')
        self.assertEqual(parsed.attachments[0].size, 12345)
        self.assertEqual(parsed.attachments[0].attachment_id, 'att-xyz')

    def test_mime_encoded_subject_decoded(self):
        raw = {
            'id': 'gid3',
            'threadId': 'th3',
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'Subject', 'value': '=?UTF-8?B?0J/RgNC40LLQtdGCINC80LjRgA==?='},
                ],
                'body': {'data': _b64url('ok')},
            },
        }
        parsed = parse_gmail_message(raw)
        self.assertEqual(parsed.subject, 'Привет мир')

    def test_outgoing_detected_by_sent_label(self):
        raw = {
            'id': 'gid4',
            'threadId': 'th4',
            'labelIds': ['SENT'],
            'payload': {
                'mimeType': 'text/plain',
                'headers': [],
                'body': {'data': _b64url('bye')},
            },
        }
        parsed = parse_gmail_message(raw)
        self.assertTrue(parsed.is_outgoing)
