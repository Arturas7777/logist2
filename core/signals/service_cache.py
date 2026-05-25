"""Инвалидация per-instance кэша услуг (``svc:<type>:<id>``).

Кэш расшаривается между админкой и расчётами цен; при изменении любой
``LineService`` / ``WarehouseService`` / ``CarrierService`` /
``CompanyService`` мы сбрасываем соответствующий ключ.

Сигналы подключаются вручную через ``post_save.connect`` /
``post_delete.connect`` в самом низу модуля — один handler на 4 модели,
``@receiver`` тут не используется намеренно.
"""

from django.db.models.signals import post_delete, post_save

from core.models import CarrierService, CompanyService, LineService, WarehouseService


def invalidate_service_cache(sender, instance, **kwargs):
    from django.core.cache import cache

    type_map = {
        LineService: "LINE",
        WarehouseService: "WAREHOUSE",
        CarrierService: "CARRIER",
        CompanyService: "COMPANY",
    }
    svc_type = type_map.get(sender)
    if svc_type:
        cache.delete(f"svc:{svc_type}:{instance.id}")


for _model in (LineService, WarehouseService, CarrierService, CompanyService):
    post_save.connect(invalidate_service_cache, sender=_model)
    post_delete.connect(invalidate_service_cache, sender=_model)
