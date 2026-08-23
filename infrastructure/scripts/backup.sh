#!/usr/bin/env bash
#
# Nightly backup.
#
#   ./backup.sh                    take a backup
#   ./backup.sh --verify           take one, then restore it to a scratch
#                                  database and compare row counts
#
# The --verify path matters more than the backup itself. An untested backup is a
# hypothesis; this makes it a fact. Run it at least monthly, and after any schema
# change.
#
# Required environment:
#   DATABASE_URL          postgres://... (the sync form, not +asyncpg)
#   BACKUP_S3_BUCKET      destination bucket
#   BACKUP_RETENTION_DAYS defaults to 30
#   ALERT_WEBHOOK         optional; POSTed on failure

set -euo pipefail

RETENTION="${BACKUP_RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="$(mktemp -d)"
DUMP="${WORKDIR}/resumeiq-${STAMP}.sql.gz"
trap 'rm -rf "${WORKDIR}"' EXIT

fail() {
  echo "BACKUP FAILED: $1" >&2
  if [[ -n "${ALERT_WEBHOOK:-}" ]]; then
    # Deliberately terse: the alert must not carry connection strings.
    curl -fsS -X POST "${ALERT_WEBHOOK}" \
      -H 'Content-Type: application/json' \
      -d "{\"text\":\"ResumeIQ backup failed at ${STAMP}: $1\"}" || true
  fi
  exit 1
}

[[ -n "${DATABASE_URL:-}" ]] || fail "DATABASE_URL is not set"

echo "==> dumping"
pg_dump --no-owner --no-privileges --format=plain "${DATABASE_URL}" \
  | gzip -9 > "${DUMP}" || fail "pg_dump returned non-zero"

SIZE=$(stat -c%s "${DUMP}" 2>/dev/null || stat -f%z "${DUMP}")
# A dump of an initialised database is never this small. Catches the case where
# pg_dump succeeds against an empty or wrong database.
[[ "${SIZE}" -gt 10240 ]] || fail "dump is only ${SIZE} bytes — refusing to upload"
echo "    ${DUMP} (${SIZE} bytes)"

if [[ "${1:-}" == "--verify" ]]; then
  echo "==> restore verification"
  SCRATCH="resumeiq_restore_check_${STAMP}"
  ADMIN_URL="${DATABASE_URL%/*}/postgres"

  psql "${ADMIN_URL}" -c "CREATE DATABASE ${SCRATCH};" >/dev/null \
    || fail "could not create scratch database"

  # shellcheck disable=SC2064
  trap "psql '${ADMIN_URL}' -c 'DROP DATABASE IF EXISTS ${SCRATCH};' >/dev/null 2>&1; rm -rf '${WORKDIR}'" EXIT

  gunzip -c "${DUMP}" | psql "${DATABASE_URL%/*}/${SCRATCH}" >/dev/null 2>&1 \
    || fail "restore into scratch database failed"

  for table in organizations users candidates screening_batches requirement_evidence audit_logs; do
    SRC=$(psql -tAc "SELECT count(*) FROM ${table};" "${DATABASE_URL}")
    DST=$(psql -tAc "SELECT count(*) FROM ${table};" "${DATABASE_URL%/*}/${SCRATCH}")
    [[ "${SRC}" == "${DST}" ]] || fail "row count mismatch on ${table}: ${SRC} vs ${DST}"
    echo "    ${table}: ${SRC} rows match"
  done
  echo "==> restore verified"
fi

if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then
  echo "==> uploading"
  aws s3 cp "${DUMP}" "s3://${BACKUP_S3_BUCKET}/postgres/$(basename "${DUMP}")" \
    --sse AES256 || fail "upload to s3 failed"

  CUTOFF=$(date -u -d "${RETENTION} days ago" +%Y%m%d 2>/dev/null \
           || date -u -v-"${RETENTION}"d +%Y%m%d)
  aws s3 ls "s3://${BACKUP_S3_BUCKET}/postgres/" | while read -r _ _ _ key; do
    KEYDATE=$(echo "${key}" | grep -oE '[0-9]{8}T' | tr -d 'T' || true)
    if [[ -n "${KEYDATE}" && "${KEYDATE}" < "${CUTOFF}" ]]; then
      aws s3 rm "s3://${BACKUP_S3_BUCKET}/postgres/${key}"
    fi
  done
else
  echo "==> BACKUP_S3_BUCKET unset; dump not copied off this machine"
  echo "    A backup that lives only on the machine it protects is not a backup."
fi

echo "==> done"
