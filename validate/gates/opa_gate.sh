#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:-}"
if [[ -z "${INPUT}" || ! -f "${INPUT}" ]]; then
  echo "usage: $0 <artifact.json>" >&2
  exit 2
fi

POLICY="policy/opa/rules.rego"

# Find an OPA binary: prefer ./opa, else system opa
if [[ -x ./opa ]]; then
  OPA="./opa"
elif command -v opa >/dev/null 2>&1; then
  OPA="$(command -v opa)"
else
  echo "ERROR: opa binary not found. Download from https://openpolicyagent.org/downloads/" >&2
  exit 2
fi

# Evaluate deny
DENY_JSON="$("$OPA" eval -f json -i "$INPUT" -d "$POLICY" 'data.cloud_migrate.deny')"
DENY_COUNT="$(jq -r '.result[0].expressions[0].value | length' <<<"$DENY_JSON" 2>/dev/null || echo 0)"

# Evaluate warn
WARN_JSON="$("$OPA" eval -f json -i "$INPUT" -d "$POLICY" 'data.cloud_migrate.warn')"
WARN_COUNT="$(jq -r '.result[0].expressions[0].value | length' <<<"$WARN_JSON" 2>/dev/null || echo 0)"

# Print warnings (if any)
if [[ "${WARN_COUNT}" != "0" ]]; then
  echo "OPA WARNINGS:"
  jq -r '.result[0].expressions[0].value | keys[]' <<<"$WARN_JSON" || true
  echo
fi

# Fail on deny
if [[ "${DENY_COUNT}" != "0" ]]; then
  echo "OPA DENY (${DENY_COUNT}):"
  # deny is a SET -> represented as an object { "msg": true, ... } in eval JSON
  jq -r '.result[0].expressions[0].value | keys[]' <<<"$DENY_JSON" || true
  exit 1
fi

echo "OPA gate: PASS"

