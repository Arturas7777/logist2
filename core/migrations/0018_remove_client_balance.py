# Generated by Django 5.1.7 on 2025-05-19 20:36

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_alter_client_options_alter_invoice_options_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='client',
            name='balance',
        ),
    ]
