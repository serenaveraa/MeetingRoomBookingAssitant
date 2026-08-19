#!/usr/bin/env bash
# Deploy the ODC Free Tier CloudFormation stack (two-phase friendly).
#
# First time:
#   1. BootstrapMode=true ./infra/scripts/deploy.sh     # ECR + RDS + SGs
#   2. ./infra/scripts/build_and_push.sh
#   3. ./infra/scripts/deploy.sh                        # full compute
#   4. python infra/migrate_rds.py
#
# Env / files:
#   AWS_DEFAULT_REGION (default us-east-2)
#   STACK_NAME (default odc-meeting)
#   infra/parameters.json  — copy from parameters.example.json (gitignored secrets OK)
#   BootstrapMode=true|false (default false)
#   KEY_NAME, ALLOWED_SSH_CIDR, DB_PASSWORD, GROQ_API_KEY, … override JSON
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-us-east-2}"
STACK_NAME="${STACK_NAME:-odc-meeting}"
TEMPLATE="$ROOT/infra/cloudformation/odc-stack.yaml"
PARAMS_FILE="${PARAMS_FILE:-$ROOT/infra/parameters.json}"
BOOTSTRAP_MODE="${BootstrapMode:-false}"
IMAGE_TAG="${LAMBDA_IMAGE_TAG:-latest}"

if [[ -f "$ROOT/infra/.deploy/image.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/infra/.deploy/image.env"
  IMAGE_TAG="${LAMBDA_IMAGE_TAG:-$IMAGE_TAG}"
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need_cmd aws
need_cmd python3

echo "Account=$(aws sts get-caller-identity --query Account --output text) Region=${REGION} Stack=${STACK_NAME} BootstrapMode=${BOOTSTRAP_MODE}"

# ---------------------------------------------------------------------------
# Resolve default VPC + two subnets in distinct AZs
# ---------------------------------------------------------------------------
VPC_ID="$(aws ec2 describe-vpcs \
  --region "$REGION" \
  --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' \
  --output text)"
if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
  echo "No default VPC in ${REGION}. Create one or pass VpcId via parameters." >&2
  exit 1
fi

# Pick one subnet per AZ (stable sort)
SUBNET_CSV="$(aws ec2 describe-subnets \
  --region "$REGION" \
  --filters "Name=vpc-id,Values=${VPC_ID}" \
  --query 'sort_by(Subnets,&AvailabilityZone)[].{az:AvailabilityZone,id:SubnetId}' \
  --output json | python3 -c '
import json,sys
rows=json.load(sys.stdin)
seen=set(); ids=[]
for r in rows:
    if r["az"] in seen: continue
    seen.add(r["az"]); ids.append(r["id"])
    if len(ids)==2: break
if len(ids)<2:
    raise SystemExit("Need at least 2 subnets in different AZs")
print(",".join(ids))
')"

echo "VPC=${VPC_ID} Subnets=${SUBNET_CSV}"

# ---------------------------------------------------------------------------
# Build parameter overrides
# ---------------------------------------------------------------------------
OVERRIDES=(
  "VpcId=${VPC_ID}"
  "SubnetIds=${SUBNET_CSV}"
  "BootstrapMode=${BOOTSTRAP_MODE}"
  "LambdaImageTag=${IMAGE_TAG}"
)

load_json_params() {
  if [[ ! -f "$PARAMS_FILE" ]]; then
    return 0
  fi
  python3 - "$PARAMS_FILE" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
# Accept either CFN CLI list form or flat object
if isinstance(data, list):
    for item in data:
        k, v = item["ParameterKey"], item["ParameterValue"]
        if k in ("VpcId", "SubnetIds", "BootstrapMode", "LambdaImageTag"):
            continue
        print(f"{k}={v}")
elif isinstance(data, dict):
    for k, v in data.items():
        if k in ("VpcId", "SubnetIds", "BootstrapMode", "LambdaImageTag"):
            continue
        print(f"{k}={v}")
PY
}

while IFS= read -r line; do
  [[ -n "$line" ]] && OVERRIDES+=("$line")
done < <(load_json_params)

# Env var overrides (secrets)
[[ -n "${KEY_NAME:-}" ]] && OVERRIDES+=("KeyName=${KEY_NAME}")
[[ -n "${ALLOWED_SSH_CIDR:-}" ]] && OVERRIDES+=("AllowedSshCidr=${ALLOWED_SSH_CIDR}")
[[ -n "${DB_PASSWORD:-}" ]] && OVERRIDES+=("DbPassword=${DB_PASSWORD}")
[[ -n "${GROQ_API_KEY:-}" ]] && OVERRIDES+=("GroqApiKey=${GROQ_API_KEY}")
[[ -n "${GROQ_MODEL:-}" ]] && OVERRIDES+=("GroqModel=${GROQ_MODEL}")
[[ -n "${BREVO_API_KEY:-}" ]] && OVERRIDES+=("BrevoApiKey=${BREVO_API_KEY}")
[[ -n "${BREVO_SENDER_EMAIL:-}" ]] && OVERRIDES+=("BrevoSenderEmail=${BREVO_SENDER_EMAIL}")
[[ -n "${REPO_URL:-}" ]] && OVERRIDES+=("RepoUrl=${REPO_URL}")
[[ -n "${REPO_BRANCH:-}" ]] && OVERRIDES+=("RepoBranch=${REPO_BRANCH}")

if [[ "$BOOTSTRAP_MODE" != "true" ]]; then
  # Require KeyName for full deploy (SSH); warn if missing
  HAS_KEY=false
  for o in "${OVERRIDES[@]}"; do
    if [[ "$o" == KeyName=* && "$o" != "KeyName=" ]]; then
      HAS_KEY=true
      break
    fi
  done
  if [[ "$HAS_KEY" != true ]]; then
    echo "Warning: KeyName is empty — EC2 will launch without an SSH key pair." >&2
  fi
fi

# Ensure DbPassword present for create
HAS_DB_PASS=false
for o in "${OVERRIDES[@]}"; do
  if [[ "$o" == DbPassword=* && "$o" != "DbPassword=" ]]; then
    HAS_DB_PASS=true
    break
  fi
done
if [[ "$HAS_DB_PASS" != true ]]; then
  if [[ -f "$ROOT/infra/local.env" ]]; then
    # Reuse password from prior deploy
    # shellcheck disable=SC1091
    source <(grep -E '^(DB_PASSWORD)=' "$ROOT/infra/local.env" | sed 's/^/export /')
    OVERRIDES+=("DbPassword=${DB_PASSWORD}")
  else
    # Generate alphanumeric password
    DB_PASSWORD="$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(24)))')"
    OVERRIDES+=("DbPassword=${DB_PASSWORD}")
    echo "Generated DbPassword (saved to infra/local.env after deploy)"
  fi
fi

echo "Deploying ${STACK_NAME}…"
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --template-file "$TEMPLATE" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "${OVERRIDES[@]}"

# ---------------------------------------------------------------------------
# Write infra/local.env + outputs.json from stack outputs + password
# ---------------------------------------------------------------------------
mkdir -p "$ROOT/infra/.deploy"

aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs' \
  --output json > "$ROOT/infra/outputs.json"

# Extract password we just used
DB_PASS_VALUE=""
for o in "${OVERRIDES[@]}"; do
  if [[ "$o" == DbPassword=* ]]; then
    DB_PASS_VALUE="${o#DbPassword=}"
  fi
done

python3 - "$ROOT" "$DB_PASS_VALUE" "$REGION" <<'PY'
import json, os, pathlib, sys
root = pathlib.Path(sys.argv[1])
password = sys.argv[2]
region = sys.argv[3]
outputs = {o["OutputKey"]: o["OutputValue"] for o in json.loads((root / "infra/outputs.json").read_text())}
endpoint = outputs.get("RdsEndpoint", "")
port = outputs.get("RdsPort", "5432")
# Recover username/db from DatabaseUrlHint if present
hint = outputs.get("DatabaseUrlHint", "")
# postgresql+psycopg://user@host:port/db?sslmode=require
user, dbname = "odcadmin", "meeting_room"
if "://" in hint:
    rest = hint.split("://", 1)[1]
    user = rest.split("@", 1)[0]
    after_at = rest.split("@", 1)[-1]
    path = after_at.split("/", 1)[-1]
    dbname = path.split("?", 1)[0]

database_url = (
    f"postgresql+psycopg://{user}:{password}@{endpoint}:{port}/{dbname}?sslmode=require"
)
lines = [
    f"AWS_DEFAULT_REGION={region}",
    f"STACK_NAME={os.environ.get('STACK_NAME', 'odc-meeting')}",
    f"DB_INSTANCE_ID=odc-meeting-room",
    f"DB_USER={user}",
    f"DB_PASSWORD={password}",
    f"DB_NAME={dbname}",
    f"DB_ENDPOINT={endpoint}",
    f"DB_PORT={port}",
    f"DATABASE_URL={database_url}",
    f"ECR_URI={outputs.get('EcrUri', '')}",
    f"API_URL={outputs.get('ApiUrl', '')}",
    f"STREAMLIT_URL={outputs.get('StreamlitUrl', '')}",
    "",
]
path = root / "infra/local.env"
path.write_text("\n".join(lines))
path.chmod(0o600)
print(f"Wrote {path}")
if outputs.get("ApiUrl"):
    print(f"ApiUrl={outputs['ApiUrl']}")
if outputs.get("StreamlitUrl"):
    print(f"StreamlitUrl={outputs['StreamlitUrl']}")
print(f"EcrUri={outputs.get('EcrUri', '')}")
print(f"RdsEndpoint={endpoint}")
PY

echo "Done."
if [[ "$BOOTSTRAP_MODE" == "true" ]]; then
  echo "Next: ./infra/scripts/build_and_push.sh && BootstrapMode=false ./infra/scripts/deploy.sh"
fi
