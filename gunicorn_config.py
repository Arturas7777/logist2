"""
Gunicorn configuration for production
"""
import multiprocessing

# Server socket
bind = "127.0.0.1:8000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'sync'
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = '/var/log/logist2/gunicorn-access.log'
errorlog = '/var/log/logist2/gunicorn-error.log'
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = 'logist2'

# Server mechanics
daemon = False
pidfile = '/var/run/logist2/gunicorn.pid'
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (if terminating SSL at gunicorn instead of nginx)
# keyfile = '/path/to/key.pem'
# certfile = '/path/to/cert.pem'

