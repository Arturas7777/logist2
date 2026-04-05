from django.db import migrations, models
import django.db.models.deletion


def migrate_warehouse_sites(apps, schema_editor):
    """Copy denormalized address fields to WarehouseSite records."""
    Warehouse = apps.get_model('core', 'Warehouse')
    WarehouseSite = apps.get_model('core', 'WarehouseSite')
    for wh in Warehouse.objects.all():
        if wh.address:
            WarehouseSite.objects.get_or_create(
                warehouse=wh, number=1,
                defaults={'name': wh.address_name, 'address': wh.address},
            )
        if wh.address2:
            WarehouseSite.objects.get_or_create(
                warehouse=wh, number=2,
                defaults={'name': wh.address2_name, 'address': wh.address2},
            )
        if wh.address3:
            WarehouseSite.objects.get_or_create(
                warehouse=wh, number=3,
                defaults={'name': wh.address3_name, 'address': wh.address3},
            )


def migrate_client_emails(apps, schema_editor):
    """Copy denormalized email fields to ClientEmail records."""
    Client = apps.get_model('core', 'Client')
    ClientEmail = apps.get_model('core', 'ClientEmail')
    for client in Client.objects.all():
        is_first = True
        for field in ['email', 'email2', 'email3', 'email4']:
            val = getattr(client, field, None)
            if val and val.strip():
                ClientEmail.objects.get_or_create(
                    client=client, email=val.strip(),
                    defaults={'is_primary': is_first},
                )
                is_first = False


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0131_rename_fields_python_only'),
    ]

    operations = [
        migrations.CreateModel(
            name='WarehouseSite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.PositiveSmallIntegerField(verbose_name='Номер площадки')),
                ('name', models.CharField(blank=True, max_length=100, verbose_name='Название площадки')),
                ('address', models.CharField(blank=True, max_length=300, verbose_name='Адрес')),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sites', to='core.warehouse', verbose_name='Склад')),
            ],
            options={
                'verbose_name': 'Площадка склада',
                'verbose_name_plural': 'Площадки складов',
                'ordering': ['warehouse', 'number'],
            },
        ),
        migrations.AddConstraint(
            model_name='warehousesite',
            constraint=models.UniqueConstraint(fields=('warehouse', 'number'), name='unique_warehouse_site_number'),
        ),
        migrations.CreateModel(
            name='ClientEmail',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, verbose_name='Email')),
                ('is_primary', models.BooleanField(default=False, verbose_name='Основной')),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_emails', to='core.client', verbose_name='Клиент')),
            ],
            options={
                'verbose_name': 'Email клиента',
                'verbose_name_plural': 'Email-адреса клиентов',
            },
        ),
        migrations.AddConstraint(
            model_name='clientemail',
            constraint=models.UniqueConstraint(fields=('client', 'email'), name='unique_client_email'),
        ),
        migrations.RunPython(migrate_warehouse_sites, migrations.RunPython.noop),
        migrations.RunPython(migrate_client_emails, migrations.RunPython.noop),
    ]
