#!/usr/bin/env bash
set -euo pipefail
OUT="validate/cost_carbon.txt"
mkdir -p validate

STACK="${TF_STACK_NAME:-cloud-migrate-ai-dev}"
REGION="${AWS_REGION:-eu-west-1}"
TS="$(date -u +%FT%TZ)"

# Stubbed numbers; replace later with Cost Explorer + region carbon API
cat > "$OUT" <<TXT
generated_at: $TS
stack: $STACK
region: $REGION
period: last_24h
estimated_cost_usd: 0.37
estimated_carbon_gco2e: 42
notes: stub metrics for thesis demonstration (replace with real API calls in Part 2)
TXT

echo "[gate] cost & carbon artifact -> $OUT"
