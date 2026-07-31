#!/usr/bin/env bash
# Idempotent RDS Postgres provisioning for MeetingRoomBookingAssitant (issue #43).
#
# Creates (or reuses):
#   - dedicated RDS security group (admin IP + optional Lambda SG on 5432)
#   - db.t3.micro PostgreSQL 16 instance (single-AZ, publicly accessible)
#   - Secrets Manager secret with DATABASE_URL (sslmode=require)
#
# Credentials are generated at create time and never printed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

load_config
require_aws

ADMIN_CIDR="$(resolve_admin_cidr)"
VPC_ID="$(resolve_vpc_id)"
ENGINE_VERSION="$(resolve_postgres_engine_version)"

echo "region:              ${AWS_REGION}"
echo "instance identifier: ${DB_INSTANCE_IDENTIFIER}"
echo "engine:              postgres ${ENGINE_VERSION}"
echo "vpc:                 ${VPC_ID}"
echo "admin cidr:          ${ADMIN_CIDR}"
if [[ -n "${LAMBDA_SECURITY_GROUP_ID:-}" ]]; then
  echo "lambda sg:           ${LAMBDA_SECURITY_GROUP_ID}"
else
  echo "lambda sg:           (not set — only admin IP allowed until Lambda SG is wired)"
fi

SG_ID="$(get_or_create_security_group "${VPC_ID}")"
ensure_sg_ingress_rule "${SG_ID}" "${ADMIN_CIDR}" "Admin psql/migrations"
if [[ -n "${LAMBDA_SECURITY_GROUP_ID:-}" ]]; then
  ensure_sg_ingress_rule "${SG_ID}" "${LAMBDA_SECURITY_GROUP_ID}" "Lambda vacate-reminder/agent"
fi

get_or_create_db_subnet_group "${VPC_ID}"

if rds_instance_exists; then
  STATUS="$(get_rds_status)"
  ENDPOINT="$(get_rds_endpoint)"
  echo "RDS instance already exists (${STATUS}): ${DB_INSTANCE_IDENTIFIER}"
  echo "endpoint: ${ENDPOINT}"
  if aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" >/dev/null 2>&1; then
    echo "secret exists: ${SECRET_NAME}"
  else
    echo "warning: RDS exists but secret ${SECRET_NAME} is missing." >&2
    echo "         Run infra/rotate-rds-password.sh to set a new password and store DATABASE_URL." >&2
  fi
  exit 0
fi

MASTER_PASSWORD="$(generate_password)"

echo "creating RDS instance ${DB_INSTANCE_IDENTIFIER} (password will be stored in Secrets Manager only)..."

aws rds create-db-instance \
  --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}" \
  --db-instance-class "${DB_INSTANCE_CLASS}" \
  --engine postgres \
  --engine-version "${ENGINE_VERSION}" \
  --master-username "${DB_MASTER_USERNAME}" \
  --master-user-password "${MASTER_PASSWORD}" \
  --db-name "${DB_NAME}" \
  --allocated-storage "${DB_ALLOCATED_STORAGE}" \
  --storage-type "${DB_STORAGE_TYPE}" \
  --no-multi-az \
  --publicly-accessible \
  --backup-retention-period 1 \
  --db-subnet-group-name "${DB_SUBNET_GROUP_NAME}" \
  --vpc-security-group-ids "${SG_ID}" \
  --tags "Key=Project,Value=${PROJECT_PREFIX}" "Key=Name,Value=${DB_INSTANCE_IDENTIFIER}" \
  >/dev/null

wait_for_rds_available
ENDPOINT="$(get_rds_endpoint)"
DATABASE_URL="$(build_database_url "${DB_MASTER_USERNAME}" "${MASTER_PASSWORD}" "${ENDPOINT}" "${DB_NAME}")"
store_database_url_secret "${DATABASE_URL}"

# Clear password from shell memory as soon as possible.
unset MASTER_PASSWORD DATABASE_URL

echo ""
echo "RDS provisioning complete."
echo "  status:     available"
echo "  identifier: ${DB_INSTANCE_IDENTIFIER}"
echo "  endpoint:   ${ENDPOINT}"
echo "  region:     ${AWS_REGION}"
echo "  secret:     ${SECRET_NAME}"
echo ""
echo "Next steps:"
echo "  1. ./infra/verify-rds.sh"
echo "  2. ./infra/init-rds-db.sh"
