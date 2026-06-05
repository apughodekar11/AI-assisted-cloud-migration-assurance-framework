#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${TF_STACK_NAME:-cloud-migrate-ai-dev}"
REGION="${AWS_REGION:-eu-west-1}"

get() {
  aws ssm get-parameter --name "$1" --with-decryption --region "$REGION" \
    --query 'Parameter.Value' --output text
}

DB_HOST="$(get "/$STACK_NAME/db_host")"
DB_PORT="$(get "/$STACK_NAME/db_port")"
DB_USER="$(get "/$STACK_NAME/db_user")"
DB_PASS="$(get "/$STACK_NAME/db_pass" || true)"   # not used in artifact; present for completeness
DB_NAME="$(get "/$STACK_NAME/db_name")"
LAMBDA_SG_ID="$(get "/$STACK_NAME/lambda_sg_id")"
SUBNET0="$(get "/$STACK_NAME/private_subnet_ids/0")"
SUBNET1="$(get "/$STACK_NAME/private_subnet_ids/1")"

BASE_URL_FILE="artifacts_http_url.txt"
if [[ ! -f "$BASE_URL_FILE" ]]; then
  echo "WARNING: $BASE_URL_FILE not found. Set http_base_url manually." >&2
  HTTP_BASE_URL=""
else
  HTTP_BASE_URL="$(cat "$BASE_URL_FILE")"
fi

mkdir -p artifacts
jq -n \
  --arg stack_name   "$STACK_NAME" \
  --arg db_host      "$DB_HOST" \
  --arg db_user      "$DB_USER" \
  --arg db_name      "$DB_NAME" \
  --argjson db_port  "$DB_PORT" \
  --arg lambda_sg_id "$LAMBDA_SG_ID" \
  --arg http_base_url "$HTTP_BASE_URL" \
  --argjson private_subnet_ids "$(jq -n --arg a "$SUBNET0" --arg b "$SUBNET1" '[ $a, $b ]')" \
  '{
     stack_name: $stack_name,
     db_host: $db_host,
     db_user: $db_user,
     db_name: $db_name,
     db_port: $db_port,
     lambda_sg_id: $lambda_sg_id,
     private_subnet_ids: $private_subnet_ids,
     http_base_url: $http_base_url,
     generated_at: (now | todate)
   }' > artifacts/migration_aws_dev.json

echo "[artifacts] wrote artifacts/migration_aws_dev.json (from SSM)"

