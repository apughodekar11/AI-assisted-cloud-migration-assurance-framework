#!/usr/bin/env bash
set -euo pipefail
URL="$1"
RETRIES="${2:-2}"
TIMEOUT="${3:-5}"

try=0
while [[ $try -le $RETRIES ]]; do
  code=$(curl -m "$TIMEOUT" -s -o /dev/null -w "%{http_code}" "$URL/healthz")
  echo "[smoke] $URL/healthz -> $code (try $try)"
  if [[ "$code" == "200" ]]; then exit 0; fi
  try=$((try+1)); sleep 2
done
echo "[smoke] FAILED after $RETRIES retries"
exit 1

