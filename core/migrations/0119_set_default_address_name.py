from django.db import migrations


def set_default_address_name(apps, schema_editor):
    Warehouse = apps.get_model('core', 'Warehouse')
    Warehouse.objects.filter(address__gt='').exclude(address_name__gt='').update(address_name='Основной')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0118_warehouse_sites_and_unload_site'),
    ]

    operations = [
        migrations.RunPython(set_default_address_name, migrations.RunPython.noop),
    ]
