# Generated manually for new balance system (FIXED VERSION)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0038_remove_invoice_field_from_client'),
    ]

    operations = [
        # Create Balance model
        migrations.CreateModel(
            name='Balance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('object_id', models.PositiveIntegerField()),
                ('balance_type', models.CharField(choices=[('INVOICE', 'Invoice Balance'), ('CASH', 'Cash'), ('CARD', 'Card')], max_length=20, verbose_name='Balance Type')),
                ('amount', models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Amount')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
            ],
            options={
                'verbose_name': 'Balance',
                'verbose_name_plural': 'Balances',
                'unique_together': {('content_type', 'object_id', 'balance_type')},
            },
        ),
        
        # Create BalanceTransaction model
        migrations.CreateModel(
            name='BalanceTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transaction_type', models.CharField(choices=[('PAYMENT', 'Payment'), ('TRANSFER', 'Transfer'), ('INVOICE_CREATED', 'Invoice Created'), ('INVOICE_PAID', 'Invoice Paid'), ('ADJUSTMENT', 'Adjustment')], max_length=20, verbose_name='Transaction Type')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=15, verbose_name='Amount')),
                ('description', models.TextField(blank=True, verbose_name='Description')),
                ('sender_balance_type', models.CharField(choices=[('INVOICE', 'Invoice Balance'), ('CASH', 'Cash'), ('CARD', 'Card')], max_length=20, verbose_name='Sender Balance Type')),
                ('recipient_balance_type', models.CharField(choices=[('INVOICE', 'Invoice Balance'), ('CASH', 'Cash'), ('CARD', 'Card')], max_length=20, verbose_name='Recipient Balance Type')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('created_by', models.CharField(blank=True, max_length=100, verbose_name='Created By')),
                ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.invoice', verbose_name='Invoice')),
                ('recipient_content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_transactions', to='contenttypes.contenttype')),
                ('sender_content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_transactions', to='contenttypes.contenttype')),
                ('sender_object_id', models.PositiveIntegerField()),
                ('recipient_object_id', models.PositiveIntegerField()),
            ],
            options={
                'verbose_name': 'Balance Transaction',
                'verbose_name_plural': 'Balance Transactions',
            },
        ),
        
        # Create Company model
        migrations.CreateModel(
            name='Company',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='Caromoto Lithuania', max_length=100, verbose_name='Company Name')),
                ('invoice_balance', models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Invoice Balance')),
                ('cash_balance', models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Cash Balance')),
                ('card_balance', models.DecimalField(decimal_places=2, default=0.00, max_digits=15, verbose_name='Card Balance')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
            ],
            options={
                'verbose_name': 'Company',
                'verbose_name_plural': 'Companies',
            },
        ),
        
        # Modify Payment model - add TRANSFER to choices
        migrations.AlterField(
            model_name='payment',
            name='payment_type',
            field=models.CharField(choices=[('CASH', 'Cash'), ('CARD', 'Card'), ('BALANCE', 'Balance'), ('TRANSFER', 'Transfer')], max_length=20, verbose_name='Payment Type'),
        ),
        migrations.AlterField(
            model_name='payment',
            name='amount',
            field=models.DecimalField(decimal_places=2, max_digits=15, verbose_name='Amount'),
        ),
        
        # Add new fields to Payment model
        migrations.AddField(
            model_name='payment',
            name='sender_content_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sent_payments', to='contenttypes.contenttype'),
        ),
        migrations.AddField(
            model_name='payment',
            name='sender_object_id',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='recipient_content_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='received_payments', to='contenttypes.contenttype'),
        ),
        migrations.AddField(
            model_name='payment',
            name='recipient_object_id',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='sender_balance_type',
            field=models.CharField(blank=True, choices=[('INVOICE', 'Invoice Balance'), ('CASH', 'Cash'), ('CARD', 'Card')], max_length=20, verbose_name='Sender Balance Type'),
        ),
        migrations.AddField(
            model_name='payment',
            name='recipient_balance_type',
            field=models.CharField(blank=True, choices=[('INVOICE', 'Invoice Balance'), ('CASH', 'Cash'), ('CARD', 'Card')], max_length=20, verbose_name='Recipient Balance Type'),
        ),
        migrations.AddField(
            model_name='payment',
            name='created_by',
            field=models.CharField(blank=True, max_length=100, verbose_name='Created By'),
        ),
        migrations.AddField(
            model_name='payment',
            name='balance_transaction',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.balancetransaction', verbose_name='Balance Transaction'),
        ),
        
        # Add indexes
        migrations.AddIndex(
            model_name='balance',
            index=models.Index(fields=['content_type', 'object_id'], name='core_balanc_content_8c8c8c_idx'),
        ),
        migrations.AddIndex(
            model_name='balance',
            index=models.Index(fields=['balance_type'], name='core_balanc_balance_8c8c8c_idx'),
        ),
        migrations.AddIndex(
            model_name='balancetransaction',
            index=models.Index(fields=['sender_content_type', 'sender_object_id'], name='core_balanc_sender__8c8c8c_idx'),
        ),
        migrations.AddIndex(
            model_name='balancetransaction',
            index=models.Index(fields=['recipient_content_type', 'recipient_object_id'], name='core_balanc_recipie_8c8c8c_idx'),
        ),
        migrations.AddIndex(
            model_name='balancetransaction',
            index=models.Index(fields=['transaction_type'], name='core_balanc_transac_8c8c8c_idx'),
        ),
        migrations.AddIndex(
            model_name='balancetransaction',
            index=models.Index(fields=['created_at'], name='core_balanc_created_8c8c8c_idx'),
        ),
    ]
