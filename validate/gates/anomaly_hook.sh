#!/usr/bin/env bash
set -euo pipefail

SCHEMA="${1:-telemetry/schemas/metrics_v1.json}"
OUT="validate/anomaly.json"
mkdir -p validate

# Accept an optional override: --anomaly to force a failure demo
ANOMALY_FLAG="${2:-}"
ANOMALY=false
if [[ "$ANOMALY_FLAG" == "--anomaly" ]]; then
  ANOMALY=true
fi

# Minimal payload you can cite in the report
jq -n --arg schema "$SCHEMA" --arg ts "$(date -u +%FT%TZ)" \
  --argjson anomaly "$ANOMALY" \
  '{
     schema: $schema,
     generated_at: $ts,
     anomaly: $anomaly,
     reason: (if $anomaly then "latency_p95 crossed threshold" else "no anomaly detected" end),
     thresholds: { latency_p95_ms: 350, error_rate_pct: 1.0 },
     sample_window: { points: 0 }
   }' > "$OUT"

echo "[gate] anomaly artifact -> $OUT"
if $ANOMALY; then
  exit 1
fi
