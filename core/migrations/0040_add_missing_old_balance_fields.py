# Generated manually to fix missing old balance fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0039_new_balance_system'),
    ]

    operations = [
        # This migration is no longer needed as the fields already exist
        # The fields cash_balance_old and card_balance_old are already present
        # in the database from previous migrations
    ]
