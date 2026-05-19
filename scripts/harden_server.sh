#!/usr/bin/env bash
# Logist2 server hardening: swap + sysctl + resilient gunicorn unit
# Idempotent: safe to re-run.
set -euo pipefail

echo '=== STEP 1: Create 2GB swap (if missing) ==='
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'swap created'
else
  echo 'swapfile already exists'
  swapon /swapfile 2>/dev/null || true
fi
swapon --show || true
free -h

echo
echo '=== STEP 2: Tune vm sysctls ==='
cat > /etc/sysctl.d/99-logist2-mem.conf <<'SYS'
vm.swappiness = 10
vm.vfs_cache_pressure = 50
vm.overcommit_memory = 1
SYS
sysctl -p /etc/sysctl.d/99-logist2-mem.conf

echo
echo '=== STEP 3: Backup and update gunicorn.service ==='
cp /etc/systemd/system/gunicorn.service \
   /etc/systemd/system/gunicorn.service.bak.$(date +%Y%m%d-%H%M%S)
cat > /etc/systemd/system/gunicorn.service <<'UNIT'
[Unit]
Description=Gunicorn instance for logist2
After=network.target

[Service]
User=www-root
Group=www-root
WorkingDirectory=/var/www/www-root/data/www/logist2
Environment="PATH=/var/www/www-root/data/www/logist2/.venv/bin"
Environment="DJANGO_SETTINGS_MODULE=logist2.settings.prod"
ExecStart=/var/www/www-root/data/www/logist2/.venv/bin/gunicorn \
  --workers 3 \
  --worker-class sync \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --timeout 120 \
  --graceful-timeout 30 \
  --bind unix:/var/www/www-root/data/www/logist2/gunicorn.sock \
  logist2.wsgi:application

Restart=on-failure
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=300

MemoryHigh=1500M
MemoryMax=1800M

KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

echo
echo '=== STEP 4: Reload systemd and restart gunicorn ==='
systemctl daemon-reload
systemctl restart gunicorn
sleep 4
systemctl status gunicorn --no-pager | head -20

echo
echo '=== STEP 5: HTTP check ==='
curl -sS -o /dev/null -w 'HTTP %{http_code} in %{time_total}s\n' https://caromoto-lt.com/ || true

echo
echo '=== STEP 6: Final memory ==='
free -h
echo
echo 'Done.'
