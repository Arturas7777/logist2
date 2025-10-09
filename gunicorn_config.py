# Gunicorn configuration file

# Server socket
bind = '127.0.0.1:8000'
backlog = 2048

# Worker processes
workers = 4  # Рекомендуется (2 x CPU cores) + 1
worker_class = 'sync'
worker_connections = 1000
timeout = 120
keepalive = 5

# Restart workers after this many requests (to prevent memory leaks)
max_requests = 1000
max_requests_jitter = 50

# Logging
accesslog = '/var/log/gunicorn/caromoto-lt-access.log'
errorlog = '/var/log/gunicorn/caromoto-lt-error.log'
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = 'caromoto-lt'

# Server mechanics
daemon = False  # Systemd будет управлять процессом
pidfile = '/var/run/caromoto-lt.pid'
user = 'www-data'
group = 'www-data'
tmp_upload_dir = None

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
