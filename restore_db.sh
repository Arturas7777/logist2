#!/bin/bash
cd /var/www/www-root/data/www/logist2
export PGPASSWORD='7154032tut'

echo "=== Dropping all tables in logist2_db ==="

# Generate and execute DROP commands for all tables
psql -U arturas -h localhost -d logist2_db -t -c "SELECT 'DROP TABLE IF EXISTS \"' || tablename || '\" CASCADE;' FROM pg_tables WHERE schemaname = 'public';" | psql -U arturas -h localhost -d logist2_db

echo "=== Restoring database from dump ==="
psql -U arturas -h localhost -d logist2_db < logist2_plain.sql

echo "=== Database restore complete ==="
