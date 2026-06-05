#!/usr/bin/env bash
set -euo pipefail

FEATURES_PATH=${1:-telemetry/processed/features.parquet}
OUT_JSON="validate/ai_score.json"

mkdir -p validate

echo "[ai_gate] Starting AI validation gate..."
echo "[ai_gate] Using features from: ${FEATURES_PATH}"

python ai/models/predict.py \
  --features-path "${FEATURES_PATH}" \
  --dataset-config ai/configs/dataset.yml \
  --lstm-config ai/configs/model_lstm.yml \
  --ae-config ai/configs/model_ae.yml \
  --models-dir ai/artifacts/models \
  --out-json "${OUT_JSON}" \
  --threshold 3.0

echo "[ai_gate] Prediction output:"
cat "${OUT_JSON}" || true

flag=$(jq -r '.flag' "${OUT_JSON}" 2>/dev/null || echo "false")

if [ "${flag}" = "true" ]; then
  echo "[ai_gate] 🚨 Anomaly detected — failing gate."
  exit 1
else
  echo "[ai_gate] ✅ No anomaly detected — gate passed."
fi

