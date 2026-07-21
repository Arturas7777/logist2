"""Тесты реквизитов контрагентов: синк общей почты в «Контакты» + счета."""

import pytest
from django.contrib.contenttypes.models import ContentType

from core.models import (
    Carrier,
    Contact,
    ContactEmail,
    CounterpartyBankAccount,
    Line,
    Warehouse,
)


@pytest.mark.django_db
class TestGeneralEmailSync:
    def test_email_creates_contact(self):
        wh = Warehouse.objects.create(name="Test WH", general_email="info@wh.example")

        ct = ContentType.objects.get_for_model(Warehouse)
        contact = Contact.objects.get(content_type=ct, object_id=wh.pk)
        assert contact.position == "Общая почта"
        assert contact.name == "Test WH"
        assert ContactEmail.objects.filter(contact=contact, email="info@wh.example").exists()

    def test_resave_does_not_duplicate(self):
        line = Line.objects.create(name="Test Line", general_email="ops@line.example")
        line.save()
        line.save()

        ct = ContentType.objects.get_for_model(Line)
        assert Contact.objects.filter(content_type=ct, object_id=line.pk).count() == 1
        assert ContactEmail.objects.filter(email="ops@line.example").count() == 1

    def test_empty_email_creates_nothing(self):
        wh = Warehouse.objects.create(name="No Email WH")
        ct = ContentType.objects.get_for_model(Warehouse)
        assert not Contact.objects.filter(content_type=ct, object_id=wh.pk).exists()

    def test_carrier_uses_own_email_field(self):
        carrier = Carrier.objects.create(name="Test Carrier", email="dispatch@carrier.example")
        ct = ContentType.objects.get_for_model(Carrier)
        contact = Contact.objects.get(content_type=ct, object_id=carrier.pk)
        assert ContactEmail.objects.filter(contact=contact, email="dispatch@carrier.example").exists()

    def test_second_email_added_to_same_contact(self):
        wh = Warehouse.objects.create(name="WH2", general_email="a@wh.example")
        wh.general_email = "b@wh.example"
        wh.save()

        ct = ContentType.objects.get_for_model(Warehouse)
        contact = Contact.objects.get(content_type=ct, object_id=wh.pk)
        emails = set(contact.emails.values_list("email", flat=True))
        assert emails == {"a@wh.example", "b@wh.example"}


@pytest.mark.django_db
class TestPartnerAdminPagesRender:
    @pytest.mark.parametrize("model_name", ["warehouse", "client", "company", "line", "carrier"])
    def test_add_page_renders_with_new_fields(self, admin_client, model_name):
        resp = admin_client.get(f"/admin/core/{model_name}/add/")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "imones_kodas" in html
        assert "vat_code" in html
        # Коллапс-блок счетов контрагента присутствует на карточке
        assert "Счета контрагента" in html


@pytest.mark.django_db
class TestCounterpartyBankAccount:
    def test_create_and_str(self):
        wh = Warehouse.objects.create(name="WH Bank")
        acc = CounterpartyBankAccount.objects.create(
            counterparty=wh,
            bank_name="Swedbank",
            iban="LT127300010012345678",
            swift="HABALT22",
        )
        assert acc.counterparty == wh
        assert acc.currency == "EUR"
        assert "Swedbank" in str(acc)
        assert "LT127300010012345678" in str(acc)

    def test_requisites_fields_optional(self):
        wh = Warehouse.objects.create(name="WH Optional")
        assert wh.imones_kodas == ""
        assert wh.vat_code == ""
        assert wh.eori_code == ""
        assert wh.registration_country == ""
        assert wh.physical_address == ""
        assert wh.website == ""
        assert wh.phone == ""
        assert wh.general_email == ""
