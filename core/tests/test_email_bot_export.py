"""export_emails_json: чтение почты ботами из БД CRM."""

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import Car, Client, Container
from core.models.email import CarEmailLink, ContainerEmail, ContainerEmailLink, TransportRequestEmailLink
from core.models.website import TransportRequest

pytestmark = pytest.mark.django_db


def _email(n, **kwargs):
    defaults = {
        "message_id": f"<m{n}@test>",
        "thread_id": f"t{n}",
        "gmail_id": f"g{n}",
        "from_addr": "ops@maersk.com",
        "to_addrs": "lithuania@caromoto.com",
        "subject": f"Subject {n}",
        "body_text": f"text {n}",
        "body_html": f"<p>html {n}</p>",
        "snippet": f"snippet {n}",
        "received_at": timezone.now(),
    }
    defaults.update(kwargs)
    return ContainerEmail.objects.create(**defaults)


def _export(*args):
    out = StringIO()
    call_command("export_emails_json", *args, stdout=out)
    return json.loads(out.getvalue())


def test_output_fields_and_links(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    cached = tmp_path / "container_emails/2026/10/g1/00_a.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"x")
    owner = Client.objects.create(name="Owner")
    container = Container.objects.create(number="MSKU1234567", status="FLOATING")
    car = Car.objects.create(vin="EXPORTVIN00000001", client=owner, container=container, status="FLOATING", year=2020)
    tr = TransportRequest.objects.create(client=owner, truck_number="T1", driver_name="D", status="SUBMITTED")
    email = _email(
        1,
        cc_addrs="cc@x.lt",
        attachments_json=[
            {
                "filename": "a.pdf",
                "size": 1,
                "content_type": "application/pdf",
                "storage_path": "container_emails/2026/10/g1/00_a.pdf",
            },
            {"filename": "b.pdf", "size": 2, "content_type": "application/pdf", "storage_path": ""},
        ],
    )
    ContainerEmailLink.objects.create(email=email, container=container)
    CarEmailLink.objects.create(email=email, car=car)
    TransportRequestEmailLink.objects.create(email=email, request=tr)

    data = _export()

    assert data["count"] == 1
    item = data["emails"][0]
    assert item["id"] == email.pk
    assert item["gmail_id"] == "g1"
    assert item["thread_id"] == "t1"
    assert item["from"] == "ops@maersk.com"
    assert item["to"] == "lithuania@caromoto.com"
    assert item["cc"] == "cc@x.lt"
    assert item["snippet"] == "snippet 1"
    assert "body_text" not in item
    assert "body_html" not in item
    assert item["containers"] == ["MSKU1234567"]
    assert item["cars"] == ["EXPORTVIN00000001"]
    assert item["transport_requests"] == [tr.number]
    assert [(a["index"], a["filename"], a["cached"]) for a in item["attachments"]] == [
        (0, "a.pdf", True),
        (1, "b.pdf", False),
    ]


def test_include_body_adds_text_only():
    _email(1)
    item = _export("--include-body")["emails"][0]
    assert item["body_text"] == "text 1"
    assert "body_html" not in item


def test_after_id_cursor_pagination_ascending():
    ids = [_email(n).pk for n in range(5)]

    first = _export("--limit", "2")
    assert [e["id"] for e in first["emails"]] == ids[:2]
    assert first["has_more"] is True
    second = _export("--limit", "2", "--after-id", str(first["next_after_id"]))
    assert [e["id"] for e in second["emails"]] == ids[2:4]
    last = _export("--limit", "2", "--after-id", str(second["next_after_id"]))
    assert [e["id"] for e in last["emails"]] == ids[4:]
    assert last["has_more"] is False
    empty = _export("--after-id", str(last["next_after_id"]))
    assert empty["count"] == 0
    assert empty["next_after_id"] == last["next_after_id"]


def test_cursor_sees_backdated_email():
    """Письмо с «задней» датой Date всё равно приходит по курсору id."""
    seen = _email(1).pk
    late = _email(2, received_at=timezone.now() - timedelta(days=30))
    assert [e["id"] for e in _export("--after-id", str(seen))["emails"]] == [late.pk]


def test_filters():
    a = _email(1, from_addr="Ops@Maersk.com", subject="Gate out", direction="INCOMING")
    b = _email(2, from_addr="cma@cma-cgm.com", subject="Booking", direction="OUTGOING")
    old = _email(3, received_at=timezone.now() - timedelta(days=10))

    assert [e["id"] for e in _export("--sender", "maersk")["emails"]] == [a.pk, old.pk]
    assert [e["id"] for e in _export("--subject", "BOOK")["emails"]] == [b.pk]
    assert [e["id"] for e in _export("--direction", "OUTGOING")["emails"]] == [b.pk]
    assert [e["id"] for e in _export("--since", "5d")["emails"]] == [a.pk, b.pk]
    since_iso = (timezone.now() - timedelta(days=5)).isoformat()
    assert [e["id"] for e in _export("--since", since_iso)["emails"]] == [a.pk, b.pk]


def test_filter_by_linked_entities():
    owner = Client.objects.create(name="Owner")
    container = Container.objects.create(number="MSKU7654321", status="FLOATING")
    car = Car.objects.create(vin="EXPORTVIN00000002", client=owner, container=container, status="FLOATING", year=2020)
    tr = TransportRequest.objects.create(client=owner, truck_number="T2", driver_name="D", status="SUBMITTED")
    e1, e2, e3, _other = _email(1), _email(2), _email(3), _email(4)
    ContainerEmailLink.objects.create(email=e1, container=container)
    CarEmailLink.objects.create(email=e2, car=car)
    TransportRequestEmailLink.objects.create(email=e3, request=tr)

    assert [e["id"] for e in _export("--container", "msku7654321")["emails"]] == [e1.pk]
    assert [e["id"] for e in _export("--car-vin", "EXPORTVIN00000002")["emails"]] == [e2.pk]
    assert [e["id"] for e in _export("--transport-request", tr.number)["emails"]] == [e3.pk]


def test_hidden_excluded_by_default():
    normal = _email(1)
    dup = _email(2, hidden_reason=ContainerEmail.HIDDEN_DUPLICATE)
    assert [e["id"] for e in _export()["emails"]] == [normal.pk]
    assert [e["id"] for e in _export("--include-hidden")["emails"]] == [normal.pk, dup.pk]


@pytest.mark.parametrize("bad", ["yesterday", "2h30", "-1d", ""])
def test_invalid_since_is_an_error(bad):
    _email(1)
    with pytest.raises(CommandError):
        _export("--since", bad)


@pytest.mark.parametrize("args", [["--limit", "0"], ["--limit", "501"], ["--after-id", "-1"]])
def test_invalid_limits(args):
    with pytest.raises(CommandError):
        _export(*args)


def test_export_has_no_n_plus_one(django_assert_max_num_queries):
    for n in range(3):
        _email(n)
    with django_assert_max_num_queries(4):
        _export()
