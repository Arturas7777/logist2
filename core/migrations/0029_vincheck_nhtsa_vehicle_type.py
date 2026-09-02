from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_transportcmr"),
    ]

    operations = [
        migrations.AddField(
            model_name="vincheck",
            name="nhtsa_vehicle_type",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Сырое значение Vehicle Type из NHTSA, например MOTORCYCLE.",
                max_length=100,
                verbose_name="Тип ТС (NHTSA)",
            ),
        ),
    ]
