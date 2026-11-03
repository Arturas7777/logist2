from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0052_car_foreign_eta"),
    ]

    operations = [
        migrations.AddField(
            model_name="transportrequest",
            name="driver_passport",
            field=models.CharField(blank=True, max_length=50, verbose_name="Паспорт водителя"),
        ),
    ]
