from rest_framework import viewsets, permissions
from .models import Car, InvoiceOLD as Invoice
from .serializers import CarSerializer, InvoiceSerializer


class ReadOnlyForStaff(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)


class CarViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Car.objects.select_related('client', 'warehouse', 'container').all()
    serializer_class = CarSerializer
    permission_classes = [ReadOnlyForStaff]


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Invoice.objects.select_related('client', 'warehouse').prefetch_related('cars').all()  # cars здесь - это ManyToMany в Invoice
    serializer_class = InvoiceSerializer
    permission_classes = [ReadOnlyForStaff]