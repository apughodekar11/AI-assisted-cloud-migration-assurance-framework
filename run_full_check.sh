#!/usr/bin/env bash
set -euo pipefail

# Simple healthcheck for Part 1 (run from repo root)
# Usage: ./run_full_check.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

AWS_REGION="${AWS_REGION:-eu-west-1}"
STAGE="${STAGE:-dev}"

banner() {
  echo
  echo "============================================================"
  echo "  $1"
  echo "============================================================"
}

fail() {
  echo
  echo "❌ $1"
  exit 1
}

ok() {
  echo "✅ $1"
}

banner "CHECKING TOOLCHAIN"

for cmd in python3 node npm curl jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    fail "Required command '$cmd' not found on PATH"
  else
    echo " - found: $cmd ($(command -v "$cmd"))"
  fi
done

if ! command -v opa >/dev/null 2>&1; then
  if [[ -x ./opa ]]; then
    echo " - using local ./opa binary"
    OPA_CMD="./opa"
  else
    fail "OPA not found (no 'opa' in PATH and no ./opa binary)"
  fi
else
  OPA_CMD="opa"
  echo " - found: opa ($($OPA_CMD version | head -n1))"
fi

# serverless may be global or via npx
if command -v serverless >/dev/null 2>&1; then
  SLS_CMD="serverless"
  echo " - found: serverless CLI ($(serverless --version))"
else
  SLS_CMD="npx --yes serverless@3"
  echo " - 'serverless' not found, will use: $SLS_CMD"
fi

banner "RUNNING OPA POLICY TESTS"

$OPA_CMD fmt -w policy/opa
$OPA_CMD test -v policy/opa
ok "OPA unit tests passed"

banner "CHECKING SERVERLESS PACKAGE"

if [[ -d serverless ]]; then
  ( cd serverless && $SLS_CMD package --stage "$STAGE" --region "$AWS_REGION" >/dev/null )
  ok "Serverless package step completed"
else
  echo "⚠️ 'serverless/' directory not found, skipping package check"
fi

banner "EXPORTING ARTIFACTS FROM SSM"

if [[ -x deploy/scripts/export_artifacts_from_ssm.sh ]]; then
  ./deploy/scripts/export_artifacts_from_ssm.sh
  ok "export_artifacts_from_ssm.sh completed"
else
  echo "⚠️ deploy/scripts/export_artifacts_from_ssm.sh not executable or missing, skipping"
fi

if [[ -f artifacts/migration_aws_dev.json ]]; then
  echo " - found artifacts/migration_aws_dev.json"
else
  echo "⚠️ artifacts/migration_aws_dev.json missing (OPA gate may fail)"
fi

banner "RUNNING OPA GATE"

if [[ -x validate/gates/opa_gate.sh ]]; then
  ./validate/gates/opa_gate.sh artifacts/migration_aws_dev.json
  ok "OPA gate passed"
else
  echo "⚠️ validate/gates/opa_gate.sh missing or not executable, skipping OPA gate"
fi

banner "RUNNING SMOKE TESTS"

if [[ -x deploy/scripts/smoke.sh ]]; then
  ./deploy/scripts/smoke.sh
  ok "Smoke tests completed"
else
  echo "⚠️ deploy/scripts/smoke.sh missing or not executable, skipping smoke tests"
fi

banner "RUNNING ANOMALY & COST/CARBON GATES"

if [[ -x validate/gates/anomaly_hook.sh ]]; then
  ./validate/gates/anomaly_hook.sh telemetry/schemas/metrics_v1.json || fail "Anomaly hook failed"
  ok "Anomaly hook completed"
else
  echo "⚠️ validate/gates/anomaly_hook.sh missing or not executable, skipping anomaly gate"
fi

if [[ -x validate/gates/cost_carbon_gate.sh ]]; then
  ./validate/gates/cost_carbon_gate.sh || fail "Cost/Carbon gate failed"
  ok "Cost/Carbon gate completed"
else
  echo "⚠️ validate/gates/cost_carbon_gate.sh missing or not executable, skipping cost/carbon gate"
fi

banner "SUMMARY"

echo "If you saw ✅ for:"
echo " - OPA unit tests"
echo " - Serverless package"
echo " - export_artifacts_from_ssm.sh"
echo " - OPA gate"
echo " - Smoke tests"
echo " - Anomaly & Cost/Carbon gates"
echo
echo "🎉 Everything in Part 1 is wired and working end-to-end."
echo

exit 0

