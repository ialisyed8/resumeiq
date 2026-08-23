#!/usr/bin/env bash
#
# Restore from a backup.
#
# Requires an explicit confirmation phrase because this destroys the target
# database. An untested restore is a hypothesis, so run this against a scratch
# database monthly — see docs/disaster-recovery.md.
#
#   ./restore.sh /var/backups/resumeiq/resumeiq-20260815T020000Z.sql.gz
set -Eeuo pipefail

ARCHIVE="${1:?usage: restore.sh <backup.sql.gz> [target-database-url]}"
TARGET="${2:-${RESTORE_TARGET_URL:-}}"
: "${TARGET:?set a target: restore.sh <archive> <database-url>}"

PG_URL="${TARGET/+asyncpg/}"
PG_URL="${PG_URL/+psycopg/}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

[[ -f "${ARCHIVE}" ]] || { log "no such archive: ${ARCHIVE}"; exit 1; }
gzip -t "${ARCHIVE}"

# Guard against restoring over production by muscle memory.
echo "This will DROP AND REPLACE all data in:"
echo "  ${PG_URL%%\?*}"
echo
read -r -p "Type RESTORE to continue: " CONFIRM
[[ "${CONFIRM}" == "RESTORE" ]] || { log "aborted"; exit 1; }

log "restoring ${ARCHIVE}"
gunzip -c "${ARCHIVE}" | psql "${PG_URL}" -v ON_ERROR_STOP=1

log "verifying"
psql "${PG_URL}" -tAc "SELECT count(*) FROM candidates;"        >/dev/null
psql "${PG_URL}" -tAc "SELECT count(*) FROM audit_logs;"        >/dev/null
psql "${PG_URL}" -tAc "SELECT count(*) FROM requirement_evidence;" >/dev/null

log "restore complete. Run 'alembic current' to confirm the schema version."
