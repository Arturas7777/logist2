#!/usr/bin/env bash
# Quick MySQL audit: who/what is using it on this server.
set -uo pipefail

echo '=== DATABASES ==='
mysql -e 'SHOW DATABASES;' 2>&1 | head -30

echo
echo '=== TABLE SIZES PER DB (MB) ==='
mysql -e "SELECT table_schema AS db, COUNT(*) AS tables, ROUND(SUM(data_length+index_length)/1024/1024,1) AS size_mb FROM information_schema.tables GROUP BY table_schema ORDER BY size_mb DESC;" 2>&1 | head -30

echo
echo '=== ACTIVE CONNECTIONS RIGHT NOW ==='
mysql -e 'SHOW PROCESSLIST;' 2>&1 | head -40

echo
echo '=== STATS (uptime, queries, connections) ==='
mysql -e "SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime','Queries','Connections','Threads_connected','Max_used_connections','Com_select','Com_insert','Com_update');" 2>&1

echo
echo '=== TCP CLIENTS CONNECTED TO 3306 ==='
ss -tnp 2>/dev/null | awk 'NR==1 || /:3306/' | head -20

echo
echo '=== PROCESSES THAT LINK MYSQL CLIENT LIBS ==='
for pid in $(pgrep -a . 2>/dev/null | awk '{print $1}' | head -200); do
  if [ -r /proc/$pid/maps ] && grep -lqE 'libmysqlclient|libmariadb' /proc/$pid/maps 2>/dev/null; then
    cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null | head -c 200)
    echo "PID $pid  $cmd"
  fi
done 2>/dev/null | head -30

echo
echo '=== ISPMANAGER MYSQL USAGE (если ispmgr) ==='
ls /usr/local/mgr5/etc 2>/dev/null | head -5 || echo 'no ispmgr config'

echo
echo '=== CRYPTO-BOT CHECK ==='
ls /var/www/crypto-bot 2>/dev/null | head -10
grep -lE 'mysql|pymysql|aiomysql' /var/www/crypto-bot/*.py /var/www/crypto-bot/*.txt /var/www/crypto-bot/*.cfg 2>/dev/null | head -10

echo
echo '=== DONE ==='
