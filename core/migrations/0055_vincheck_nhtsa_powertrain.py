from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0054_car_eta_field"),
    ]

    operations = [
        migrations.AddField(
            model_name="vincheck",
            name="nhtsa_electrification",
            field=models.CharField(
                blank=True,
                default=None,
                help_text="Electrification Level. Пустая строка — обычный ДВС, NULL — ещё не запрашивали.",
                max_length=100,
                null=True,
                verbose_name="Электрификация (NHTSA)",
            ),
        ),
        migrations.AddField(
            model_name="vincheck",
            name="nhtsa_fuel_primary",
            field=models.CharField(
                blank=True,
                default=None,
                help_text="Fuel Type - Primary. Пустая строка — не указано, NULL — ещё не запрашивали.",
                max_length=50,
                null=True,
                verbose_name="Топливо (NHTSA)",
            ),
        ),
    ]
