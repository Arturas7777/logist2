#!/usr/bin/env bash
# Detailed MySQL memory diagnosis: shows what exactly is eating RAM.
set -uo pipefail

echo '=== 1. MYSQL PROCESS RSS ==='
ps -o pid,rss,vsz,cmd -C mysqld 2>/dev/null

echo
echo '=== 2. MYSQL VERSION ==='
mysql -e "SELECT VERSION();" 2>&1

echo
echo '=== 3. ACTIVE CONFIG FILES ==='
mysqld --verbose --help 2>/dev/null | grep -A 1 'Default options' | tail -3
ls -la /etc/mysql/ 2>/dev/null
ls -la /etc/mysql/mariadb.conf.d/ 2>/dev/null
ls -la /etc/mysql/mysql.conf.d/ 2>/dev/null
ls -la /etc/mysql/conf.d/ 2>/dev/null

echo
echo '=== 4. CUSTOM CONFIG CONTENT ==='
for f in /etc/mysql/my.cnf /etc/mysql/mariadb.cnf /etc/mysql/mariadb.conf.d/*.cnf /etc/mysql/mysql.conf.d/*.cnf /etc/mysql/conf.d/*.cnf; do
  if [ -f "$f" ]; then
    echo "--- $f ---"
    grep -vE '^\s*(#|$)' "$f" 2>/dev/null
  fi
done

echo
echo '=== 5. KEY BUFFER VARIABLES (BYTES) ==='
mysql -e "SHOW VARIABLES WHERE Variable_name IN (
  'innodb_buffer_pool_size',
  'innodb_log_buffer_size',
  'innodb_additional_mem_pool_size',
  'key_buffer_size',
  'query_cache_size',
  'tmp_table_size',
  'max_heap_table_size',
  'max_connections',
  'thread_stack',
  'thread_cache_size',
  'sort_buffer_size',
  'read_buffer_size',
  'read_rnd_buffer_size',
  'join_buffer_size',
  'binlog_cache_size',
  'net_buffer_length',
  'table_open_cache',
  'performance_schema',
  'open_files_limit'
);" 2>&1

echo
echo '=== 6. PERFORMANCE_SCHEMA MEMORY USAGE (top 20) ==='
mysql -e "SELECT EVENT_NAME, ROUND(CURRENT_NUMBER_OF_BYTES_USED/1024/1024, 2) AS mb_used FROM performance_schema.memory_summary_global_by_event_name WHERE CURRENT_NUMBER_OF_BYTES_USED > 1048576 ORDER BY CURRENT_NUMBER_OF_BYTES_USED DESC LIMIT 20;" 2>&1

echo
echo '=== 7. INNODB BUFFER POOL STATS ==='
mysql -e "SHOW STATUS WHERE Variable_name IN (
  'Innodb_buffer_pool_pages_total',
  'Innodb_buffer_pool_pages_data',
  'Innodb_buffer_pool_pages_free',
  'Innodb_buffer_pool_bytes_data',
  'Innodb_buffer_pool_read_requests',
  'Innodb_buffer_pool_reads'
);" 2>&1

echo
echo '=== 8. THREAD / CONNECTION STATS ==='
mysql -e "SHOW STATUS WHERE Variable_name IN (
  'Threads_connected',
  'Threads_running',
  'Threads_created',
  'Max_used_connections',
  'Connections',
  'Open_tables',
  'Open_files'
);" 2>&1

echo
echo '=== 9. INNODB STATUS SUMMARY ==='
mysql -e "SHOW ENGINE INNODB STATUS\G" 2>/dev/null | grep -E 'Buffer pool size|Free buffers|Database pages|Modified db pages|Total memory|Dictionary memory' | head -20

echo
echo '=== 10. MYSQLTUNER-STYLE ESTIMATE ==='
mysql -BN -e "
SELECT 
  ROUND(
    @@innodb_buffer_pool_size/1048576 +
    @@key_buffer_size/1048576 +
    @@query_cache_size/1048576 +
    @@tmp_table_size/1048576 +
    (
      @@read_buffer_size +
      @@read_rnd_buffer_size +
      @@sort_buffer_size +
      @@join_buffer_size +
      @@binlog_cache_size +
      @@thread_stack +
      @@net_buffer_length
    ) * @@max_connections / 1048576,
    1
  ) AS theoretical_max_mb,
  ROUND(
    @@innodb_buffer_pool_size/1048576 +
    @@key_buffer_size/1048576 +
    @@query_cache_size/1048576 +
    @@tmp_table_size/1048576,
    1
  ) AS base_global_mb,
  ROUND(
    (
      @@read_buffer_size +
      @@read_rnd_buffer_size +
      @@sort_buffer_size +
      @@join_buffer_size +
      @@binlog_cache_size +
      @@thread_stack +
      @@net_buffer_length
    ) / 1024,
    1
  ) AS per_connection_kb,
  @@max_connections AS max_conn;
" 2>&1

echo
echo '=== DONE ==='
