from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0031_vehicle_types_jetski_snowmobile"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentInboxWatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False, verbose_name="Включён")),
                ("enabled_at", models.DateTimeField(blank=True, null=True, verbose_name="Включён в")),
                (
                    "analyze_since",
                    models.DateTimeField(
                        blank=True,
                        help_text="Письма с received_at раньше этой метки не анализируются, кроме bootstrap_ids.",
                        null=True,
                        verbose_name="Разбирать письма не старше",
                    ),
                ),
                (
                    "bootstrap_ids",
                    models.JSONField(blank=True, default=list, verbose_name="Стартовые письма"),
                ),
                (
                    "updated_by",
                    models.CharField(blank=True, default="", max_length=150, verbose_name="Изменил"),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Разбор почты агентом",
                "verbose_name_plural": "Разбор почты агентом",
            },
        ),
    ]
