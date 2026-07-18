#!/usr/bin/env sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-backups/postgres}"
ENV_LABEL="${BACKUP_ENV_LABEL:-local}"
DATABASE_NAME="${POSTGRES_DB:-warehouse_wms}"
DATABASE_USER="${POSTGRES_USER:-warehouse_wms}"
DATABASE_HOST="${POSTGRES_HOST:-postgres}"
DATABASE_PORT="${POSTGRES_PORT:-5432}"

mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_FILE="$BACKUP_DIR/${ENV_LABEL}_${DATABASE_NAME}_${TIMESTAMP}.dump"

pg_dump \
  --format=custom \
  --no-owner \
  --no-acl \
  --host="$DATABASE_HOST" \
  --port="$DATABASE_PORT" \
  --username="$DATABASE_USER" \
  --dbname="$DATABASE_NAME" \
  --file="$OUTPUT_FILE"

if [ ! -s "$OUTPUT_FILE" ]; then
  echo "Backup failed: $OUTPUT_FILE was not created or is empty." >&2
  exit 1
fi

echo "Backup created: $OUTPUT_FILE"
