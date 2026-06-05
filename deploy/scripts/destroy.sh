#!/usr/bin/env bash
set -euo pipefail
CLOUD="${1:-aws}"
STAGE="${2:-dev}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

pushd "$ROOT/serverless" >/dev/null
npx serverless remove --stage "$STAGE" || true
popd >/dev/null

pushd "$ROOT/infra/terraform/$CLOUD" >/dev/null
terraform destroy -auto-approve -var-file="$ROOT/infra/tfvars/${STAGE}.tfvars"
popd >/dev/null

