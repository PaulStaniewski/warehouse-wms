param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile
)

$ErrorActionPreference = "Stop"

$targetDb = if ($env:RESTORE_DATABASE) { $env:RESTORE_DATABASE } else { "warehouse_wms_restore_verify" }
$databaseUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "warehouse_wms" }
$databaseHost = if ($env:POSTGRES_HOST) { $env:POSTGRES_HOST } else { "localhost" }
$databasePort = if ($env:POSTGRES_PORT) { $env:POSTGRES_PORT } else { "5432" }

if (!(Test-Path -LiteralPath $BackupFile) -or ((Get-Item -LiteralPath $BackupFile).Length -le 0)) {
    throw "Restore failed: backup file does not exist or is empty: $BackupFile"
}

if ($env:CONFIRM_RESTORE -ne "yes") {
    Write-Error "Set CONFIRM_RESTORE=yes to restore into database '$targetDb'. The target database will be dropped and recreated."
}

dropdb --if-exists --host=$databaseHost --port=$databasePort --username=$databaseUser $targetDb
createdb --host=$databaseHost --port=$databasePort --username=$databaseUser $targetDb
pg_restore --clean --if-exists --no-owner --no-acl --host=$databaseHost --port=$databasePort --username=$databaseUser --dbname=$targetDb $BackupFile

Write-Host "Restore completed into database: $targetDb"
