"""Rename underscore-prefixed keys in ScanProcessingJob.extracted_data.

Django templates запрещают обращаться к атрибутам/ключам, начинающимся с
"_" (security). Поэтому переименовываем:
  * ``_vin_mismatch``    → ``vin_mismatch_review``
  * ``_skip_vin_check``  → ``skip_vin_check``
"""
from __future__ import annotations

from django.db import migrations


def _rename_keys(apps, schema_editor):
    ScanProcessingJob = apps.get_model('core', 'ScanProcessingJob')
    for job in ScanProcessingJob.objects.all().iterator():
        data = job.extracted_data or {}
        if not isinstance(data, dict):
            continue
        changed = False
        if '_vin_mismatch' in data:
            data['vin_mismatch_review'] = data.pop('_vin_mismatch')
            changed = True
        if '_skip_vin_check' in data:
            data['skip_vin_check'] = data.pop('_skip_vin_check')
            changed = True
        if changed:
            job.extracted_data = data
            job.save(update_fields=['extracted_data'])


def _revert_keys(apps, schema_editor):
    ScanProcessingJob = apps.get_model('core', 'ScanProcessingJob')
    for job in ScanProcessingJob.objects.all().iterator():
        data = job.extracted_data or {}
        if not isinstance(data, dict):
            continue
        changed = False
        if 'vin_mismatch_review' in data:
            data['_vin_mismatch'] = data.pop('vin_mismatch_review')
            changed = True
        if 'skip_vin_check' in data:
            data['_skip_vin_check'] = data.pop('skip_vin_check')
            changed = True
        if changed:
            job.extracted_data = data
            job.save(update_fields=['extracted_data'])


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0166_add_scan_processing'),
    ]

    operations = [
        migrations.RunPython(_rename_keys, _revert_keys),
    ]
