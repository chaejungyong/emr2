#!/bin/sh
set -eu

umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
: "${BACKUP_DESTINATION:?BACKUP_DESTINATION must point to an off-project backup directory}"
: "${BACKUP_PASSPHRASE_FILE:?BACKUP_PASSPHRASE_FILE must point to a protected passphrase file}"

case "$BACKUP_DESTINATION" in
    "$PROJECT_ROOT"|"$PROJECT_ROOT"/*)
        echo "BACKUP_DESTINATION must be outside the project directory." >&2
        exit 2
        ;;
esac

if [ ! -s "$BACKUP_PASSPHRASE_FILE" ]; then
    echo "Backup passphrase file is missing or empty." >&2
    exit 2
fi

for command_name in docker openssl tar sha256sum find; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "Required command not found: $command_name" >&2
        exit 2
    }
done

mkdir -p "$BACKUP_DESTINATION"
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/emr2-backup.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT HUP INT TERM
PAYLOAD_DIR="$WORK_DIR/payload"
mkdir -p "$PAYLOAD_DIR/storage"

docker compose --project-directory "$PROJECT_ROOT" -f "$PROJECT_ROOT/docker-compose.yml" \
    exec -T db sh -c \
    'exec mariadb-dump --single-transaction --quick --routines --triggers --events --hex-blob --databases "$MYSQL_DATABASE" -uroot -p"$MYSQL_ROOT_PASSWORD"' \
    > "$PAYLOAD_DIR/mariadb.sql"

if [ ! -s "$PAYLOAD_DIR/mariadb.sql" ]; then
    echo "MariaDB dump is empty." >&2
    exit 1
fi

for relative_path in storage/xrays storage/echo-videos storage/knowledge/inbox; do
    if [ -d "$PROJECT_ROOT/$relative_path" ]; then
        parent=$(dirname "$relative_path")
        mkdir -p "$PAYLOAD_DIR/$parent"
        cp -a "$PROJECT_ROOT/$relative_path" "$PAYLOAD_DIR/$parent/"
    else
        mkdir -p "$PAYLOAD_DIR/$relative_path"
    fi
done
cp "$PROJECT_ROOT/.env" "$PAYLOAD_DIR/.env"

(
    cd "$PAYLOAD_DIR"
    find . -type f ! -name MANIFEST.sha256 -exec sha256sum "{}" \; | LC_ALL=C sort > MANIFEST.sha256
)

STAMP=$(date '+%Y%m%dT%H%M%S')
HOST_TAG=$(hostname | tr -c 'A-Za-z0-9._-' '_')
ARCHIVE_NAME="emr2-clinical-${HOST_TAG}-${STAMP}.tar.gz.enc"
PLAIN_ARCHIVE="$WORK_DIR/payload.tar.gz"
ENCRYPTED_ARCHIVE="$WORK_DIR/$ARCHIVE_NAME"

tar -C "$PAYLOAD_DIR" -czf "$PLAIN_ARCHIVE" .
openssl enc -aes-256-cbc -pbkdf2 -salt \
    -in "$PLAIN_ARCHIVE" \
    -out "$ENCRYPTED_ARCHIVE" \
    -pass "file:$BACKUP_PASSPHRASE_FILE"

(
    cd "$WORK_DIR"
    sha256sum "$ARCHIVE_NAME" > "$ARCHIVE_NAME.sha256"
)

mv "$ENCRYPTED_ARCHIVE" "$BACKUP_DESTINATION/$ARCHIVE_NAME"
mv "$WORK_DIR/$ARCHIVE_NAME.sha256" "$BACKUP_DESTINATION/$ARCHIVE_NAME.sha256"

BACKUP_PASSPHRASE_FILE="$BACKUP_PASSPHRASE_FILE" \
    "$SCRIPT_DIR/verify_clinical_backup.sh" "$BACKUP_DESTINATION/$ARCHIVE_NAME"

echo "Encrypted clinical backup created: $BACKUP_DESTINATION/$ARCHIVE_NAME"
