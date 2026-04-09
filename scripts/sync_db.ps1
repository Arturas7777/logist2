# Sync production database to local PostgreSQL
# Usage: .\scripts\sync_db.ps1
#
# Creates a compressed dump on the server, downloads it, and restores locally.
# Local database is fully replaced with server data.

$SERVER = "root@176.118.198.78"
$REMOTE_DIR = "/var/www/www-root/data/www/logist2"
$DUMP_FILE = "logist2_dump.sql.gz"
$LOCAL_DB = "logist2_db"
$LOCAL_USER = "arturas"
$LOCAL_PASSWORD = "7154032tut"

$env:PGPASSWORD = $LOCAL_PASSWORD

Write-Host "=== DB SYNC ===" -ForegroundColor Cyan
Write-Host ""

# [1/3] Create dump on server
Write-Host "[1/3] Creating dump on server..." -ForegroundColor Yellow
$dumpCmd = "cd $REMOTE_DIR && PGPASSWORD=7154032tut pg_dump -h localhost -U arturas -Fc --no-owner --no-acl $LOCAL_DB > /tmp/$DUMP_FILE && ls -lh /tmp/$DUMP_FILE | awk '{print `$5}'"
$size = ssh -o ConnectTimeout=30 -o ServerAliveInterval=10 -o ServerAliveCountMax=6 $SERVER $dumpCmd
if ($LASTEXITCODE -ne 0) {
    Write-Host "      ERROR: pg_dump failed on server" -ForegroundColor Red
    exit 1
}
Write-Host "      Dump created ($size)" -ForegroundColor Green

# [2/3] Download dump
Write-Host "[2/3] Downloading..." -ForegroundColor Yellow
$localDump = "$env:TEMP\$DUMP_FILE"
scp -o ConnectTimeout=30 "${SERVER}:/tmp/$DUMP_FILE" $localDump
if ($LASTEXITCODE -ne 0) {
    Write-Host "      ERROR: Download failed" -ForegroundColor Red
    exit 1
}
Write-Host "      Downloaded to $localDump" -ForegroundColor Green

# Cleanup remote dump
ssh -o ConnectTimeout=30 $SERVER "rm -f /tmp/$DUMP_FILE" 2>$null

# [3/3] Restore locally
Write-Host "[3/3] Restoring to local database..." -ForegroundColor Yellow

# Drop and recreate database
psql -h localhost -U $LOCAL_USER -d postgres -q -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$LOCAL_DB' AND pid <> pg_backend_pid();" >$null 2>$null
psql -h localhost -U $LOCAL_USER -d postgres -q -c "DROP DATABASE IF EXISTS $LOCAL_DB;" >$null 2>$null
psql -h localhost -U $LOCAL_USER -d postgres -q -c "CREATE DATABASE $LOCAL_DB OWNER $LOCAL_USER;" >$null 2>$null

pg_restore -h localhost -U $LOCAL_USER -d $LOCAL_DB --no-owner --no-acl $localDump 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "      WARNING: pg_restore reported warnings (this is usually OK)" -ForegroundColor Yellow
} else {
    Write-Host "      Restored successfully" -ForegroundColor Green
}

# Cleanup local dump
Remove-Item $localDump -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== DB SYNC COMPLETE ===" -ForegroundColor Cyan
Write-Host ""
