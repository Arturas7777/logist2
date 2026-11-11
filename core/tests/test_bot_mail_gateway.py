"""scripts/bot_mail_gateway.py: белый список команд и опций для SSH-ботов."""

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bot_mail_gateway.py"
_spec = importlib.util.spec_from_file_location("bot_mail_gateway", _PATH)
gateway = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gateway)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("export_emails_json --after-id 12", ["export_emails_json", "--after-id", "12"]),
        ("export_emails_json --since=2h --limit=50", ["export_emails_json", "--since=2h", "--limit=50"]),
        (
            "export_emails_json --subject 'gate out' --sender maersk.com",
            ["export_emails_json", "--subject", "gate out", "--sender", "maersk.com"],
        ),
        ("export_emails_json --sender=--settings", ["export_emails_json", "--sender=--settings"]),
        ("get_email_attachment 15 0", ["get_email_attachment", "15", "0"]),
        ("get_email_attachment 15 0 --info", ["get_email_attachment", "15", "0", "--info"]),
        ("email_disk_usage --pretty", ["email_disk_usage", "--pretty"]),
        ("export_emails_json --help", ["export_emails_json", "--help"]),
    ],
)
def test_allowed(raw, expected):
    assert gateway.parse_command(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "shell",
        "dbshell",
        "migrate",
        "bash -c id",
        "export_emails_json --settings=evil",
        "export_emails_json --pythonpath /tmp --settings evil",
        "export_emails_json --set evil",
        "export_emails_json --traceback",
        "get_email_attachment 1 0 --output /etc/cron.d/x",
        "get_email_attachment 1 0 --out=/tmp/x",
        "email_disk_usage --info",
        "export_emails_json --subject 'unterminated",
        "export_emails_json " + "x" * 3000,
    ],
)
def test_rejected(raw):
    with pytest.raises(gateway.Rejected):
        gateway.parse_command(raw)


def test_main_rejects_without_exec(monkeypatch, capsys):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "shell")
    monkeypatch.setattr(gateway.os, "execve", lambda *a: pytest.fail("execve не должен вызываться"))
    assert gateway.main() == 2
    assert "запрещена" in capsys.readouterr().err


def test_main_execs_manage_py_with_prod_settings(monkeypatch):
    calls = {}
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "export_emails_json --after-id 5")
    monkeypatch.setattr(gateway.os, "chdir", lambda path: calls.setdefault("cwd", path))
    monkeypatch.setattr(gateway.os, "execve", lambda exe, argv, env: calls.update(exe=exe, argv=argv, env=env))

    gateway.main()

    assert calls["cwd"] == gateway.PROJECT_DIR
    assert calls["argv"][1:] == ["manage.py", "export_emails_json", "--after-id", "5"]
    assert calls["exe"].endswith("/.venv/bin/python")
    assert calls["env"]["DJANGO_SETTINGS_MODULE"] == "logist2.settings.prod"
    assert "SSH_ORIGINAL_COMMAND" not in calls["env"]
