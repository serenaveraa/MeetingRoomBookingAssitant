#!/usr/bin/env bash
# Shared helpers for MeetingRoomBookingAssitant AWS infra scripts.

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${INFRA_DIR}/.." && pwd)"

# Defaults — override via infra/config.env or environment.
: "${AWS_REGION:=sa-east-1}"
: "${PROJECT_PREFIX:=odc-mrba}"
: "${DB_INSTANCE_IDENTIFIER:=${PROJECT_PREFIX}-postgres}"
: "${DB_NAME:=meeting_room}"
: "${DB_MASTER_USERNAME:=odc_admin}"
: "${DB_INSTANCE_CLASS:=db.t3.micro}"
: "${DB_ALLOCATED_STORAGE:=20}"
: "${DB_STORAGE_TYPE:=gp3}"
: "${SECRET_NAME:=${PROJECT_PREFIX}/DATABASE_URL}"
: "${RDS_SECURITY_GROUP_NAME:=${PROJECT_PREFIX}-rds-sg}"
: "${DB_SUBNET_GROUP_NAME:=${PROJECT_PREFIX}-db-subnets}"

load_config() {
  if [[ -f "${INFRA_DIR}/config.env" ]]; then
    # shellcheck disable=SC1091
    source "${INFRA_DIR}/config.env"
  fi
  export AWS_REGION PROJECT_PREFIX DB_INSTANCE_IDENTIFIER DB_NAME
  export DB_MASTER_USERNAME DB_INSTANCE_CLASS DB_ALLOCATED_STORAGE DB_STORAGE_TYPE
  export SECRET_NAME RDS_SECURITY_GROUP_NAME DB_SUBNET_GROUP_NAME
  export AWS_DEFAULT_REGION="${AWS_REGION}"
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "error: required command not found: ${cmd}" >&2
    exit 1
  fi
}

require_aws() {
  require_cmd aws
  aws sts get-caller-identity >/dev/null
}

resolve_admin_cidr() {
  if [[ -n "${ADMIN_CIDR:-}" ]]; then
    echo "${ADMIN_CIDR}"
    return
  fi
  require_cmd curl
  local ip
  ip="$(curl -fsS --max-time 10 https://checkip.amazonaws.com/ | tr -d '[:space:]')"
  if [[ -z "${ip}" ]]; then
    echo "error: set ADMIN_CIDR in infra/config.env (could not detect public IP)" >&2
    exit 1
  fi
  echo "${ip}/32"
}

generate_password() {
  # RDS-safe charset; avoid shell/URL metacharacters.
  openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32
}

urlencode() {
  python3 - "$1" <<'PY'
import sys
import urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=""))
PY
}

build_database_url() {
  local user="$1" password="$2" host="$3" dbname="$4"
  local encoded_password
  encoded_password="$(urlencode "${password}")"
  echo "postgresql+psycopg://${user}:${encoded_password}@${host}:5432/${dbname}?sslmode=require"
}

resolve_vpc_id() {
  if [[ -n "${VPC_ID:-}" ]]; then
    echo "${VPC_ID}"
    return
  fi
  aws ec2 describe-vpcs \
    --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' \
    --output text
}

get_or_create_security_group() {
  local vpc_id="$1"
  local sg_id
  sg_id="$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${RDS_SECURITY_GROUP_NAME}" "Name=vpc-id,Values=${vpc_id}" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || true)"

  if [[ -n "${sg_id}" && "${sg_id}" != "None" ]]; then
    echo "security group already exists: ${sg_id}" >&2
    echo "${sg_id}"
    return
  fi

  sg_id="$(aws ec2 create-security-group \
    --group-name "${RDS_SECURITY_GROUP_NAME}" \
    --description "RDS Postgres for ${PROJECT_PREFIX} (issue #43)" \
    --vpc-id "${vpc_id}" \
    --query 'GroupId' \
    --output text)"

  aws ec2 create-tags \
    --resources "${sg_id}" \
    --tags "Key=Name,Value=${RDS_SECURITY_GROUP_NAME}" "Key=Project,Value=${PROJECT_PREFIX}"

  echo "created security group: ${sg_id}" >&2
  echo "${sg_id}"
}

ensure_sg_ingress_rule() {
  local sg_id="$1" source="$2" description="$3"
  local existing
  existing="$(aws ec2 describe-security-groups \
    --group-ids "${sg_id}" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`5432\` && ToPort==\`5432\` && IpProtocol==\`tcp\`]" \
    --output json)"

  if [[ "${source}" == sg-* ]]; then
    if echo "${existing}" | python3 -c "import json,sys; perms=json.load(sys.stdin); sys.exit(0 if any(g.get('GroupId')=='${source}' for p in perms for g in p.get('UserIdGroupPairs',[])) else 1)"; then
      echo "ingress rule already present: 5432 from ${source}" >&2
      return
    fi
    aws ec2 authorize-security-group-ingress \
      --group-id "${sg_id}" \
      --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=${source},Description=\"${description}\"}]"
  else
    if echo "${existing}" | python3 -c "import json,sys; perms=json.load(sys.stdin); sys.exit(0 if any(r.get('CidrIp')=='${source}' for p in perms for r in p.get('IpRanges',[])) else 1)"; then
      echo "ingress rule already present: 5432 from ${source}" >&2
      return
    fi
    aws ec2 authorize-security-group-ingress \
      --group-id "${sg_id}" \
      --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,IpRanges=[{CidrIp=${source},Description=\"${description}\"}]"
  fi
  echo "added ingress rule: 5432 from ${source}" >&2
}

get_or_create_db_subnet_group() {
  local vpc_id="$1"
  if aws rds describe-db-subnet-groups \
    --db-subnet-group-name "${DB_SUBNET_GROUP_NAME}" >/dev/null 2>&1; then
    echo "db subnet group already exists: ${DB_SUBNET_GROUP_NAME}" >&2
    return 0
  fi

  local subnet_ids
  subnet_ids="$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=${vpc_id}" \
    --query 'Subnets[*].SubnetId' \
    --output text | tr '\t' ' ')"

  if [[ -z "${subnet_ids// /}" ]]; then
    echo "error: no subnets found in VPC ${vpc_id}" >&2
    exit 1
  fi

  # shellcheck disable=SC2086
  aws rds create-db-subnet-group \
    --db-subnet-group-name "${DB_SUBNET_GROUP_NAME}" \
    --db-subnet-group-description "Subnets for ${PROJECT_PREFIX} RDS" \
    --subnet-ids ${subnet_ids}

  echo "created db subnet group: ${DB_SUBNET_GROUP_NAME}" >&2
}

resolve_postgres_engine_version() {
  aws rds describe-db-engine-versions \
    --engine postgres \
    --query "DBEngineVersions[?starts_with(EngineVersion, '16.')]| sort_by(@, &EngineVersion) | [-1].EngineVersion" \
    --output text
}

rds_instance_exists() {
  aws rds describe-db-instances \
    --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}" >/dev/null 2>&1
}

wait_for_rds_available() {
  echo "waiting for RDS instance ${DB_INSTANCE_IDENTIFIER} to become available..." >&2
  aws rds wait db-instance-available --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}"
}

get_rds_endpoint() {
  aws rds describe-db-instances \
    --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}" \
    --query 'DBInstances[0].Endpoint.Address' \
    --output text
}

get_rds_status() {
  aws rds describe-db-instances \
    --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}" \
    --query 'DBInstances[0].DBInstanceStatus' \
    --output text
}

store_database_url_secret() {
  local database_url="$1"
  local payload
  payload="$(python3 - "${database_url}" <<'PY'
import json, sys
print(json.dumps({"DATABASE_URL": sys.argv[1]}))
PY
)"

  if aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value \
      --secret-id "${SECRET_NAME}" \
      --secret-string "${payload}" >/dev/null
    echo "updated secret: ${SECRET_NAME}" >&2
  else
    aws secretsmanager create-secret \
      --name "${SECRET_NAME}" \
      --description "DATABASE_URL for ${PROJECT_PREFIX} (RDS Postgres)" \
      --secret-string "${payload}" \
      --tags "Key=Project,Value=${PROJECT_PREFIX}" >/dev/null
    echo "created secret: ${SECRET_NAME}" >&2
  fi
}

fetch_database_url_from_secret() {
  aws secretsmanager get-secret-value \
    --secret-id "${SECRET_NAME}" \
    --query 'SecretString' \
    --output text | python3 -c "import json,sys; data=json.load(sys.stdin); print(data['DATABASE_URL'])"
}

to_psql_url() {
  # Convert SQLAlchemy psycopg URL to libpq format for psql CLI checks.
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlparse, parse_qs, unquote

url = sys.argv[1].replace("postgresql+psycopg://", "postgresql://", 1)
parsed = urlparse(url)
user = unquote(parsed.username or "")
password = unquote(parsed.password or "")
host = parsed.hostname or ""
port = parsed.port or 5432
db = parsed.path.lstrip("/")
query = parse_qs(parsed.query)
sslmode = query.get("sslmode", ["require"])[0]
print(f"postgresql://{user}:{password}@{host}:{port}/{db}?sslmode={sslmode}")
PY
}
