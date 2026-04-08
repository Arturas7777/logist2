"""
Add PERSONAL category type and seed personal expense categories.
"""

from django.db import migrations, models


def seed_personal_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model('core', 'ExpenseCategory')
    categories = [
        ('Личные расходы', 'ЛИЧН', 'PERSONAL', 100),
        ('Продукты', 'ПРОД', 'PERSONAL', 101),
        ('Транспорт (личный)', 'ТРАНС', 'PERSONAL', 102),
        ('Развлечения', 'РАЗВЛ', 'PERSONAL', 103),
        ('Здоровье', 'ЗДОР', 'PERSONAL', 104),
        ('Одежда', 'ОДЕЖ', 'PERSONAL', 105),
    ]
    for name, short_name, cat_type, order in categories:
        ExpenseCategory.objects.get_or_create(
            name=name,
            defaults={
                'short_name': short_name,
                'category_type': cat_type,
                'order': order,
                'is_active': True,
            },
        )


def remove_personal_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model('core', 'ExpenseCategory')
    ExpenseCategory.objects.filter(category_type='PERSONAL').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0140_add_skip_ai_comparison'),
    ]

    operations = [
        migrations.AlterField(
            model_name='expensecategory',
            name='category_type',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('OPERATIONAL', 'Операционные'),
                    ('ADMINISTRATIVE', 'Административные'),
                    ('SALARY', 'Зарплаты'),
                    ('MARKETING', 'Маркетинг'),
                    ('TAX', 'Налоги и сборы'),
                    ('PERSONAL', 'Личные расходы'),
                    ('OTHER', 'Прочие'),
                ],
                default='OTHER',
                verbose_name='Тип категории',
            ),
        ),
        migrations.RunPython(seed_personal_categories, remove_personal_categories),
    ]
