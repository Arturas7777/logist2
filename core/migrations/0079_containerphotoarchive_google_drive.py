# Generated manually for Google Drive integration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0078_alter_accounting_options_alter_declaration_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='containerphotoarchive',
            name='google_drive_url',
            field=models.URLField(blank=True, help_text='Публичная ссылка на архив с фотографиями в Google Drive', max_length=500, verbose_name='Ссылка на Google Drive'),
        ),
        migrations.AddField(
            model_name='containerphotoarchive',
            name='download_error',
            field=models.TextField(blank=True, verbose_name='Ошибка загрузки'),
        ),
        migrations.AlterField(
            model_name='containerphotoarchive',
            name='archive_file',
            field=models.FileField(blank=True, null=True, upload_to='container_archives/%Y/%m/%d/', verbose_name='Архивный файл'),
        ),
    ]

