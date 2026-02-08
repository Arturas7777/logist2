import logging

from django import forms
from django.forms import ModelForm
from core.models import Line, Carrier, Warehouse, LineService, CarrierService, WarehouseService

logger = logging.getLogger('django')


class LineForm(ModelForm):
    class Meta:
        model = Line
        fields = '__all__'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Добавляем динамические поля для услуг
        if self.instance.pk:
            for service in self.instance.services.all():
                field_name = f'service_{service.id}'
                self.fields[field_name] = forms.DecimalField(
                    label=service.name,
                    initial=service.default_price,
                    required=False,
                    decimal_places=2,
                    max_digits=10,
                )
    
    def save(self, commit=True):
        instance = super().save(commit=commit)
        
        if commit and instance.pk:
            # Сохраняем значения динамических полей
            for field_name, value in self.cleaned_data.items():
                if field_name.startswith('service_'):
                    service_id = field_name.replace('service_', '')
                    try:
                        service = LineService.objects.get(id=service_id, line=instance)
                        service.default_price = value or 0
                        service.save()
                    except LineService.DoesNotExist:
                        pass
        
        return instance


class CarrierForm(ModelForm):
    class Meta:
        model = Carrier
        fields = '__all__'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Добавляем динамические поля для услуг
        if self.instance.pk:
            for service in self.instance.services.all():
                field_name = f'service_{service.id}'
                self.fields[field_name] = forms.DecimalField(
                    label=service.name,
                    initial=service.default_price,
                    required=False,
                    decimal_places=2,
                    max_digits=10,
                )
    
    def save(self, commit=True):
        instance = super().save(commit=commit)
        
        if commit and instance.pk:
            # Сохраняем значения динамических полей
            for field_name, value in self.cleaned_data.items():
                if field_name.startswith('service_'):
                    service_id = field_name.replace('service_', '')
                    try:
                        service = CarrierService.objects.get(id=service_id, carrier=instance)
                        service.default_price = value or 0
                        service.save()
                    except CarrierService.DoesNotExist:
                        pass
        
        return instance


class WarehouseForm(ModelForm):
    class Meta:
        model = Warehouse
        fields = '__all__'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Добавляем динамические поля для услуг
        if self.instance.pk:
            for service in self.instance.services.all():
                field_name = f'service_{service.id}'
                self.fields[field_name] = forms.DecimalField(
                    label=service.name,
                    initial=service.default_price,
                    required=False,
                    decimal_places=2,
                    max_digits=10,
                )
                # Добавляем поле для удаления
                delete_field_name = f'delete_service_{service.id}'
                self.fields[delete_field_name] = forms.BooleanField(
                    label=f'Удалить {service.name}',
                    required=False,
                    initial=False,
                    widget=forms.HiddenInput()  # Делаем скрытым
                )
    
    def save(self, commit=True):
        instance = super().save(commit=commit)
        
        if commit and instance.pk:
            logger.debug("=== WAREHOUSE FORM SAVE ===")
            logger.debug("cleaned_data: %s", self.cleaned_data)
            
            # Обрабатываем удаление услуг
            for field_name, value in self.cleaned_data.items():
                if field_name.startswith('delete_service_'):
                    service_id = field_name.replace('delete_service_', '')
                    logger.debug(f"Найдено поле удаления: {field_name} = {value}, service_id = {service_id}")
                    if service_id.isdigit():
                        try:
                            service = WarehouseService.objects.get(id=service_id, warehouse=instance)
                            logger.debug(f"Удаляем услугу: {service.name}")
                            service.delete()
                        except WarehouseService.DoesNotExist:
                            logger.warning(f"Услуга с ID {service_id} не найдена")
                            pass
            
            # Обрабатываем новые услуги
            for field_name, value in self.cleaned_data.items():
                if field_name.startswith('new_service_name_'):
                    index = field_name.replace('new_service_name_', '')
                    name = value
                    price_field = f'new_service_price_{index}'
                    price = self.cleaned_data.get(price_field, 0)
                    
                    if name:
                        try:
                            WarehouseService.objects.create(
                                warehouse=instance,
                                name=name,
                                default_price=float(price) if price else 0
                            )
                            logger.debug(f"Создана новая услуга: {name} с ценой {price}")
                        except ValueError as e:
                            logger.error(f"Ошибка при создании услуги: {e}")
                            pass
            
            # Сохраняем значения существующих услуг
            for field_name, value in self.cleaned_data.items():
                if field_name.startswith('service_') and not field_name.startswith('delete_service_') and not field_name.startswith('new_service_'):
                    service_id = field_name.replace('service_', '')
                    if service_id.isdigit():
                        try:
                            service = WarehouseService.objects.get(id=service_id, warehouse=instance)
                            service.default_price = value or 0
                            service.save()
                        except WarehouseService.DoesNotExist:
                            pass
        
        return instance
