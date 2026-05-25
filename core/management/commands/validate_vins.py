"""Bulk-проверка VIN'ов через VIN-валидатор + NHTSA.

Два режима:
  * --jobs — пройтись по всем ScanProcessingJob, у которых ещё нет
    vin_validations, и дополнить ими extracted_data (БЕЗ изменения других
    полей, БЕЗ ре-apply). Полезно для уже applied jobs, чтобы увидеть
    подозрения задним числом.
  * --cars — пройтись по всем Car и вывести в консоль список тех, у кого
    VIN не проходит валидацию. БД не меняется, только отчёт.

Примеры:
    python manage.py validate_vins --jobs
    python manage.py validate_vins --cars --limit 50
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from core.models import Car
from core.models_scans import ScanProcessingJob
from core.services.vin_validator import (
    cross_check_with_ai_data,
    is_north_american_vin,
    is_vin_checksum_valid,
)


class Command(BaseCommand):
    help = "Bulk VIN validation (check digit + NHTSA)."

    def add_arguments(self, parser):
        parser.add_argument('--jobs', action='store_true',
                            help='Update vin_validations on ScanProcessingJob')
        parser.add_argument('--cars', action='store_true',
                            help='Print suspicious Car VINs (БД не меняется)')
        parser.add_argument('--limit', type=int, default=0,
                            help='Limit number of items processed')
        parser.add_argument('--no-nhtsa', action='store_true',
                            help='Use only check digit (faster, offline)')

    def handle(self, *args, **opts):
        if not opts['jobs'] and not opts['cars']:
            self.stdout.write(self.style.WARNING(
                'Specify --jobs and/or --cars'
            ))
            return
        use_nhtsa = not opts['no_nhtsa']
        limit = opts['limit'] or None

        if opts['jobs']:
            self._validate_jobs(limit=limit, use_nhtsa=use_nhtsa)
        if opts['cars']:
            self._report_cars(limit=limit, use_nhtsa=use_nhtsa)

    def _validate_jobs(self, *, limit, use_nhtsa):
        qs = ScanProcessingJob.objects.all().order_by('id')
        if limit:
            qs = qs[:limit]
        n_total = n_updated = n_warnings = 0
        for job in qs:
            n_total += 1
            data = job.extracted_data or {}
            if not isinstance(data, dict):
                continue
            changed = False
            if job.scan_type == ScanProcessingJob.SCAN_TYPE_TITLE:
                vins = data.get('vins') or []
                results = []
                for vin in vins:
                    if not vin:
                        continue
                    res = cross_check_with_ai_data(
                        vin,
                        ai_make=data.get('make'),
                        ai_model=data.get('model'),
                        ai_year=data.get('year'),
                        use_nhtsa=use_nhtsa,
                    )
                    results.append(res)
                    if res.get('warnings'):
                        n_warnings += 1
                if results != data.get('vin_validations'):
                    data['vin_validations'] = results
                    changed = True
            elif job.scan_type == ScanProcessingJob.SCAN_TYPE_DOCK_RECEIPT:
                for veh in data.get('vehicles') or []:
                    vin = veh.get('vin')
                    if not vin:
                        continue
                    res = cross_check_with_ai_data(
                        vin,
                        ai_make=veh.get('make'),
                        ai_model=veh.get('model'),
                        ai_year=veh.get('year'),
                        use_nhtsa=use_nhtsa,
                    )
                    if veh.get('vin_validation') != res:
                        veh['vin_validation'] = res
                        changed = True
                    if res.get('warnings'):
                        n_warnings += 1

            # Дополнительно: если есть vin_mismatch_review с кандидатами без
            # validation — обогащаем их NHTSA-инфой, чтобы UI показал ✓/❌.
            mismatch = data.get('vin_mismatch_review') or {}
            cands = mismatch.get('candidates') or []
            if cands and any(not c.get('validation') for c in cands):
                from core.models import Car
                for c in cands:
                    if c.get('validation') or not c.get('vin'):
                        continue
                    try:
                        cand_car = Car.objects.filter(pk=c.get('car_id')).only('brand', 'year').first()
                        ai_make = (cand_car.brand or '').split()[0] if cand_car and cand_car.brand else None
                        ai_year = cand_car.year if cand_car else None
                        val = cross_check_with_ai_data(
                            c['vin'],
                            ai_make=ai_make,
                            ai_year=ai_year,
                            use_nhtsa=use_nhtsa,
                        )
                        nhtsa = val.get('nhtsa') or {}
                        c['validation'] = {
                            'checksum_ok': val.get('checksum_ok'),
                            'warnings_count': len(val.get('warnings') or []),
                            'nhtsa_make': nhtsa.get('make'),
                            'nhtsa_model': nhtsa.get('model'),
                            'nhtsa_year': nhtsa.get('year'),
                            'nhtsa_ok': nhtsa.get('ok'),
                        }
                        changed = True
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(
                            f"  candidate {c.get('vin')} validation failed: {e}"
                        ))
            if changed:
                job.extracted_data = data
                job.save(update_fields=['extracted_data'])
                n_updated += 1
                self.stdout.write(f"  job #{job.id}: updated")
        self.stdout.write(self.style.SUCCESS(
            f'\nJobs: total={n_total}, updated={n_updated}, '
            f'with_warnings={n_warnings}'
        ))

    def _report_cars(self, *, limit, use_nhtsa):
        qs = Car.objects.exclude(vin='').order_by('id')
        if limit:
            qs = qs[:limit]
        n_total = n_bad_checksum = n_nhtsa_bad = 0
        suspicious: list[tuple] = []
        for car in qs.iterator():
            n_total += 1
            vin = car.vin
            if len(vin) != 17:
                continue
            cs_ok = is_vin_checksum_valid(vin)
            if is_north_american_vin(vin) and not cs_ok:
                n_bad_checksum += 1
                suspicious.append((car.id, vin, car.brand, car.year, 'NA checksum FAIL'))
            if use_nhtsa:
                res = cross_check_with_ai_data(
                    vin, ai_year=car.year, use_nhtsa=True,
                )
                warns = res.get('warnings') or []
                if warns:
                    n_nhtsa_bad += 1
                    suspicious.append((car.id, vin, car.brand, car.year, '; '.join(warns)[:200]))
        self.stdout.write(self.style.SUCCESS(
            f'Cars: total={n_total}, bad_checksum_NA={n_bad_checksum}, '
            f'nhtsa_warnings={n_nhtsa_bad}'
        ))
        if suspicious:
            self.stdout.write('\nSuspicious VINs:')
            for cid, vin, brand, year, reason in suspicious:
                self.stdout.write(f"  Car #{cid}  {vin}  {brand} {year}  → {reason}")
