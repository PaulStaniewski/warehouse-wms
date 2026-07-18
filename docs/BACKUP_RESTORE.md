# PostgreSQL Backup And Restore

Backups use PostgreSQL custom format dumps:

```text
pg_dump --format=custom
```

This format is compressed, supports `pg_restore`, and is suitable for restore verification.

## Backup

Linux/container:

```sh
scripts/backup_postgres.sh
```

Windows PowerShell:

```powershell
.\scripts\backup_postgres.ps1
```

Configuration is read from environment variables:

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `PGPASSWORD`
- `BACKUP_DIR`
- `BACKUP_ENV_LABEL`

The script creates a timestamped `.dump` file and fails if the file is missing or empty.

## Restore

Restore is destructive for the target database and requires explicit confirmation.

Linux/container:

```sh
CONFIRM_RESTORE=yes RESTORE_DATABASE=warehouse_wms_restore_verify scripts/restore_postgres.sh backups/postgres/local_warehouse_wms_YYYYMMDDTHHMMSSZ.dump
```

Windows PowerShell:

```powershell
$env:CONFIRM_RESTORE = "yes"
$env:RESTORE_DATABASE = "warehouse_wms_restore_verify"
.\scripts\restore_postgres.ps1 .\backups\postgres\local_warehouse_wms_YYYYMMDDTHHMMSSZ.dump
```

Never restore over production while application writes continue. Put the app in maintenance mode or restore into a disposable verification database first.

## Restore Verification Drill

Recommended drill:

1. Create or identify deterministic rows in a non-production database.
2. Run the backup script.
3. Verify the dump file exists and has non-zero size.
4. Restore into `warehouse_wms_restore_verify`.
5. Run integrity checks against representative tables.
6. Run Django migration checks against the restored database.
7. Drop the disposable database.

Example integrity query:

```sh
psql "$RESTORE_DATABASE" -c "select count(*) from warehouse_branch;"
```

## Retention

Recommended starting policy:

- daily backups for short-term operational recovery,
- weekly backups for longer retention,
- copy backups off the Docker host,
- encrypt backups at rest using the deployment/storage provider.

Exact retention is a business decision. Database credentials must not be embedded in backup filenames.

## RPO And RTO

RPO is the acceptable data-loss window. Nightly backups imply up to roughly one day of possible data loss. Smaller RPO needs more frequent backups or WAL/PITR.

RTO is the acceptable time to restore service. Measure it through the restore drill before promising an operational target.
