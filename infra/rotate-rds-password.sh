#!/usr/bin/env bash
# Rotate RDS master password and refresh Secrets Manager DATABASE_URL.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

load_config
require_aws

if ! rds_instance_exists; then
  echo "error: RDS instance ${DB_INSTANCE_IDENTIFIER} not found" >&2
  exit 1
fi

NEW_PASSWORD="$(generate_password)"
ENDPOINT="$(get_rds_endpoint)"

echo "rotating master password for ${DB_INSTANCE_IDENTIFIER}..."
aws rds modify-db-instance \
  --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}" \
  --master-user-password "${NEW_PASSWORD}" \
  --apply-immediately \
  >/dev/null

echo "waiting for modification to complete..."
aws rds wait db-instance-available --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}"

DATABASE_URL="$(build_database_url "${DB_MASTER_USERNAME}" "${NEW_PASSWORD}" "${ENDPOINT}" "${DB_NAME}")"
store_database_url_secret "${DATABASE_URL}"

unset NEW_PASSWORD DATABASE_URL

echo "password rotated; secret ${SECRET_NAME} updated."
echo "Restart any running API/Lambda tasks so they pick up the new secret value."
