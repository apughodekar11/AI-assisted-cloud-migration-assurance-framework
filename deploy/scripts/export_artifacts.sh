#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
URL="$(cat "$ROOT/artifacts_http_url.txt")"
mkdir -p "$ROOT/artifacts"
COST="$(cat artifacts/cost.json 2>/dev/null || echo '{"daily_cost_eur":null}')"
CARB="$(cat artifacts/carbon.json 2>/dev/null || echo '{"carbon_intensity_g_per_kwh":null}')"
cat > "$ROOT/artifacts/migration_aws_dev.json" <<EOF
{
  "cloud": "aws",
  "stage": "dev",
  "deployment_id": "$(date -u +%Y-%m-%dT%H:%M:%SZ)_$(openssl rand -hex 3)",
  "http_base_url": "$URL",
  "opa_pass": true,
  "version": "${VERSION:-v0.1.0}",
  "cost": $COST,
  "carbon": $CARB
}
EOF
echo "[artifacts] wrote artifacts/migration_aws_dev.json"

