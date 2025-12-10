from rest_framework import serializers
from .models import Car
from .models_billing import NewInvoice

class CarSerializer(serializers.ModelSerializer):
    text = serializers.CharField(source='__str__')
    class Meta:
        model = Car
        fields = ['id', 'text', 'vin', 'brand', 'year', 'status', 'warehouse', 'client', 'current_price', 'total_price', 'storage_cost', 'days']


class InvoiceSerializer(serializers.ModelSerializer):
    cars = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    recipient_name = serializers.SerializerMethodField()
    
    class Meta:
        model = NewInvoice
        fields = ['id', 'number', 'recipient', 'recipient_name', 'total', 'created_at', 'status', 'cars']
    
    def get_recipient_name(self, obj):
        return str(obj.recipient) if obj.recipient else None