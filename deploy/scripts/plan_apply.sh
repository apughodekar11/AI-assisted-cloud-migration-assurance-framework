#!/usr/bin/env bash
set -euo pipefail

# Usage: ./deploy/scripts/plan_apply.sh aws dev
CLOUD="${1:-aws}"
STAGE="${2:-dev}"

# Expect these env vars (set them in your shell or ~/.bashrc)
: "${AWS_REGION:=eu-west-1}"
: "${TF_STACK_NAME:=cloud-migrate-ai-dev}"
: "${VERSION:=v0.1.0}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "[deploy] cloud=$CLOUD stage=$STAGE region=$AWS_REGION stack=$TF_STACK_NAME version=$VERSION"

# --- Terraform apply/refresh ---
pushd "$ROOT/infra/terraform/$CLOUD" >/dev/null
  terraform init -upgrade
  terraform apply -auto-approve -var-file="$ROOT/infra/tfvars/${STAGE}.tfvars"

  STACK_NAME="$(terraform output -raw stack_name)"
  DB_HOST="$(terraform output -raw db_host)"
  DB_PORT="$(terraform output -raw db_port)"
  DB_USER="$(terraform output -raw db_user)"
  DB_PASS="$(terraform output -raw db_pass)"
  DB_NAME="$(terraform output -raw db_name)"
  LAMBDA_SG="$(terraform output -raw lambda_sg_id)"
  SUBNETS_JSON="$(terraform output -json private_subnet_ids)"
popd >/dev/null

echo "[deploy] terraform outputs:"
echo "  stack_name=$STACK_NAME"
echo "  db_host=$DB_HOST"
echo "  db_port=$DB_PORT"
echo "  db_user=$DB_USER"
echo "  db_name=$DB_NAME"
echo "  lambda_sg_id=$LAMBDA_SG"
echo "  private_subnets=$(echo "$SUBNETS_JSON" | jq -cr '.')"

# --- Write Terraform outputs into SSM Parameter Store ---
# NOTE: For real secrets, consider --type SecureString with KMS. For a thesis demo, String is fine.
echo "[deploy] writing parameters to SSM under /$STACK_NAME/*"

aws ssm put-parameter --name "/$STACK_NAME/db_host"          --value "$DB_HOST"   --type String --overwrite >/dev/null
aws ssm put-parameter --name "/$STACK_NAME/db_port"          --value "$DB_PORT"   --type String --overwrite >/dev/null
aws ssm put-parameter --name "/$STACK_NAME/db_user"          --value "$DB_USER"   --type String --overwrite >/dev/null
aws ssm put-parameter --name "/$STACK_NAME/db_pass"          --value "$DB_PASS"   --type String --overwrite >/dev/null
aws ssm put-parameter --name "/$STACK_NAME/db_name"          --value "$DB_NAME"   --type String --overwrite >/dev/null
aws ssm put-parameter --name "/$STACK_NAME/lambda_sg_id"     --value "$LAMBDA_SG" --type String --overwrite >/dev/null

SUBNET0="$(echo "$SUBNETS_JSON" | jq -r '.[0]')"
SUBNET1="$(echo "$SUBNETS_JSON" | jq -r '.[1]')"
aws ssm put-parameter --name "/$STACK_NAME/private_subnet_ids/0" --value "$SUBNET0" --type String --overwrite >/dev/null
aws ssm put-parameter --name "/$STACK_NAME/private_subnet_ids/1" --value "$SUBNET1" --type String --overwrite >/dev/null

echo "[deploy] SSM parameters written."

# --- Serverless deploy ---
pushd "$ROOT/serverless" >/dev/null
  # Ensure a package.json exists to keep npm quiet (first run only)
  [[ -f package.json ]] || npm init -y >/dev/null 2>&1

  # Install the Python requirements plugin (idempotent)
  npm i --silent
  npx serverless plugin install -n serverless-python-requirements >/dev/null

  # Bundle Python deps into a layer (speeds up cold starts)
  # (The plugin will also try to build these; preinstall helps for local dev)
  pip install -r "$ROOT/app/requirements.txt" \
      -t ./.python_packages/lib/python3.11/site-packages --upgrade --quiet || true

  echo "[deploy] serverless deploy (stage=$STAGE, region=$AWS_REGION)..."
  npx serverless deploy --stage "$STAGE" --region "$AWS_REGION"

  # Grab the first GET endpoint printed by `sls info`
  HTTP_URL="$(
    npx serverless info --stage "$STAGE" --region "$AWS_REGION" \
    | awk '/endpoints:/{p=1;next} p && /GET -/ {print $3; exit}'
  )"
popd >/dev/null

if [[ -z "${HTTP_URL:-}" ]]; then
  echo "[deploy] ERROR: could not parse HTTP URL from serverless info."
  exit 1
fi

echo "$HTTP_URL" > "$ROOT/artifacts_http_url.txt"
echo "[deploy] HTTP base URL: $HTTP_URL"
echo "[deploy] done."

