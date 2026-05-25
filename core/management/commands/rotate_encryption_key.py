"""
Ротация ENCRYPTION_KEY: пересохраняет все зашифрованные поля primary-ключом.

Использование:
    1. Сгенерировать новый ключ:
         python -c "import secrets; print(secrets.token_urlsafe(48))"
    2. В .env:
         ENCRYPTION_KEY=<новый ключ>
         ENCRYPTION_KEY_FALLBACKS=<старый ключ или SECRET_KEY-значение>
    3. Перезапустить сервисы (или импортнуть свежие settings).
    4. Запустить:
         python manage.py rotate_encryption_key
    5. После успешного прогона убрать ENCRYPTION_KEY_FALLBACKS из .env
       (или оставить SECRET_KEY как safety-net на одну итерацию).

Команда идемпотентна: повторный запуск ничего не ломает — fernet rotate
просто перепишет токены тем же ключом с новым timestamp.

Поля, которые ротируются:
    - BankConnection: _client_id, _refresh_token, _access_token, _jwt_assertion
    - SiteProConnection: _username, _password, _api_key, _private_key,
      _access_token
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from core import encryption

# (model_path, attr_name) — список полей в формате (Django model, db column).
# Используем строки, чтобы не тащить импорты при сборе argparse.
ENCRYPTED_FIELDS: list[tuple[str, list[str]]] = [
    ("core.BankConnection", ["_client_id", "_refresh_token", "_access_token", "_jwt_assertion"]),
    ("core.SiteProConnection", ["_username", "_password", "_api_key", "_private_key", "_access_token"]),
]


class Command(BaseCommand):
    help = "Пересохранить все зашифрованные банковские/accounting-токены primary-ключом."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, что будет ротировано, без записи в БД.",
        )

    def handle(self, *args, **options):
        from django.apps import apps

        dry_run = options["dry_run"]

        # Сбросим кэш на случай, если settings подменили в shell-сессии.
        encryption.reset_cache()

        total_rows = 0
        total_fields = 0
        errors: list[str] = []

        for model_label, attrs in ENCRYPTED_FIELDS:
            app_label, model_name = model_label.split(".")
            try:
                Model = apps.get_model(app_label, model_name)
            except LookupError:
                self.stdout.write(self.style.WARNING(f"  ! Модель {model_label} не найдена — пропускаю."))
                continue

            qs = Model.objects.all()
            count = qs.count()
            self.stdout.write(f"{model_label}: {count} строк, {len(attrs)} зашифрованных полей")

            for obj in qs.iterator():
                changed_attrs: list[str] = []
                for attr in attrs:
                    current = getattr(obj, attr, "") or ""
                    if not current:
                        continue
                    try:
                        new_value = encryption.rotate_value(current)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{model_label}#{obj.pk}.{attr}: {exc}")
                        continue
                    if new_value != current:
                        setattr(obj, attr, new_value)
                        changed_attrs.append(attr)
                        total_fields += 1

                if changed_attrs and not dry_run:
                    with transaction.atomic():
                        # Сохраняем только нужные колонки, чтобы не дергать
                        # auto_now и не плодить сигналы по объекту.
                        Model.objects.filter(pk=obj.pk).update(**{attr: getattr(obj, attr) for attr in changed_attrs})

                if changed_attrs:
                    total_rows += 1
                    self.stdout.write(
                        f"  {'[dry-run] ' if dry_run else ''}{model_label}#{obj.pk}: {', '.join(changed_attrs)}"
                    )

        self.stdout.write("")
        if errors:
            self.stdout.write(self.style.ERROR(f"Ошибок расшифровки: {len(errors)}"))
            for err in errors[:20]:
                self.stdout.write(self.style.ERROR(f"  {err}"))
            if len(errors) > 20:
                self.stdout.write(self.style.ERROR(f"  ... ещё {len(errors) - 20}"))

        verb = "будет ротировано" if dry_run else "ротировано"
        self.stdout.write(self.style.SUCCESS(f"Готово: {verb} {total_fields} полей в {total_rows} строках."))
        if errors:
            # Чтобы CI/sysadmin сразу заметили.
            raise SystemExit(1)
