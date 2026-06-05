#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RAW="$(cat "$ROOT/artifacts_http_url.txt")"
BASE=$(echo "$RAW" | sed -E 's#(https?://[^/]+).*#\1#')
"$ROOT/validate/gates/smoke_gate.sh" "$BASE" 2 5

