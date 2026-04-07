"""Repad AV numbers from 8 digits to 6 digits to match PARDP format."""

from django.db import migrations


def repad_av(apps, schema_editor):
    NewInvoice = apps.get_model('core', 'NewInvoice')
    for inv in NewInvoice.objects.filter(number__startswith='AV-').order_by('id'):
        try:
            num = int(inv.number.split('-', 1)[1])
            inv.number = f'AV-{num:06d}'
            inv.save(update_fields=['number'])
        except (ValueError, IndexError):
            pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0135_rename_inv_to_av'),
    ]

    operations = [
        migrations.RunPython(repad_av, migrations.RunPython.noop),
    ]
