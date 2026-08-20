"""Отрисовать кабинет клиента и карточку заявки в static/ для визуальной проверки."""

import os
import pathlib
import sys

import django

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "logist2.settings.dev")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.test import Client as TestClient  # noqa: E402

from core.models.website import ClientUser, TransportRequest  # noqa: E402

OUT = pathlib.Path("static")


def save(name: str, html: str) -> None:
    if "<meta charset" not in html:
        html = html.replace("<head>", '<head><meta charset="utf-8">', 1)
    (OUT / name).write_text(html, encoding="utf-8")
    print(name, len(html))


def main() -> None:
    link = ClientUser.objects.select_related("user", "client").first()
    if link is None:
        print("нет ClientUser в локальной базе")
    else:
        client = TestClient()
        client.force_login(link.user)
        from django.urls import reverse

        resp = client.get(reverse("website:transport_requests"), follow=True)
        print("portal", resp.status_code, link.client.name)
        if resp.status_code == 200:
            save("_debug_portal.html", resp.content.decode())

    staff = User.objects.filter(is_staff=True).first()
    tr = TransportRequest.objects.order_by("-id").first()
    if staff and tr:
        admin = TestClient()
        admin.force_login(staff)
        resp = admin.get(f"/admin/requests/{tr.pk}/", follow=True)
        print("card", resp.status_code, tr.number)
        if resp.status_code == 200:
            save("_debug_card.html", resp.content.decode())


main()
