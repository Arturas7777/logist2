from .settings_base import *  # noqa

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']

LOGGING['loggers']['django']['level'] = 'DEBUG'


