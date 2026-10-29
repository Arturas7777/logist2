from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0051_bots_access"),
    ]

    operations = [
        migrations.AddField(
            model_name="car",
            name="foreign_eta",
            field=models.DateField(
                blank=True,
                help_text="Для машины без своего контейнера. ETA нашего контейнера живёт на Container.",
                null=True,
                verbose_name="ETA чужого контейнера",
            ),
        ),
    ]
