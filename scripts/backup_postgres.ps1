$ErrorActionPreference = "Stop"

$backupDir = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { "backups\postgres" }
$envLabel = if ($env:BACKUP_ENV_LABEL) { $env:BACKUP_ENV_LABEL } else { "local" }
$databaseName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "warehouse_wms" }
$databaseUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "warehouse_wms" }
$databaseHost = if ($env:POSTGRES_HOST) { $env:POSTGRES_HOST } else { "localhost" }
$databasePort = if ($env:POSTGRES_PORT) { $env:POSTGRES_PORT } else { "5432" }

New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$outputFile = Join-Path $backupDir "$($envLabel)_$($databaseName)_$timestamp.dump"

pg_dump --format=custom --no-owner --no-acl --host=$databaseHost --port=$databasePort --username=$databaseUser --dbname=$databaseName --file=$outputFile

if (!(Test-Path -LiteralPath $outputFile) -or ((Get-Item -LiteralPath $outputFile).Length -le 0)) {
    throw "Backup failed: $outputFile was not created or is empty."
}

Write-Host "Backup created: $outputFile"
