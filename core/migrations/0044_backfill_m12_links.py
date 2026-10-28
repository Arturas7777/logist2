import re

from django.db import migrations
from django.db.models import F

REFUND_PREFIX = "Возврат по транзакции "
REFUND_NUMBER_RE = re.compile(r"^Возврат по транзакции (TRX-\d{8}-\d+)\.")


def backfill(apps, schema_editor):
    PersonalTransfer = apps.get_model("core", "PersonalTransfer")
    Transaction = apps.get_model("core", "Transaction")

    # Все существующие переводы проведены сразу при создании (view → execute).
    PersonalTransfer.objects.filter(executed_at__isnull=True).update(executed_at=F("created_at"))

    Transaction.objects.filter(type="PAYMENT", method="CASH", description__startswith="Оплата наличными (").update(
        auto_cash=True
    )

    numbers = {}
    for pk, description in Transaction.objects.filter(
        type="REFUND", refund_of__isnull=True, description__startswith=REFUND_PREFIX
    ).values_list("pk", "description"):
        match = REFUND_NUMBER_RE.match(description)
        if match:
            numbers[pk] = match.group(1)
    originals = dict(Transaction.objects.filter(number__in=set(numbers.values())).values_list("number", "pk"))
    for pk, number in numbers.items():
        if number in originals:
            Transaction.objects.filter(pk=pk).update(refund_of_id=originals[number])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_m12_refund_of_auto_cash_executed_at"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
