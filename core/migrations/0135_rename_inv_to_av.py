"""
Data migration: rename INV-* invoices to AV-* and set document_type correctly.

- PARDP-* → document_type = 'INVOICE'
- INV-* → rename to AV-NNNNNNNN, document_type = 'PROFORMA'
- Everything else → document_type = 'PROFORMA'
"""

from django.db import migrations


def rename_inv_to_av(apps, schema_editor):
    NewInvoice = apps.get_model('core', 'NewInvoice')

    NewInvoice.objects.filter(number__startswith='PARDP-').update(document_type='INVOICE')

    inv_invoices = NewInvoice.objects.filter(number__startswith='INV-').order_by('id')
    last_av = NewInvoice.objects.filter(number__startswith='AV-').order_by('-number').first()
    if last_av:
        try:
            counter = int(last_av.number.split('-', 1)[1]) + 1
        except (ValueError, IndexError):
            counter = 1
    else:
        counter = 1

    for inv in inv_invoices:
        new_number = f'AV-{counter:08d}'
        inv.number = new_number
        inv.document_type = 'PROFORMA'
        inv.save(update_fields=['number', 'document_type'])
        counter += 1

    NewInvoice.objects.filter(document_type='').update(document_type='PROFORMA')


def reverse_rename(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0134_add_document_type_to_invoice'),
    ]

    operations = [
        migrations.RunPython(rename_inv_to_av, reverse_rename),
    ]
