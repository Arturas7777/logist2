from rest_framework import serializers
from .models import Car, InvoiceOLD as Invoice

class CarSerializer(serializers.ModelSerializer):
    text = serializers.CharField(source='__str__')
    class Meta:
        model = Car
        fields = ['id', 'text', 'vin', 'brand', 'year', 'status', 'warehouse', 'client', 'current_price', 'total_price', 'storage_cost', 'days']


class InvoiceSerializer(serializers.ModelSerializer):
    cars = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    client_name = serializers.CharField(source='client.name', read_only=True)
    class Meta:
        model = Invoice
        fields = ['id', 'number', 'client', 'client_name', 'warehouse', 'total_amount', 'issue_date', 'paid', 'is_outgoing', 'cars']