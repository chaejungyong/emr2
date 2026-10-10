#!/bin/sh
set -eu

umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ARCHIVE=${1:-}
: "${ARCHIVE:?Usage: restore_drill.sh /path/to/archive.tar.gz.enc}"
: "${BACKUP_PASSPHRASE_FILE:?BACKUP_PASSPHRASE_FILE must point to a protected passphrase file}"

BACKUP_PASSPHRASE_FILE="$BACKUP_PASSPHRASE_FILE" \
    "$SCRIPT_DIR/verify_clinical_backup.sh" "$ARCHIVE"

WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/emr2-restore-drill.XXXXXX")
CONTAINER_NAME="emr2-restore-drill-$(date '+%Y%m%d%H%M%S')-$$"
DRILL_PASSWORD=$(openssl rand -hex 24)
cleanup() {
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT HUP INT TERM

openssl enc -d -aes-256-cbc -pbkdf2 \
    -in "$ARCHIVE" \
    -out "$WORK_DIR/payload.tar.gz" \
    -pass "file:$BACKUP_PASSPHRASE_FILE"
mkdir -p "$WORK_DIR/payload"
tar -C "$WORK_DIR/payload" -xzf "$WORK_DIR/payload.tar.gz"

docker run -d --name "$CONTAINER_NAME" \
    -e "MARIADB_ROOT_PASSWORD=$DRILL_PASSWORD" \
    mariadb:10.6 >/dev/null

ready=0
attempt=0
while [ "$attempt" -lt 60 ]; do
    if docker exec "$CONTAINER_NAME" healthcheck.sh --connect --innodb_initialized >/dev/null 2>&1; then
        ready=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 2
done
if [ "$ready" -ne 1 ]; then
    echo "Restore drill MariaDB did not become ready." >&2
    exit 1
fi

docker exec -i "$CONTAINER_NAME" \
    mariadb -uroot -p"$DRILL_PASSWORD" < "$WORK_DIR/payload/mariadb.sql"

TABLE_COUNT=$(docker exec "$CONTAINER_NAME" mariadb -N -uroot -p"$DRILL_PASSWORD" -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys');")
if [ "${TABLE_COUNT:-0}" -lt 1 ]; then
    echo "Restore drill completed without application tables." >&2
    exit 1
fi

echo "Restore drill passed in an isolated MariaDB container: $TABLE_COUNT application tables restored."
