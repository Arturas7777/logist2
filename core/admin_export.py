"""CSV-экспорт для админки Django (без сторонних зависимостей).

Используется как admin action «Скачать отфильтрованные как CSV».

Пример:
    class NewInvoiceAdmin(CSVExportMixin, admin.ModelAdmin):
        csv_export_fields = [
            ('number', 'Номер'),
            ('document_type', 'Серия'),
            ('date', 'Дата'),
            ('total', 'Сумма'),
            ('status', 'Статус'),
        ]
        actions = [..., 'export_selected_as_csv']
"""
from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO

from django.http import HttpResponse
from django.utils.encoding import smart_str


class CSVExportMixin:
    """Добавляет admin action `export_selected_as_csv`.

    Параметры класса:
      csv_export_fields: list[tuple[str|callable, str]] — пары (attr_path, header).
          attr_path может быть именем поля/проперти или функцией, принимающей obj.
          Вложенные FK через '__' тоже поддерживаются (например 'client__name').
      csv_export_filename_prefix: str — префикс имени файла.
    """

    csv_export_fields: list[tuple] = []
    csv_export_filename_prefix: str = "export"

    def _resolve_value(self, obj, path):
        if callable(path):
            try:
                return path(obj)
            except Exception:
                return ""
        try:
            cur = obj
            for part in path.split("__"):
                if cur is None:
                    return ""
                cur = getattr(cur, part, None)
                if callable(cur) and not hasattr(cur, '__self__'):
                    # property на уровне класса — вызовем
                    try:
                        cur = cur()
                    except Exception:
                        return ""
            return cur if cur is not None else ""
        except Exception:
            return ""

    def export_selected_as_csv(self, request, queryset):
        fields = list(self.csv_export_fields or [])
        if not fields:
            # Безопасный дефолт: все concrete fields модели
            fields = [(f.name, f.verbose_name or f.name)
                      for f in self.model._meta.fields]

        # Пишем в память как utf-8-sig, чтобы Excel корректно открыл кириллицу
        buf = StringIO()
        writer = csv.writer(buf, delimiter=';', quoting=csv.QUOTE_MINIMAL)
        writer.writerow([smart_str(h) for _, h in fields])

        for obj in queryset.iterator(chunk_size=500):
            row = []
            for path, _ in fields:
                val = self._resolve_value(obj, path)
                if hasattr(val, 'isoformat'):
                    val = val.isoformat()
                row.append(smart_str(val))
            writer.writerow(row)

        response = HttpResponse(
            ("\ufeff" + buf.getvalue()).encode('utf-8'),
            content_type='text/csv; charset=utf-8',
        )
        filename = f"{self.csv_export_filename_prefix}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    export_selected_as_csv.short_description = "⬇️ Скачать выбранные как CSV"
