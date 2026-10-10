#!/bin/sh
set -eu

umask 077

ARCHIVE=${1:-}
: "${ARCHIVE:?Usage: verify_clinical_backup.sh /path/to/archive.tar.gz.enc}"
: "${BACKUP_PASSPHRASE_FILE:?BACKUP_PASSPHRASE_FILE must point to a protected passphrase file}"

if [ ! -f "$ARCHIVE" ] || [ ! -f "$ARCHIVE.sha256" ]; then
    echo "Backup archive or checksum sidecar is missing." >&2
    exit 2
fi
if [ ! -s "$BACKUP_PASSPHRASE_FILE" ]; then
    echo "Backup passphrase file is missing or empty." >&2
    exit 2
fi

ARCHIVE_DIR=$(CDPATH= cd -- "$(dirname -- "$ARCHIVE")" && pwd)
ARCHIVE_FILE=$(basename -- "$ARCHIVE")
(
    cd "$ARCHIVE_DIR"
    sha256sum -c "$ARCHIVE_FILE.sha256"
)

WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/emr2-verify.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT HUP INT TERM
PLAIN_ARCHIVE="$WORK_DIR/payload.tar.gz"
PAYLOAD_DIR="$WORK_DIR/payload"
mkdir -p "$PAYLOAD_DIR"

openssl enc -d -aes-256-cbc -pbkdf2 \
    -in "$ARCHIVE" \
    -out "$PLAIN_ARCHIVE" \
    -pass "file:$BACKUP_PASSPHRASE_FILE"

if tar -tzf "$PLAIN_ARCHIVE" | grep -E '(^/|(^|/)\.\.(/|$))' >/dev/null 2>&1; then
    echo "Unsafe path detected in backup archive." >&2
    exit 1
fi
tar -C "$PAYLOAD_DIR" -xzf "$PLAIN_ARCHIVE"

if [ ! -s "$PAYLOAD_DIR/mariadb.sql" ] || [ ! -s "$PAYLOAD_DIR/MANIFEST.sha256" ]; then
    echo "Backup does not contain a database dump or manifest." >&2
    exit 1
fi
(
    cd "$PAYLOAD_DIR"
    sha256sum -c MANIFEST.sha256
)

XRAY_COUNT=$(find "$PAYLOAD_DIR/storage/xrays" -type f 2>/dev/null | wc -l | tr -d ' ')
ECHO_COUNT=$(find "$PAYLOAD_DIR/storage/echo-videos" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "Backup verified: database dump, $XRAY_COUNT X-ray files, $ECHO_COUNT Echo files."
