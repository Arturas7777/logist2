from rest_framework import serializers
from .models import Car

class CarSerializer(serializers.ModelSerializer):
    text = serializers.CharField(source='__str__')
    class Meta:
        model = Car
        fields = ['id', 'text']