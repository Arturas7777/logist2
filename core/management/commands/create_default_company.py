from django.core.management.base import BaseCommand
from core.models import Company

class Command(BaseCommand):
    help = 'Создает дефолтную компанию Caromoto Lithuania'

    def handle(self, *args, **options):
        company, created = Company.objects.get_or_create(
            name="Caromoto Lithuania",
            defaults={
                'invoice_balance': 0.00,
                'cash_balance': 0.00,
                'card_balance': 0.00
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS(f'Успешно создана компания "{company.name}"')
            )
        else:
            self.stdout.write(
                self.style.WARNING(f'Компания "{company.name}" уже существует')
            )


