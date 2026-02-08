import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings')
app = Celery('logist2')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
