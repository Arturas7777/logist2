"""HMAC-подписанные ссылки для публичной отдачи фотографий.

Используется встроенный `django.core.signing.TimestampSigner` (HMAC на
SECRET_KEY) — без внешних зависимостей. Подписанная ссылка живёт
`PHOTO_URL_TTL` секунд (по умолчанию 1 час) и привязана к конкретному
объекту:

- `make_photo_token(kind, photo_id, variant)` — токен на один файл
  (`kind` ∈ {`container`, `car`}, `variant` ∈ {`full`, `thumb`}).
- `make_container_token(container_number)` — токен на контейнер,
  необходим для `download_photos_archive`: по нему сервер
  убеждается, что фронт **видел** список фото именно этого контейнера,
  а не перебирает `photo_ids` сторонним скриптом.

Источник угроз: см. `docs/PUBLIC_ENDPOINTS.md`.
"""

from __future__ import annotations

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

# Дефолтное время жизни одного подписанного URL. Должно быть достаточно,
# чтобы пользователь успел открыть галерею и скачать архив, но не давать
# «вечную» ссылку, которую можно сохранить и использовать год спустя.
DEFAULT_TTL_SECONDS = 3600  # 1 час

PHOTO_KINDS = ("container", "car")
PHOTO_VARIANTS = ("full", "thumb")

_PHOTO_SALT = "logist2.photos.v1"
_CONTAINER_SALT = "logist2.container_archive.v1"


def _ttl() -> int:
    """TTL подписей. Можно переопределить через `settings.PHOTO_URL_TTL`."""
    return int(getattr(settings, "PHOTO_URL_TTL", DEFAULT_TTL_SECONDS))


def make_photo_token(kind: str, photo_id: int, variant: str = "full") -> str:
    """Подписывает идентификатор фото в виде `kind:id:variant`.

    Raises:
        ValueError: если kind / variant не из разрешённого списка.
    """
    if kind not in PHOTO_KINDS:
        raise ValueError(f"Unknown photo kind: {kind!r}")
    if variant not in PHOTO_VARIANTS:
        raise ValueError(f"Unknown photo variant: {variant!r}")
    signer = TimestampSigner(salt=_PHOTO_SALT)
    return signer.sign(f"{kind}:{int(photo_id)}:{variant}")


def parse_photo_token(token: str, max_age: int | None = None) -> tuple[str, int, str]:
    """Проверяет подпись и TTL, возвращает `(kind, id, variant)`.

    Raises:
        SignatureExpired: если токен старше `max_age` (или TTL по умолчанию).
        BadSignature: если подпись не сходится / payload подделан.
        ValueError: если payload в неправильном формате.
    """
    signer = TimestampSigner(salt=_PHOTO_SALT)
    raw = signer.unsign(token, max_age=max_age if max_age is not None else _ttl())
    parts = raw.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid photo token payload: {raw!r}")
    kind, raw_id, variant = parts
    if kind not in PHOTO_KINDS or variant not in PHOTO_VARIANTS or not raw_id.isdigit():
        raise ValueError(f"Invalid photo token payload: {raw!r}")
    return kind, int(raw_id), variant


def make_container_token(container_number: str) -> str:
    """Подписывает номер контейнера. Используется как «proof of view»:
    клиент сначала получает список фото через `get_container_photos`,
    оттуда же получает этот токен, и только с ним может попросить
    архив `download_photos_archive`.

    Это закрывает дыру «перебором photo_id скачать все публичные фото»:
    без `container_token` сервер не примет запрос вовсе, а внутри
    `photo_ids` мы дополнительно проверяем, что все они из этого
    контейнера.
    """
    if not container_number:
        raise ValueError("container_number обязателен")
    signer = TimestampSigner(salt=_CONTAINER_SALT)
    return signer.sign(str(container_number))


def parse_container_token(token: str, max_age: int | None = None) -> str:
    """Проверяет container_token и возвращает container_number.

    Те же исключения, что и `parse_photo_token`.
    """
    signer = TimestampSigner(salt=_CONTAINER_SALT)
    return signer.unsign(token, max_age=max_age if max_age is not None else _ttl())


__all__ = [
    "BadSignature",
    "DEFAULT_TTL_SECONDS",
    "PHOTO_KINDS",
    "PHOTO_VARIANTS",
    "SignatureExpired",
    "make_container_token",
    "make_photo_token",
    "parse_container_token",
    "parse_photo_token",
]
