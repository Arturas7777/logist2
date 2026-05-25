"""
Шифрование чувствительных данных (Revolut/Paysera/site.pro credentials).
========================================================================

Архитектура:
- ``ENCRYPTION_KEY`` (env, отдельный от ``SECRET_KEY``) — основной ключ.
- ``ENCRYPTION_KEY_FALLBACKS`` (env, comma-separated) — старые ключи для
  расшифровки данных, ещё не пересохранённых новым ключом (ротация).
- Если ``ENCRYPTION_KEY`` пуст — fallback на ``SECRET_KEY`` (обратная
  совместимость со старыми инсталляциями; в проде такое поведение
  включается warning'ом, см. ``logist2/settings/base.py``).

Под капотом используется ``cryptography.fernet.MultiFernet``: шифруем
первым ключом из списка, расшифровываем — любым подходящим (это и есть
правильная процедура ротации Fernet-ключей).

Ротация ключей:
1. Сгенерировать новый ключ.
2. Положить в ``.env``: ``ENCRYPTION_KEY=<new>`` и
   ``ENCRYPTION_KEY_FALLBACKS=<old>``.
3. Перезапустить процессы.
4. Прогнать ``python manage.py rotate_encryption_key`` — все
   зашифрованные поля будут пересохранены первым ключом.
5. Убрать старый ключ из ``ENCRYPTION_KEY_FALLBACKS``.

Сгенерировать ключ:
    python -c "import secrets; print(secrets.token_urlsafe(48))"
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

logger = logging.getLogger(__name__)


def _derive_fernet_key(material: str) -> bytes:
    """SHA-256 → urlsafe-base64 → 44-byte Fernet key."""
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _collect_key_materials() -> list[str]:
    """Возвращает список материалов ключей: primary + fallbacks (+ SECRET_KEY,
    если primary пуст, для обратной совместимости).

    Порядок важен: первый материал используется для шифрования; остальные
    — только для расшифровки.
    """
    primary = (getattr(settings, "ENCRYPTION_KEY", "") or "").strip()
    fallbacks_raw = (getattr(settings, "ENCRYPTION_KEY_FALLBACKS", "") or "").strip()

    materials: list[str] = []

    if primary:
        materials.append(primary)
    else:
        # Обратная совместимость: данные могли быть зашифрованы SECRET_KEY,
        # пока не был внедрён отдельный ENCRYPTION_KEY. Без primary падать
        # в импорте моделей нельзя — это сломает любые миграции.
        materials.append(settings.SECRET_KEY)

    if fallbacks_raw:
        for fb in fallbacks_raw.split(","):
            fb = fb.strip()
            if fb and fb not in materials:
                materials.append(fb)

    # Если есть primary и primary != SECRET_KEY — добавим SECRET_KEY как
    # последний fallback (старые токены, которые ещё не ротированы).
    if primary and settings.SECRET_KEY not in materials:
        materials.append(settings.SECRET_KEY)

    return materials


@lru_cache(maxsize=1)
def _get_multi_fernet() -> MultiFernet:
    materials = _collect_key_materials()
    fernets = [Fernet(_derive_fernet_key(m)) for m in materials]
    return MultiFernet(fernets)


def reset_cache() -> None:
    """Сбросить кэш ключей (используется в тестах и в rotate-команде)."""
    _get_multi_fernet.cache_clear()


def encrypt_value(plain_text: str) -> str:
    """Зашифровать строку первым (primary) ключом и вернуть ASCII-cipher для БД."""
    if not plain_text:
        return ""
    return _get_multi_fernet().encrypt(plain_text.encode("utf-8")).decode("ascii")


def decrypt_value(cipher_text: str) -> str:
    """Расшифровать строку, пробуя все известные ключи. На ошибке вернуть ''."""
    if not cipher_text:
        return ""
    try:
        return _get_multi_fernet().decrypt(cipher_text.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning(
            "encryption: не удалось расшифровать значение ни одним из %d ключей",
            len(_collect_key_materials()),
        )
        return ""


def rotate_value(cipher_text: str) -> str:
    """Пересохранить значение primary-ключом (MultiFernet.rotate).

    Если ``cipher_text`` зашифрован любым из fallback-ключей — расшифрует
    и зашифрует первым. Если он уже зашифрован primary — fernet вернёт
    ту же структуру, но с новым timestamp/нонсом.
    """
    if not cipher_text:
        return ""
    try:
        return _get_multi_fernet().rotate(cipher_text.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError):
        logger.error("encryption: rotate не смог расшифровать значение — пропускаю")
        raise


def is_using_secret_key_fallback() -> bool:
    """True, если primary-ключа нет и используется SECRET_KEY."""
    primary = (getattr(settings, "ENCRYPTION_KEY", "") or "").strip()
    return not primary
