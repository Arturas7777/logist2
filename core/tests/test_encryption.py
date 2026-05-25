"""
Тесты модуля шифрования (core/encryption.py) — Fernet + ротация ключей.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from core import encryption


@pytest.fixture(autouse=True)
def _reset_encryption_cache():
    """LRU-кэш ключей живёт между тестами — сбрасываем перед каждым."""
    encryption.reset_cache()
    yield
    encryption.reset_cache()


# --- helpers ----------------------------------------------------------------

PRIMARY_KEY = "primary-key-with-enough-entropy-AAAAAAAAAAAAAAAAAA"
OLD_KEY = "old-key-with-enough-entropy-BBBBBBBBBBBBBBBBBBBBBBBBBB"
ANOTHER_OLD_KEY = "another-old-key-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


# --- базовое шифрование -----------------------------------------------------


class TestBasicEncryption:
    @override_settings(ENCRYPTION_KEY=PRIMARY_KEY, ENCRYPTION_KEY_FALLBACKS="")
    def test_roundtrip_simple(self):
        encryption.reset_cache()
        cipher = encryption.encrypt_value("hello")
        assert cipher != "hello"
        assert encryption.decrypt_value(cipher) == "hello"

    @override_settings(ENCRYPTION_KEY=PRIMARY_KEY, ENCRYPTION_KEY_FALLBACKS="")
    def test_roundtrip_unicode(self):
        encryption.reset_cache()
        secret = "Caromoto Lithuania — токен Revolut: αβγ 中文 🔑"
        assert encryption.decrypt_value(encryption.encrypt_value(secret)) == secret

    @override_settings(ENCRYPTION_KEY=PRIMARY_KEY)
    def test_empty_string_passthrough(self):
        encryption.reset_cache()
        assert encryption.encrypt_value("") == ""
        assert encryption.decrypt_value("") == ""

    @override_settings(ENCRYPTION_KEY=PRIMARY_KEY)
    def test_decrypt_garbage_returns_empty(self):
        encryption.reset_cache()
        assert encryption.decrypt_value("not-a-valid-token") == ""


# --- fallback на SECRET_KEY -------------------------------------------------


class TestSecretKeyFallback:
    @override_settings(ENCRYPTION_KEY="", SECRET_KEY="unit-test-secret-key-xxxxxxxxxxxxxxxxxxxxx")
    def test_uses_secret_key_when_no_primary(self):
        encryption.reset_cache()
        assert encryption.is_using_secret_key_fallback() is True
        cipher = encryption.encrypt_value("payload")
        assert encryption.decrypt_value(cipher) == "payload"

    @override_settings(
        ENCRYPTION_KEY=PRIMARY_KEY,
        SECRET_KEY="unit-test-secret-key-xxxxxxxxxxxxxxxxxxxxx",
    )
    def test_primary_key_disables_fallback_flag(self):
        encryption.reset_cache()
        assert encryption.is_using_secret_key_fallback() is False


# --- ротация ----------------------------------------------------------------


class TestKeyRotation:
    @override_settings(
        ENCRYPTION_KEY=PRIMARY_KEY,
        ENCRYPTION_KEY_FALLBACKS=OLD_KEY,
        SECRET_KEY="unit-test-secret-key-xxxxxxxxxxxxxxxxxxxxx",
    )
    def test_can_decrypt_value_encrypted_with_old_key(self):
        # 1. Зашифровать "старым" ключом, как будто primary = OLD_KEY.
        with override_settings(ENCRYPTION_KEY=OLD_KEY, ENCRYPTION_KEY_FALLBACKS=""):
            encryption.reset_cache()
            cipher_old = encryption.encrypt_value("legacy-token")

        # 2. Переключаемся на новый primary + старый в fallbacks.
        encryption.reset_cache()
        assert encryption.decrypt_value(cipher_old) == "legacy-token"

    @override_settings(
        ENCRYPTION_KEY=PRIMARY_KEY,
        ENCRYPTION_KEY_FALLBACKS=OLD_KEY,
    )
    def test_rotate_value_changes_underlying_key(self):
        # Шифруем "старым" → потом ротируем → новый cipher должен
        # расшифровываться даже без fallback на старый.
        with override_settings(ENCRYPTION_KEY=OLD_KEY, ENCRYPTION_KEY_FALLBACKS=""):
            encryption.reset_cache()
            cipher_old = encryption.encrypt_value("token-X")

        encryption.reset_cache()
        rotated = encryption.rotate_value(cipher_old)
        assert rotated != cipher_old
        assert encryption.decrypt_value(rotated) == "token-X"

        # После полного удаления fallback'а новый cipher всё ещё читается.
        with override_settings(ENCRYPTION_KEY=PRIMARY_KEY, ENCRYPTION_KEY_FALLBACKS=""):
            encryption.reset_cache()
            assert encryption.decrypt_value(rotated) == "token-X"

    @override_settings(
        ENCRYPTION_KEY=PRIMARY_KEY,
        ENCRYPTION_KEY_FALLBACKS=f"{OLD_KEY},{ANOTHER_OLD_KEY}",
    )
    def test_multiple_fallbacks(self):
        # 2 разных ключа в истории.
        with override_settings(ENCRYPTION_KEY=OLD_KEY, ENCRYPTION_KEY_FALLBACKS=""):
            encryption.reset_cache()
            cipher_a = encryption.encrypt_value("A")
        with override_settings(ENCRYPTION_KEY=ANOTHER_OLD_KEY, ENCRYPTION_KEY_FALLBACKS=""):
            encryption.reset_cache()
            cipher_b = encryption.encrypt_value("B")

        encryption.reset_cache()
        assert encryption.decrypt_value(cipher_a) == "A"
        assert encryption.decrypt_value(cipher_b) == "B"

    @override_settings(
        ENCRYPTION_KEY=PRIMARY_KEY,
        ENCRYPTION_KEY_FALLBACKS="",
    )
    def test_cannot_decrypt_after_dropping_old_key_without_rotation(self):
        with override_settings(ENCRYPTION_KEY=OLD_KEY, ENCRYPTION_KEY_FALLBACKS=""):
            encryption.reset_cache()
            cipher_old = encryption.encrypt_value("lost-token")

        encryption.reset_cache()
        # Старый ключ не в fallbacks → расшифровка падает в '' (warning).
        assert encryption.decrypt_value(cipher_old) == ""


# --- интеграция с BankConnection -------------------------------------------


@pytest.mark.django_db
class TestBankConnectionEncryption:
    @override_settings(ENCRYPTION_KEY=PRIMARY_KEY, ENCRYPTION_KEY_FALLBACKS="")
    def test_bank_connection_roundtrip(self):
        encryption.reset_cache()
        from core.models import Company
        from core.models_banking import BankConnection

        company = Company.objects.create(name="Caromoto Lithuania, MB")
        conn = BankConnection.objects.create(
            bank_type="REVOLUT",
            company=company,
            name="Revolut Test",
        )
        conn.client_id = "cid-secret"
        conn.refresh_token = "rt-secret"
        conn.access_token = "at-secret"
        conn.jwt_assertion = "jwt.header.signature"
        conn.save()

        # Перечитываем из БД, чтобы убедиться что property работает после save.
        conn.refresh_from_db()
        assert conn.client_id == "cid-secret"
        assert conn.refresh_token == "rt-secret"
        assert conn.access_token == "at-secret"
        assert conn.jwt_assertion == "jwt.header.signature"

        # Зашифрованные поля в БД — не plain text.
        assert conn._client_id != "cid-secret"
        assert "secret" not in conn._refresh_token
