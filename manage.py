#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main():
    """Run administrative tasks."""
    # Локальный дефолт — dev-профиль (DEBUG=True, debug-toolbar и т.п.).
    # На сервере systemd unit'ы (gunicorn/daphne/celery) явно выставляют
    # DJANGO_SETTINGS_MODULE=logist2.settings.prod через Environment=,
    # а scripts/deploy.ps1 — для ручных migrate/collectstatic. CI задаёт
    # logist2.settings.test через .github/workflows/ci.yml.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "logist2.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
