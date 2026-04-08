"""
Machine-readable service codes used in business logic.

Use these constants instead of matching services by their human-readable name.
The ``code`` field on BaseService (SlugField) should be populated to match
these values.  Legacy lookup by ``name`` is supported via ``NAME_TO_CODE``.
"""


class ServiceCode:
    STORAGE = 'storage'
    THS = 'ths'
    UNLOADING = 'unloading'
    DELIVERY = 'delivery'
    LOADING = 'loading'
    DOCUMENTS = 'documents'
    TRANSFER = 'transfer'
    TRANSIT_DECLARATION = 'transit_declaration'
    EXPORT_DECLARATION = 'export_declaration'
    EXTRA_COSTS = 'extra_costs'
    COMPLEX = 'complex'
    DAILY_RATE = 'daily_rate'
    FREE_DAYS = 'free_days'


# Mapping from legacy Russian service names to codes.
# Used for backward-compatible lookups until all services have ``code`` populated.
NAME_TO_CODE: dict[str, str] = {
    'Хранение': ServiceCode.STORAGE,
    'Цена за разгрузку': ServiceCode.UNLOADING,
    'Доставка до склада': ServiceCode.DELIVERY,
    'Погрузка на трал': ServiceCode.LOADING,
    'Документы': ServiceCode.DOCUMENTS,
    'Плата за передачу': ServiceCode.TRANSFER,
    'Транзитная декл.': ServiceCode.TRANSIT_DECLARATION,
    'Экспортная декл.': ServiceCode.EXPORT_DECLARATION,
    'Доп.расходы': ServiceCode.EXTRA_COSTS,
    'Комплекс': ServiceCode.COMPLEX,
    'Ставка за сутки': ServiceCode.DAILY_RATE,
    'Бесплатные дни': ServiceCode.FREE_DAYS,
}

CODE_TO_NAME: dict[str, str] = {v: k for k, v in NAME_TO_CODE.items()}

# Short names for invoice table columns
CODE_TO_SHORT: dict[str, str] = {
    ServiceCode.STORAGE: 'Хран',
    ServiceCode.THS: 'THS',
    ServiceCode.UNLOADING: 'Разгр',
    ServiceCode.DELIVERY: 'Дост',
    ServiceCode.LOADING: 'Погр',
    ServiceCode.DOCUMENTS: 'Док',
    ServiceCode.TRANSFER: 'Перед',
    ServiceCode.COMPLEX: 'Компл',
}


def is_storage_service(service) -> bool:
    """Check whether a service object represents the Storage service."""
    if hasattr(service, 'code') and service.code:
        return service.code == ServiceCode.STORAGE
    name = getattr(service, 'name', '')
    if not name and callable(getattr(service, 'get_service_name', None)):
        name = service.get_service_name()
    return name == 'Хранение'


def is_ths_service(service) -> bool:
    """Check whether a service object represents a THS service."""
    if hasattr(service, 'code') and service.code:
        return service.code == ServiceCode.THS
    name = getattr(service, 'name', '')
    if callable(getattr(service, 'get_service_name', None)):
        name = service.get_service_name()
    return 'THS' in name.upper() if name else False


def service_matches_code(service, code: str) -> bool:
    """Check if a service matches a given code (with name fallback)."""
    if hasattr(service, 'code') and service.code:
        return service.code == code
    expected_name = CODE_TO_NAME.get(code)
    return expected_name is not None and getattr(service, 'name', '') == expected_name
