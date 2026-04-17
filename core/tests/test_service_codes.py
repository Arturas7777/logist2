"""Tests for service_codes module."""
from django.test import TestCase

from core.service_codes import (
    CODE_TO_NAME,
    NAME_TO_CODE,
    ServiceCode,
    is_storage_service,
    is_ths_service,
    service_matches_code,
)


class _FakeService:
    def __init__(self, name='', code=''):
        self.name = name
        self.code = code

    def get_service_name(self):
        return self.name


class ServiceCodeConstantsTest(TestCase):
    def test_name_to_code_and_back(self):
        for name, code in NAME_TO_CODE.items():
            self.assertEqual(CODE_TO_NAME[code], name)

    def test_storage_code(self):
        self.assertEqual(ServiceCode.STORAGE, 'storage')

    def test_ths_code(self):
        self.assertEqual(ServiceCode.THS, 'ths')


class IsStorageServiceTest(TestCase):
    def test_by_code(self):
        svc = _FakeService(name='Whatever', code='storage')
        self.assertTrue(is_storage_service(svc))

    def test_by_name_fallback(self):
        svc = _FakeService(name='Хранение', code='')
        self.assertTrue(is_storage_service(svc))

    def test_non_storage(self):
        svc = _FakeService(name='Разгрузка', code='unloading')
        self.assertFalse(is_storage_service(svc))


class IsTHSServiceTest(TestCase):
    def test_by_code(self):
        svc = _FakeService(name='Some Name', code='ths')
        self.assertTrue(is_ths_service(svc))

    def test_by_name_contains_ths(self):
        svc = _FakeService(name='THS MSC Line', code='')
        self.assertTrue(is_ths_service(svc))

    def test_by_name_lowercase(self):
        svc = _FakeService(name='ths something', code='')
        self.assertTrue(is_ths_service(svc))

    def test_non_ths(self):
        svc = _FakeService(name='Ocean Freight', code='ocean')
        self.assertFalse(is_ths_service(svc))


class ServiceMatchesCodeTest(TestCase):
    def test_exact_code(self):
        svc = _FakeService(code='delivery')
        self.assertTrue(service_matches_code(svc, ServiceCode.DELIVERY))

    def test_name_fallback(self):
        svc = _FakeService(name='Доставка до склада', code='')
        self.assertTrue(service_matches_code(svc, ServiceCode.DELIVERY))

    def test_no_match(self):
        svc = _FakeService(name='Random', code='random')
        self.assertFalse(service_matches_code(svc, ServiceCode.DELIVERY))
