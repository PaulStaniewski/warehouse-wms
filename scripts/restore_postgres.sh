#!/usr/bin/env sh
set -eu

if [ "${1:-}" = "" ]; then
  echo "Usage: scripts/restore_postgres.sh <backup.dump>" >&2
  exit 2
fi

BACKUP_FILE="$1"
TARGET_DB="${RESTORE_DATABASE:-warehouse_wms_restore_verify}"
DATABASE_USER="${POSTGRES_USER:-warehouse_wms}"
DATABASE_HOST="${POSTGRES_HOST:-postgres}"
DATABASE_PORT="${POSTGRES_PORT:-5432}"
CONFIRM="${CONFIRM_RESTORE:-}"

if [ ! -s "$BACKUP_FILE" ]; then
  echo "Restore failed: backup file does not exist or is empty: $BACKUP_FILE" >&2
  exit 1
fi

if [ "$CONFIRM" != "yes" ]; then
  echo "Set CONFIRM_RESTORE=yes to restore into database '$TARGET_DB'." >&2
  echo "The target database will be dropped and recreated." >&2
  exit 2
fi

dropdb --if-exists --host="$DATABASE_HOST" --port="$DATABASE_PORT" --username="$DATABASE_USER" "$TARGET_DB"
createdb --host="$DATABASE_HOST" --port="$DATABASE_PORT" --username="$DATABASE_USER" "$TARGET_DB"
pg_restore --clean --if-exists --no-owner --no-acl --host="$DATABASE_HOST" --port="$DATABASE_PORT" --username="$DATABASE_USER" --dbname="$TARGET_DB" "$BACKUP_FILE"

echo "Restore completed into database: $TARGET_DB"
