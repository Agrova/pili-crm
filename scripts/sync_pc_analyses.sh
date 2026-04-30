#!/usr/bin/env bash
# sync_pc_analyses.sh — Pull PC analysis results to Mac (ADR-011 Addendum 2, G2)
#
# Pulls records where analyzer_version LIKE '%@pc' from the PC PostgreSQL
# to the Mac PostgreSQL. Covers BOTH tables:
#   - analysis_chat_analysis
#   - analysis_extracted_identity
#
# Usage:
#   ./scripts/sync_pc_analyses.sh
#
# Environment (override as needed):
#   PC_HOST      — PC IP on local network (default: 192.168.1.5)
#   PC_PORT      — PC Postgres port       (default: 5432)
#   PC_DB        — PC database name       (default: pili_crm)
#   PC_USER      — PC Postgres user       (default: pili)
#   PC_PASSWORD  — PC Postgres password   (default: pili)
#   MAC_DB       — Mac database name      (default: pili_crm)
#   MAC_USER     — Mac Postgres user      (default: pili)
#   MAC_CONTAINER— Mac Docker container   (default: pili-crm-postgres-1)
#
# Idempotency:
#   The UNIQUE constraint on (chat_id, analyzer_version) in analysis_chat_analysis
#   and (id) PK in analysis_extracted_identity ensure ON CONFLICT DO NOTHING
#   makes any re-run a no-op for already-synced records.
#
# Requires: psql client accessible on Mac (via docker exec or system psql).
#           PC PostgreSQL must be reachable from Mac on PC_HOST:PC_PORT.

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

PC_HOST="${PC_HOST:-192.168.1.5}"
PC_PORT="${PC_PORT:-5432}"
PC_DB="${PC_DB:-pili_crm}"
PC_USER="${PC_USER:-pili}"
PC_PASSWORD="${PC_PASSWORD:-pili}"
MAC_DB="${MAC_DB:-pili_crm}"
MAC_USER="${MAC_USER:-pili}"
MAC_CONTAINER="${MAC_CONTAINER:-pili-crm-postgres-1}"

WORKER_FILTER="%@pc"
TMPDIR_SYNC="$(mktemp -d /tmp/sync_pc_analyses_XXXXXX)"
ANALYSES_SQL="${TMPDIR_SYNC}/analyses.sql"
IDENTITY_SQL="${TMPDIR_SYNC}/identity.sql"

# ── Helpers ──────────────────────────────────────────────────────────────────

mac_psql() {
    docker exec -i "${MAC_CONTAINER}" psql -U "${MAC_USER}" -d "${MAC_DB}" "$@"
}

pc_psql() {
    PGPASSWORD="${PC_PASSWORD}" psql \
        -h "${PC_HOST}" -p "${PC_PORT}" \
        -U "${PC_USER}" -d "${PC_DB}" \
        "$@"
}

cleanup() {
    rm -rf "${TMPDIR_SYNC}"
}
trap cleanup EXIT

# ── Preflight: check PC reachability ────────────────────────────────────────

echo "[sync] Starting PC → Mac analysis sync ($(date -u '+%Y-%m-%dT%H:%M:%SZ'))"
echo "[sync] PC: ${PC_USER}@${PC_HOST}:${PC_PORT}/${PC_DB}"
echo "[sync] Mac container: ${MAC_CONTAINER}/${MAC_DB}"
echo "[sync] Worker filter: analyzer_version LIKE '${WORKER_FILTER}'"

if ! PGPASSWORD="${PC_PASSWORD}" psql \
        -h "${PC_HOST}" -p "${PC_PORT}" \
        -U "${PC_USER}" -d "${PC_DB}" \
        -c "SELECT 1" -q --no-psqlrc > /dev/null 2>&1; then
    echo "[sync] ERROR: Cannot connect to PC at ${PC_HOST}:${PC_PORT}. Is PC online?" >&2
    exit 1
fi
echo "[sync] PC reachable ✓"

# ── Count before sync ────────────────────────────────────────────────────────

echo ""
echo "[sync] === analysis_chat_analysis ==="

PC_ANALYSES_COUNT=$(pc_psql -t -c \
    "SELECT COUNT(*) FROM analysis_chat_analysis WHERE analyzer_version LIKE '${WORKER_FILTER}';" \
    2>/dev/null | tr -d ' ')
MAC_ANALYSES_BEFORE=$(mac_psql -t -c \
    "SELECT COUNT(*) FROM analysis_chat_analysis WHERE analyzer_version LIKE '${WORKER_FILTER}';" \
    2>/dev/null | tr -d ' ')

echo "[sync] PC has:  ${PC_ANALYSES_COUNT} analysis_chat_analysis rows with @pc"
echo "[sync] Mac has: ${MAC_ANALYSES_BEFORE} (before sync)"

echo ""
echo "[sync] === analysis_extracted_identity ==="

PC_IDENTITY_COUNT=$(pc_psql -t -c \
    "SELECT COUNT(*) FROM analysis_extracted_identity WHERE analyzer_version LIKE '${WORKER_FILTER}';" \
    2>/dev/null | tr -d ' ')
MAC_IDENTITY_BEFORE=$(mac_psql -t -c \
    "SELECT COUNT(*) FROM analysis_extracted_identity WHERE analyzer_version LIKE '${WORKER_FILTER}';" \
    2>/dev/null | tr -d ' ')

echo "[sync] PC has:  ${PC_IDENTITY_COUNT} analysis_extracted_identity rows with @pc"
echo "[sync] Mac has: ${MAC_IDENTITY_BEFORE} (before sync)"

# ── Export from PC ───────────────────────────────────────────────────────────

echo ""
echo "[sync] Exporting analysis_chat_analysis from PC..."

# Export analysis_chat_analysis with column-level INSERT for ON CONFLICT support.
# We use COPY TO STDOUT and reconstruct INSERTs, or use pg_dump --data-only
# with --column-inserts --where. The --where flag filters rows.
PGPASSWORD="${PC_PASSWORD}" pg_dump \
    -h "${PC_HOST}" -p "${PC_PORT}" \
    -U "${PC_USER}" -d "${PC_DB}" \
    --data-only \
    --column-inserts \
    --table=analysis_chat_analysis \
    --where="analyzer_version LIKE '${WORKER_FILTER}'" \
    > "${ANALYSES_SQL}"

echo "[sync] Exported $(wc -l < "${ANALYSES_SQL}") lines for analysis_chat_analysis"

echo "[sync] Exporting analysis_extracted_identity from PC..."

PGPASSWORD="${PC_PASSWORD}" pg_dump \
    -h "${PC_HOST}" -p "${PC_PORT}" \
    -U "${PC_USER}" -d "${PC_DB}" \
    --data-only \
    --column-inserts \
    --table=analysis_extracted_identity \
    --where="analyzer_version LIKE '${WORKER_FILTER}'" \
    > "${IDENTITY_SQL}"

echo "[sync] Exported $(wc -l < "${IDENTITY_SQL}") lines for analysis_extracted_identity"

# ── Transform: INSERT → INSERT ON CONFLICT DO NOTHING ───────────────────────

# pg_dump --column-inserts generates plain INSERT INTO ... VALUES (...);
# We rewrite them to INSERT INTO ... ON CONFLICT DO NOTHING; for idempotency.

echo ""
echo "[sync] Patching INSERTs → INSERT ... ON CONFLICT DO NOTHING ..."

sed -i 's/^INSERT INTO \(.*\) VALUES (\(.*\));$/INSERT INTO \1 VALUES (\2) ON CONFLICT DO NOTHING;/' \
    "${ANALYSES_SQL}"

sed -i 's/^INSERT INTO \(.*\) VALUES (\(.*\));$/INSERT INTO \1 VALUES (\2) ON CONFLICT DO NOTHING;/' \
    "${IDENTITY_SQL}"

# ── Import into Mac ──────────────────────────────────────────────────────────

echo "[sync] Importing analysis_chat_analysis into Mac..."

# Pipe the SQL file into Mac's Postgres via docker exec.
# Suppress the "INSERT 0 1" success lines; show errors only.
ANALYSES_IMPORTED=$(docker exec -i "${MAC_CONTAINER}" \
    psql -U "${MAC_USER}" -d "${MAC_DB}" \
    --set ON_ERROR_STOP=off \
    < "${ANALYSES_SQL}" 2>&1 \
    | grep -c "^INSERT" || true)

echo "[sync] Importing analysis_extracted_identity into Mac..."

IDENTITY_IMPORTED=$(docker exec -i "${MAC_CONTAINER}" \
    psql -U "${MAC_USER}" -d "${MAC_DB}" \
    --set ON_ERROR_STOP=off \
    < "${IDENTITY_SQL}" 2>&1 \
    | grep -c "^INSERT" || true)

# ── Count after sync ─────────────────────────────────────────────────────────

MAC_ANALYSES_AFTER=$(mac_psql -t -c \
    "SELECT COUNT(*) FROM analysis_chat_analysis WHERE analyzer_version LIKE '${WORKER_FILTER}';" \
    2>/dev/null | tr -d ' ')

MAC_IDENTITY_AFTER=$(mac_psql -t -c \
    "SELECT COUNT(*) FROM analysis_extracted_identity WHERE analyzer_version LIKE '${WORKER_FILTER}';" \
    2>/dev/null | tr -d ' ')

ANALYSES_NEW=$((MAC_ANALYSES_AFTER - MAC_ANALYSES_BEFORE))
IDENTITY_NEW=$((MAC_IDENTITY_AFTER - MAC_IDENTITY_BEFORE))

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "[sync] ════════ Summary ════════"
echo "[sync] analysis_chat_analysis:"
echo "[sync]   PC total @pc:     ${PC_ANALYSES_COUNT}"
echo "[sync]   Mac before:       ${MAC_ANALYSES_BEFORE}"
echo "[sync]   Mac after:        ${MAC_ANALYSES_AFTER}"
echo "[sync]   New rows added:   ${ANALYSES_NEW}"

echo "[sync] analysis_extracted_identity:"
echo "[sync]   PC total @pc:     ${PC_IDENTITY_COUNT}"
echo "[sync]   Mac before:       ${MAC_IDENTITY_BEFORE}"
echo "[sync]   Mac after:        ${MAC_IDENTITY_AFTER}"
echo "[sync]   New rows added:   ${IDENTITY_NEW}"

if [[ "${ANALYSES_NEW}" -eq 0 && "${IDENTITY_NEW}" -eq 0 ]]; then
    echo "[sync] ℹ️  No new rows (idempotent run — all PC records already on Mac)"
else
    echo "[sync] ✅ Sync complete — ${ANALYSES_NEW} analyses + ${IDENTITY_NEW} identity rows added"
fi

# ── Show distribution on Mac ─────────────────────────────────────────────────

echo ""
echo "[sync] analyzer_version distribution on Mac (all versions):"
mac_psql -c \
    "SELECT analyzer_version, COUNT(*) AS rows FROM analysis_chat_analysis GROUP BY analyzer_version ORDER BY analyzer_version;"

echo "[sync] Done ($(date -u '+%Y-%m-%dT%H:%M:%SZ'))"
