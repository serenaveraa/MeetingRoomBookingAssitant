#!/usr/bin/env bash
# Verify RDS availability, SSL connectivity, and security group scoping.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

load_config
require_aws
require_cmd psql

if ! rds_instance_exists; then
  echo "error: RDS instance ${DB_INSTANCE_IDENTIFIER} not found in ${AWS_REGION}" >&2
  exit 1
fi

STATUS="$(get_rds_status)"
ENDPOINT="$(get_rds_endpoint)"
echo "instance: ${DB_INSTANCE_IDENTIFIER}"
echo "status:   ${STATUS}"
echo "endpoint: ${ENDPOINT}"

if [[ "${STATUS}" != "available" ]]; then
  echo "error: instance is not available (status=${STATUS})" >&2
  exit 1
fi

DATABASE_URL="$(fetch_database_url_from_secret)"
PSQL_URL="$(to_psql_url "${DATABASE_URL}")"

echo ""
echo "=== connectivity + SSL (allowed source — this host) ==="
psql "${PSQL_URL}" -c "SELECT version();"
psql "${PSQL_URL}" -c "\\conninfo"

echo ""
echo "=== schema smoke check (rooms) ==="
psql "${PSQL_URL}" -c "SELECT id, name FROM rooms;" || {
  echo "note: rooms table empty or missing — run ./infra/init-rds-db.sh first" >&2
}

echo ""
echo "=== security group ingress (port 5432) ==="
SG_ID="$(aws rds describe-db-instances \
  --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}" \
  --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' \
  --output text)"
aws ec2 describe-security-groups \
  --group-ids "${SG_ID}" \
  --query 'SecurityGroups[0].IpPermissions[?FromPort==`5432`]' \
  --output table

echo ""
echo "Manual check (disallowed IP): from a host whose IP is NOT in the SG above,"
echo "run: psql \"<DATABASE_URL>\" -c 'SELECT 1'"
echo "Expected: connection timeout or 'no pg_hba.conf entry' — not a successful query."

unset DATABASE_URL PSQL_URL
