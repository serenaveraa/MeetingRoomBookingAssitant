#!/usr/bin/env bash
# Build the Lambda container image and push it to the stack ECR repo.
#
# Prerequisites:
#   - AWS CLI configured (us-east-2)
#   - Docker running
#   - Bootstrap stack already deployed (ECR exists), OR set ECR_URI explicitly
#
# Usage (from repo root):
#   ./infra/scripts/build_and_push.sh
#   ./infra/scripts/build_and_push.sh my-tag
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-us-east-2}"
STACK_NAME="${STACK_NAME:-odc-meeting}"
TAG="${1:-latest}"
REPO_NAME="odc-meeting-api"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_URI="${ECR_URI:-${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}}"

echo "Account=${ACCOUNT_ID} Region=${REGION} Image=${ECR_URI}:${TAG}"

# Prefer ECR from stack output when available
if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" &>/dev/null; then
  STACK_ECR="$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='EcrUri'].OutputValue" \
    --output text 2>/dev/null || true)"
  if [[ -n "${STACK_ECR}" && "${STACK_ECR}" != "None" ]]; then
    ECR_URI="$STACK_ECR"
  fi
fi

# Ensure repository exists (bootstrap stack creates it; this is a safety net)
if ! aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$REGION" &>/dev/null; then
  echo "ECR repo ${REPO_NAME} not found. Deploy bootstrap first:"
  echo "  BootstrapMode=true ./infra/scripts/deploy.sh"
  exit 1
fi

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "Building image (linux/amd64, docker media type for Lambda)…"
# Prefer the local docker driver (faster cache). Lambda rejects OCI indexes/attestations.
docker buildx use desktop-linux 2>/dev/null || docker buildx use default 2>/dev/null || true
docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --sbom=false \
  --output "type=image,name=${ECR_URI}:${TAG},push=true,oci-mediatypes=false" \
  -f "$ROOT/backend/Dockerfile.lambda" \
  "$ROOT"

DIGEST="$(aws ecr describe-images \
  --repository-name "$REPO_NAME" \
  --region "$REGION" \
  --image-ids imageTag="$TAG" \
  --query 'imageDetails[0].imageDigest' \
  --output text)"

mkdir -p "$ROOT/infra/.deploy"
cat > "$ROOT/infra/.deploy/image.env" <<EOF
ECR_URI=${ECR_URI}
LAMBDA_IMAGE_TAG=${TAG}
IMAGE_DIGEST=${DIGEST}
EOF

echo "Pushed ${ECR_URI}:${TAG}"
echo "Digest ${DIGEST}"
