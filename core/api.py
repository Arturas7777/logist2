from rest_framework import permissions, viewsets

from .models import Car
from .models_billing import NewInvoice as Invoice
from .serializers import CarSerializer, InvoiceSerializer


class ReadOnlyForStaff(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)


class CarViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Car.objects.select_related(
        'client', 'warehouse', 'container', 'container__line', 'line', 'carrier'
    ).all()
    serializer_class = CarSerializer
    permission_classes = [ReadOnlyForStaff]


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Invoice.objects.select_related(
        'recipient_client', 'recipient_warehouse', 'recipient_company',
        'recipient_line', 'recipient_carrier',
        'issuer_company', 'issuer_warehouse', 'issuer_line', 'issuer_carrier',
        'category',
    ).prefetch_related('cars').all()
    serializer_class = InvoiceSerializer
    permission_classes = [ReadOnlyForStaff]
