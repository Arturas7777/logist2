from django.db import migrations, models
import datetime
from django.utils import timezone


def backfill_unloaded_status_at(apps, schema_editor):
    Container = apps.get_model('core', 'Container')
    tz = timezone.get_current_timezone()
    for container in Container.objects.filter(status='UNLOADED', unloaded_status_at__isnull=True, unload_date__isnull=False):
        naive_dt = datetime.datetime.combine(container.unload_date, datetime.time.min)
        container.unloaded_status_at = timezone.make_aware(naive_dt, tz)
        container.save(update_fields=['unloaded_status_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0096_add_default_markup_to_services'),
    ]

    operations = [
        migrations.AddField(
            model_name='container',
            name='unloaded_status_at',
            field=models.DateTimeField(blank=True, help_text="РљРѕРіРґР° РєРѕРЅС‚РµР№РЅРµСЂ РїРѕР»СѓС‡РёР» СЃС‚Р°С‚СѓСЃ 'Р Р°Р·РіСЂСѓР¶РµРЅ' (РґР»СЏ Р·Р°РґРµСЂР¶РєРё СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёРё С„РѕС‚Рѕ)", null=True, verbose_name="РЎС‚Р°С‚СѓСЃ 'Р Р°Р·РіСЂСѓР¶РµРЅ' СЃ"),
        ),
        migrations.RunPython(backfill_unloaded_status_at, migrations.RunPython.noop),
    ]
