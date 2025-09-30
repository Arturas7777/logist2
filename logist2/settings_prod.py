from .settings_base import *  # noqa

DEBUG = False

# Harden logging in prod
LOGGING['loggers']['django']['level'] = 'INFO'


