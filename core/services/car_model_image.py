"""Подбор иллюстрации модели авто по марке и году (поколению)."""


def _as_year(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def select_car_model_image(records, year, brand):
    """Выбирает запись картинки модели с учётом поколения.

    Фото с годом Y действует для авто этой марки/модели с годом выпуска
    >= Y — до следующего (более нового) поколения. Более новое фото
    не показывается на более старых машинах.

    Приоритет внутри одной марки: точный год > наибольший год записи,
    который не новее авто > запись без года (для любого года).
    Точное совпадение марки важнее префикса («BMW» ⊂ «BMW 430I»).
    """
    if not brand:
        return None
    brand_norm = brand.strip().lower()
    if not brand_norm:
        return None
    car_year = _as_year(year)

    best = None
    best_score = None
    for rec in records:
        rec_brand = (getattr(rec, "brand", None) or "").strip().lower()
        if not rec_brand:
            continue
        exact_brand = rec_brand == brand_norm
        if not exact_brand and not brand_norm.startswith(rec_brand):
            continue

        rec_year = _as_year(getattr(rec, "year", None))
        if rec_year is not None and car_year is not None and rec_year > car_year:
            continue

        if rec_year is not None and rec_year == car_year:
            year_tier = 3
            year_key = rec_year
        elif rec_year is not None:
            year_tier = 2
            year_key = rec_year
        else:
            year_tier = 1
            year_key = 0

        score = (
            1 if exact_brand else 0,
            len(rec_brand),
            year_tier,
            year_key,
        )
        if best_score is None or score > best_score:
            best, best_score = rec, score
    return best


def car_model_image_media_url(record, *, thumbnail=False):
    """MEDIA-URL картинки с cache-bust по ``updated_at``. ``None`` если файла нет."""
    field = None
    if thumbnail:
        field = getattr(record, "thumbnail", None)
    if not field:
        field = getattr(record, "image", None)
    if not field:
        return None
    try:
        url = field.url
    except ValueError:
        return None
    updated_at = getattr(record, "updated_at", None)
    if updated_at:
        url = f"{url}?v={int(updated_at.timestamp())}"
    return url


def find_car_model_image_url(year, brand):
    """Ищет картинку модели в БД. Возвращает MEDIA-URL или None."""
    if not brand:
        return None
    from core.models import CarModelImage

    records = list(CarModelImage.objects.filter(is_active=True).exclude(image=""))
    match = select_car_model_image(records, year, brand)
    if not match:
        return None
    return car_model_image_media_url(match)
