from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0161_add_credit_note_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailIngestFilter',
            fields=[
                ('id', models.AutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name='ID',
                )),
                ('phrase', models.CharField(
                    help_text=(
                        'Фраза, наличие которой в теме или теле письма прячет '
                        'его из карточек. Для «подстроки» регистр не важен; '
                        'для «regex» — синтаксис Python re, поиск тоже без '
                        'учёта регистра.'
                    ),
                    max_length=500,
                    verbose_name='Ключевая фраза',
                )),
                ('scope', models.CharField(
                    choices=[
                        ('SUBJECT', 'Только тема'),
                        ('BODY', 'Только тело'),
                        ('ANY', 'Тема или тело'),
                    ],
                    default='ANY',
                    max_length=10,
                    verbose_name='Где искать',
                )),
                ('match_type', models.CharField(
                    choices=[
                        ('CONTAINS', 'Подстрока (без учёта регистра)'),
                        ('REGEX', 'Регулярное выражение (re.IGNORECASE)'),
                    ],
                    default='CONTAINS',
                    max_length=10,
                    verbose_name='Тип совпадения',
                )),
                ('is_active', models.BooleanField(
                    default=True, verbose_name='Активен',
                )),
                ('notes', models.CharField(
                    blank=True, default='',
                    help_text='Для чего нужен фильтр (служебная пометка).',
                    max_length=500,
                    verbose_name='Комментарий',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Фильтр Gmail-ингеста',
                'verbose_name_plural': 'Фильтры Gmail-ингеста',
                'ordering': ['-is_active', 'phrase'],
            },
        ),
        migrations.AddIndex(
            model_name='emailingestfilter',
            index=models.Index(
                fields=['is_active'],
                name='core_emaili_is_acti_6f7a1b_idx',
            ),
        ),
    ]
