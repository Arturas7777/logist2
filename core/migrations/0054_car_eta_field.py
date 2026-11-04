# Generated manually for car ETA improvements
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0053_transportrequest_driver_passport"),
    ]

    operations = [
        migrations.AddField(
            model_name="car",
            name="_eta_override",
            field=models.DateField(
                blank=True,
                db_column="eta",
                help_text="Явная ETA для машины (переопределяет ETA контейнера). NULL = наследуется от контейнера.",
                null=True,
                verbose_name="ETA (переопределение)",
            ),
        ),
    ]
