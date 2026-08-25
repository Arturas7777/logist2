"""Единая проверка VIN для всех точек ввода данных.

Раньше «умные» проверки VIN (контрольная цифра, NHTSA, поиск похожих
номеров) жили только в пайплайне сканов, а оператор, вводящий машину
руками, не получал ничего кроме проверки длины. Этот модуль сводит все
сигналы в один вызов :func:`check_vin`, который одинаково используют
и админка, и applier сканов, и аудит контейнера.

Сеть спрятана за кэшем :class:`core.models.VinCheck` — повторный вызов
для того же VIN не ходит в NHTSA. Если API недоступен, проверка
деградирует до контрольной цифры и поиска дублей, но не падает и не
блокирует сохранение.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.models.vin_checks import VinCheck
from core.services.vin_corrector import (
    VALID_VIN_CHARS,
    correct_vin_by_checksum,
    hamming_distance,
)
from core.services.vin_validator import (
    decode_vin_nhtsa,
    is_north_american_vin,
    is_vin_checksum_valid,
)

logger = logging.getLogger(__name__)

# Коды находок. Совпадают с кодами в container_audit там, где речь об
# одном и том же — чтобы оператор видел одинаковые формулировки в форме
# ввода и в панели сверки.
ISSUE_LENGTH = "vin_length"
ISSUE_FORBIDDEN_CHARS = "vin_forbidden_chars"
ISSUE_CHECKSUM = "vin_checksum_failed"
ISSUE_NHTSA_UNKNOWN = "vin_nhtsa_unknown"
ISSUE_SPEC_MISMATCH = "vin_spec_mismatch"
ISSUE_NEAR_DUPLICATE = "vin_near_duplicate"

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"

# Запрещённые ISO 3779 символы и их визуальные двойники — подсказка
# оператору, а не автозамена: в ручном вводе менять набранное молча нельзя.
_FORBIDDEN_HINT = {"I": "1", "O": "0", "Q": "0"}

# Разговорные и сокращённые марки с тайтлов / dock receipt. Сравниваем
# каноническую форму, иначе «CHEVY» против «CHEVROLET» выглядит как
# чужой производитель и валит сверку ложным расхождением.
_MAKE_ALIASES = {
    "CHEVY": "CHEVROLET",
    "CHEV": "CHEVROLET",
    "VW": "VOLKSWAGEN",
    "VOLKS": "VOLKSWAGEN",
    "MB": "MERCEDES",
    "MERC": "MERCEDES",
}

# Отличие в 1-2 символа — типичная опечатка или OCR-ошибка.
_NEAR_DUPLICATE_MAX_DISTANCE = 2

_NHTSA_TIMEOUT = 5


@dataclass
class VinIssue:
    """Одна находка по VIN."""

    code: str
    severity: str
    message: str
    suggestion: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class VinVerdict:
    """Результат проверки одного VIN."""

    vin: str
    original: str
    issues: list[VinIssue] = field(default_factory=list)
    check: VinCheck | None = None

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def blocking_issues(self) -> list[VinIssue]:
        """Находки, из-за которых форма требует подтверждения оператора."""
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def summary(self) -> str:
        """Короткая строка для лога и заголовков."""
        if self.ok:
            return "проверка пройдена"
        return "; ".join(i.message for i in self.issues)

    def first_suggestion(self) -> str:
        for issue in self.issues:
            if issue.suggestion:
                return issue.suggestion
        return ""


def makes_match(left, right) -> bool:
    """Сравнивает марки, прощая сокращения: CHEV ↔ CHEVROLET, CHEVY ↔ CHEVROLET.

    В документах марка почти всегда сокращена («CHEV MALIBU»), а в карточке
    записана целиком («CHEVROLET MALIBU») — считать это расхождением значит
    завалить оператора ложными срабатываниями. Поэтому сравниваем только
    первое слово (после раскрытия псевдонимов) и признаём совпадением, если
    одно является началом другого. «TOYOTA» против «VOLKSWAGEN» расхождением
    остаётся — а это как раз тот случай, когда VIN, скорее всего, не тот.
    """
    first = _canonical_make(_first_word(left))
    second = _canonical_make(_first_word(right))
    # Слишком короткий огрызок ничего не доказывает — не спорим.
    if len(first) < 3 or len(second) < 3:
        return True
    return first.startswith(second) or second.startswith(first)


def extracted_make_agrees(extracted, nhtsa_make, nhtsa_model=None) -> bool:
    """OCR-марка из документа не противоречит расшифровке NHTSA.

    На тайтлах марка сокращается (CHEVY), пишется вместе с моделью
    (CHEV EQUINOX) или вместо марки попадает сама модель (EQUINOX). Это не
    ошибка VIN: NHTSA по нему уже сказал, какая это машина. Чужой
    производитель («TOYOTA» против «CHEVROLET») по-прежнему не сходится.
    """
    if not (extracted or "").strip():
        return True
    if nhtsa_make and makes_match(extracted, nhtsa_make):
        return True
    if nhtsa_model and makes_match(extracted, nhtsa_model):
        return True
    return not (nhtsa_make or "").strip() and not (nhtsa_model or "").strip()


def brand_from_nhtsa(make, model, *, max_length: int = 50) -> str:
    """«CHEVROLET Equinox» — каноническая марка+модель из расшифровки VIN."""
    label = " ".join(part.strip() for part in (make, model) if part and str(part).strip())
    return label[:max_length]


def _canonical_make(word: str) -> str:
    return _MAKE_ALIASES.get(word, word)


def _first_word(value) -> str:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in (value or "").upper())
    parts = cleaned.split()
    return parts[0] if parts else ""


def normalize_vin_input(vin) -> str:
    """Приводит введённый VIN к каноническому виду: без пробелов, заглавные.

    Это единственное преобразование, которое делается молча. Замену
    похожих символов (I/O/Q) молча не делаем — см. :func:`check_vin`.
    """
    return "".join((vin or "").split()).upper()


# ── Кэш проверок ──────────────────────────────────────────────────────────


def refresh_vin_check(vin: str, *, timeout: int = _NHTSA_TIMEOUT, use_nhtsa: bool = True) -> VinCheck | None:
    """Опрашивает NHTSA и сохраняет снимок в :class:`VinCheck`.

    Возвращает ``None`` только для VIN неподходящей длины — для такого
    хранить нечего. Сетевая ошибка не исключение: снимок сохраняется с
    ``nhtsa_ok=False``, чтобы следующий вызов через неделю попробовал снова.
    """
    vin_norm = normalize_vin_input(vin)
    if len(vin_norm) != 17:
        return None

    defaults: dict[str, Any] = {
        "length_ok": True,
        "checksum_ok": is_vin_checksum_valid(vin_norm),
        "is_north_american": is_north_american_vin(vin_norm),
        "nhtsa_ok": False,
        "nhtsa_make": "",
        "nhtsa_model": "",
        "nhtsa_year": None,
        "error_text": "",
    }

    if use_nhtsa:
        decoded = decode_vin_nhtsa(vin_norm, timeout=timeout)
        if decoded.get("raw_failed"):
            defaults["error_text"] = "NHTSA недоступен"
        else:
            defaults["nhtsa_make"] = (decoded.get("make") or "")[:100]
            defaults["nhtsa_model"] = (decoded.get("model") or "")[:100]
            defaults["nhtsa_year"] = decoded.get("year")
            defaults["error_text"] = (decoded.get("error_text") or "")[:255]
            # Для европейских и азиатских VIN контрольная цифра по ISO не
            # обязана сходиться, и NHTSA честно возвращает ошибку. Если при
            # этом марка и год декодированы — VIN считаем подтверждённым.
            partial_ok = bool(decoded.get("make") and decoded.get("year"))
            defaults["nhtsa_ok"] = bool(decoded.get("ok")) or (partial_ok and not defaults["is_north_american"])

    from django.utils import timezone

    defaults["checked_at"] = timezone.now()
    obj, _created = VinCheck.objects.update_or_create(vin=vin_norm, defaults=defaults)
    return obj


def get_vin_check(vin: str, *, allow_network: bool = True, timeout: int = _NHTSA_TIMEOUT) -> VinCheck | None:
    """Отдаёт снимок проверки из кэша, при необходимости обновляя его.

    ``allow_network=False`` — режим аудита и списков: работаем только с
    тем, что уже посчитано, чтобы отрисовка страницы не зависела от сети.
    """
    vin_norm = normalize_vin_input(vin)
    if len(vin_norm) != 17:
        return None
    existing = VinCheck.objects.filter(vin=vin_norm).first()
    if existing is not None and not existing.is_stale:
        return existing
    if not allow_network:
        return existing
    try:
        return refresh_vin_check(vin_norm, timeout=timeout) or existing
    except Exception:
        logger.warning("Не удалось обновить VinCheck для %s", vin_norm, exc_info=True)
        return existing


def schedule_vin_check(vin: str) -> None:
    """Ставит обновление кэша в фон; без брокера выполняет синхронно."""
    vin_norm = normalize_vin_input(vin)
    if len(vin_norm) != 17:
        return
    from core.tasks import refresh_vin_check_task

    try:
        refresh_vin_check_task.delay(vin_norm)
    except Exception:
        try:
            refresh_vin_check_task(vin_norm)  # type: ignore[call-arg]
        except Exception:
            logger.warning("Фоновая проверка VIN %s не запустилась", vin_norm, exc_info=True)


# ── Основная проверка ─────────────────────────────────────────────────────


def check_vin(
    vin,
    *,
    exclude_car_id: int | None = None,
    brand: str | None = None,
    year: int | None = None,
    allow_network: bool = True,
    check_duplicates: bool = True,
) -> VinVerdict:
    """Сводит все проверки одного VIN в единый вердикт.

    ``exclude_car_id`` — машина, которую редактируем: её собственный VIN
    не должен считаться дублем самого себя.
    ``brand`` / ``year`` — то, что оператор ввёл в форме; сверяются с
    ответом NHTSA, чтобы поймать ошибку в VIN по противоречию в марке.
    """
    original = (vin or "").strip()
    vin_norm = normalize_vin_input(vin)
    verdict = VinVerdict(vin=vin_norm, original=original)

    if not vin_norm:
        return verdict

    if len(vin_norm) != 17:
        verdict.issues.append(
            VinIssue(
                code=ISSUE_LENGTH,
                severity=SEVERITY_ERROR,
                message=f"VIN содержит {len(vin_norm)} символов вместо 17.",
            )
        )
        return verdict

    forbidden = _forbidden_chars(vin_norm)
    if forbidden:
        verdict.issues.append(
            VinIssue(
                code=ISSUE_FORBIDDEN_CHARS,
                severity=SEVERITY_ERROR,
                message=(
                    f"В VIN не используются буквы I, O и Q. Найдено: {', '.join(sorted({c for c, _ in forbidden}))}."
                ),
                suggestion=_apply_char_hints(vin_norm),
                details={"positions": [pos for _c, pos in forbidden]},
            )
        )
        # Дальнейшие проверки на заведомо невалидном наборе символов
        # только добавят шума — контрольная цифра для них не считается.
        return verdict

    is_na = is_north_american_vin(vin_norm)
    checksum_ok = is_vin_checksum_valid(vin_norm)
    if is_na and not checksum_ok:
        correction = correct_vin_by_checksum(vin_norm)
        suggestion = correction.get("corrected") or ""
        verdict.issues.append(
            VinIssue(
                code=ISSUE_CHECKSUM,
                severity=SEVERITY_ERROR,
                message=(
                    "Контрольная цифра не сходится. Для VIN из США и Канады это почти всегда ошибка в одном символе."
                ),
                suggestion=suggestion,
                details={"candidates": correction.get("candidates") or []},
            )
        )

    check = get_vin_check(vin_norm, allow_network=allow_network)
    verdict.check = check

    # Если контрольная цифра уже не сошлась, NHTSA скажет ровно о том же —
    # повторять одну проблему дважды разными словами незачем.
    checksum_reported = any(issue.code == ISSUE_CHECKSUM for issue in verdict.issues)
    if not checksum_reported and check is not None and not check.nhtsa_ok and check.error_text != "NHTSA недоступен":
        verdict.issues.append(
            VinIssue(
                code=ISSUE_NHTSA_UNKNOWN,
                severity=SEVERITY_ERROR,
                message=f"NHTSA не распознаёт этот VIN{f': {check.error_text}' if check.error_text else ''}.",
            )
        )

    if check is not None and check.nhtsa_ok:
        mismatch = _spec_mismatch(check, brand=brand, year=year)
        if mismatch:
            verdict.issues.append(mismatch)

    if check_duplicates:
        duplicate = _near_duplicate(vin_norm, exclude_car_id=exclude_car_id)
        if duplicate:
            verdict.issues.append(duplicate)

    return verdict


def _forbidden_chars(vin: str) -> list[tuple[str, int]]:
    return [(ch, i + 1) for i, ch in enumerate(vin) if ch in _FORBIDDEN_HINT or ch not in VALID_VIN_CHARS]


def _apply_char_hints(vin: str) -> str:
    """Как бы выглядел VIN, если заменить I/O/Q на визуальные двойники."""
    replaced = "".join(_FORBIDDEN_HINT.get(ch, ch) for ch in vin)
    return replaced if replaced != vin and set(replaced) <= VALID_VIN_CHARS else ""


def _spec_mismatch(check: VinCheck, *, brand: str | None, year: int | None) -> VinIssue | None:
    """Марка или год из карточки противоречат расшифровке VIN."""
    parts: list[str] = []
    details: dict[str, Any] = {}

    if year and check.nhtsa_year and int(year) != int(check.nhtsa_year):
        parts.append(f"год {year} вместо {check.nhtsa_year}")
        details["year"] = {"entered": int(year), "nhtsa": int(check.nhtsa_year)}

    if not makes_match(brand, check.nhtsa_make):
        parts.append(f"марка {brand} вместо {check.nhtsa_make}")
        details["brand"] = {"entered": brand, "nhtsa": check.nhtsa_make}

    if not parts:
        return None
    return VinIssue(
        code=ISSUE_SPEC_MISMATCH,
        severity=SEVERITY_ERROR,
        message=(f"Данные карточки расходятся с расшифровкой VIN: {', '.join(parts)}. Проверьте, тот ли VIN введён."),
        details=details,
    )


def _near_duplicate(vin: str, *, exclude_car_id: int | None) -> VinIssue | None:
    """В базе есть VIN, отличающийся на один-два символа."""
    from core.models import Car

    qs = Car.objects.exclude(vin="")
    if exclude_car_id:
        qs = qs.exclude(pk=exclude_car_id)

    matches: list[dict[str, Any]] = []
    for db_vin, car_id, car_brand in qs.values_list("vin", "id", "brand").iterator():
        if not db_vin or len(db_vin) != 17:
            continue
        dist = hamming_distance(vin, db_vin)
        if dist is not None and 0 < dist <= _NEAR_DUPLICATE_MAX_DISTANCE:
            matches.append({"vin": db_vin, "car_id": car_id, "brand": car_brand, "distance": dist})

    if not matches:
        return None
    matches.sort(key=lambda m: m["distance"])
    closest = matches[0]
    word = "символ" if closest["distance"] == 1 else "символа"
    return VinIssue(
        code=ISSUE_NEAR_DUPLICATE,
        severity=SEVERITY_ERROR,
        message=(
            f"В базе уже есть похожий VIN {closest['vin']} "
            f"({closest['brand'] or 'без марки'}), отличие {closest['distance']} {word}. "
            "Возможно, это одна и та же машина."
        ),
        suggestion=closest["vin"],
        details={"matches": matches[:5]},
    )
