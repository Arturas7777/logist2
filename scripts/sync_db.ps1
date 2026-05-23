# Sync production database to local PostgreSQL
# Usage: .\scripts\sync_db.ps1
#
# Creates a compressed dump on the server, downloads it, and restores locally.
# Local database is fully replaced with server data.
#
# Credentials are resolved from:
#   1) Environment: $env:LOCAL_DB_PASSWORD / $env:REMOTE_DB_PASSWORD
#   2) .env in repo root: LOCAL_DB_PASSWORD / REMOTE_DB_PASSWORD
#   3) Fallback to DB_PASSWORD from .env
# Nothing is hardcoded in this file.

$SERVER = "root@176.118.198.78"
$REMOTE_DIR = "/var/www/www-root/data/www/logist2"
$DUMP_FILE = "logist2_dump.sql.gz"
$LOCAL_DB = "logist2_db"
$LOCAL_USER = "arturas"
$SSH_OPTS = @("-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3")

function Get-EnvValue {
    param([string]$Key)
    $envFile = Join-Path $PSScriptRoot '..\.env'
    if (-not (Test-Path $envFile)) { return $null }
    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*=\s*(.+?)\s*$'
    foreach ($line in Get-Content $envFile) {
        if ($line -match $pattern) {
            return $Matches[1].Trim('"').Trim("'")
        }
    }
    return $null
}

$LOCAL_PASSWORD = $env:LOCAL_DB_PASSWORD
if (-not $LOCAL_PASSWORD) { $LOCAL_PASSWORD = Get-EnvValue 'LOCAL_DB_PASSWORD' }
if (-not $LOCAL_PASSWORD) { $LOCAL_PASSWORD = Get-EnvValue 'DB_PASSWORD' }

$REMOTE_PASSWORD = $env:REMOTE_DB_PASSWORD
if (-not $REMOTE_PASSWORD) { $REMOTE_PASSWORD = Get-EnvValue 'REMOTE_DB_PASSWORD' }
if (-not $REMOTE_PASSWORD) { $REMOTE_PASSWORD = $LOCAL_PASSWORD }

if (-not $LOCAL_PASSWORD) {
    Write-Host "ERROR: local DB password not found." -ForegroundColor Red
    Write-Host "Set `$env:LOCAL_DB_PASSWORD or put LOCAL_DB_PASSWORD/DB_PASSWORD into .env" -ForegroundColor Red
    exit 1
}

$env:PGPASSWORD = $LOCAL_PASSWORD

Write-Host "=== DB SYNC ===" -ForegroundColor Cyan
Write-Host ""

# [1/3] Create dump on server (password injected via env var of the ssh session)
Write-Host "[1/3] Creating dump on server..." -ForegroundColor Yellow
$dumpCmd = "cd $REMOTE_DIR && PGPASSWORD='$REMOTE_PASSWORD' pg_dump -h localhost -U arturas -Fc --no-owner --no-acl $LOCAL_DB > /tmp/$DUMP_FILE && ls -lh /tmp/$DUMP_FILE | awk '{print `$5}'"
$size = ssh @SSH_OPTS $SERVER $dumpCmd
if ($LASTEXITCODE -ne 0) {
    Write-Host "      ERROR: pg_dump failed on server" -ForegroundColor Red
    exit 1
}
Write-Host "      Dump created ($size)" -ForegroundColor Green

# [2/3] Download dump
Write-Host "[2/3] Downloading..." -ForegroundColor Yellow
$localDump = "$env:TEMP\$DUMP_FILE"
scp @SSH_OPTS "${SERVER}:/tmp/$DUMP_FILE" $localDump
if ($LASTEXITCODE -ne 0) {
    Write-Host "      ERROR: Download failed" -ForegroundColor Red
    exit 1
}
Write-Host "      Downloaded to $localDump" -ForegroundColor Green

# Cleanup remote dump (non-blocking, best-effort with 15s timeout)
$cleanupJob = Start-Job -ScriptBlock {
    param($srv, $file, $opts)
    & ssh @opts $srv "rm -f /tmp/$file" 2>$null
} -ArgumentList $SERVER, $DUMP_FILE, $SSH_OPTS
$null = Wait-Job $cleanupJob -Timeout 15
if ($cleanupJob.State -eq 'Running') {
    Stop-Job $cleanupJob
    Write-Host "      Remote cleanup timed out (non-critical, skipped)" -ForegroundColor DarkYellow
}
Remove-Job $cleanupJob -Force

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
