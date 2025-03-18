from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Car, Container, Invoice

@receiver(post_save, sender=Car)
def update_related_on_car_save(sender, instance, **kwargs):
    if instance.container:
        instance.container.update_expenses()
    for invoice in Invoice.objects.filter(cars=instance):
        invoice.save()