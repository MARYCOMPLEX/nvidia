#!/usr/bin/env bash
# Usage: ./set-sleep.sh [hours]
#   hours  - hours from now to set sleep_at (default: 71, min: 1, max: 720)
# Env:
#   NVIDIA_AIR_API_BASE  - API base URL (default: https://api.air-ngc.nvidia.com/api/v3)
#   NVIDIA_AIR_API_KEY   - API key (required)
#   SIMULATION_ID        - simulation resource ID (required)
#   VERBOSE              - set to true for verbose output
set -euo pipefail

NVIDIA_AIR_API_BASE="${NVIDIA_AIR_API_BASE:-https://api.air-ngc.nvidia.com/api/v3}"
NVIDIA_AIR_API_KEY="${NVIDIA_AIR_API_KEY:?must set NVIDIA_AIR_API_KEY}"
SIMULATION_ID="${SIMULATION_ID:?must set SIMULATION_ID}"
VERBOSE="${VERBOSE:-}"

HOURS="${1:-71}"
if ! [[ "$HOURS" =~ ^[0-9]+$ ]] || [ "$HOURS" -lt 1 ] || [ "$HOURS" -gt 720 ]; then
  echo "error: hours must be an integer between 1 and 720 (got: $HOURS)" >&2
  exit 1
fi

for cmd in curl python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing required command: $cmd" >&2
    exit 1
  fi
done

api_base="${NVIDIA_AIR_API_BASE%/}"
simulation_url="$api_base/simulations/$SIMULATION_ID/"

verbose() {
  [ -n "$VERBOSE" ] && echo "[verbose] $*" >&2 || true
}

target_sleep_at="$(
  python3 -c "
from datetime import datetime, timedelta, timezone
import sys
try:
    t = datetime.now(timezone.utc) + timedelta(hours=$HOURS)
    print(t.replace(microsecond=0).isoformat().replace('+00:00', 'Z'))
except Exception as e:
    print(f'error: failed to compute target time: {e}', file=sys.stderr)
    sys.exit(1)
  "
)"

trap_expr='rm -f'
for f in payload before_body patch_body after_body; do
  declare "$f=$(mktemp)"
  trap_expr="$trap_expr \"\${$f}\""
done
trap "$trap_expr" EXIT

printf '{"sleep_at":"%s"}' "$target_sleep_at" > "$payload"

headers=(
  -H 'Accept: application/json'
  -H 'Content-Type: application/json'
  -H 'User-Agent: air-sdk/1.3.1'
  -H 'X-Air-Sdk-Version: 1.3.1'
  -H "Authorization: Bearer ${NVIDIA_AIR_API_KEY}"
)

should_retry() {
  case "$1" in
    000|408|409|425|429|500|502|503|504) return 0 ;;
    *) return 1 ;;
  esac
}

json_field() {
  python3 -c "
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as f:
        data = json.load(f)
    val = data.get(sys.argv[2])
    print(val if val is not None else '')
except Exception as e:
    print(f'error: failed to read json field {sys.argv[2]} from {sys.argv[1]}: {e}', file=sys.stderr)
    sys.exit(1)
  " "$1" "$2"
}

air_request() {
  local method="$1"
  local out="$2"
  local attempt code bytes sleep_seconds
  shift 2

  for attempt in 1 2 3 4 5; do
    : > "$out"
    if code="$(
      curl --ipv4 -sS --max-time 25 \
        --noproxy '*' \
        -o "$out" \
        -w '%{http_code}' \
        -X "$method" \
        "${headers[@]}" \
        "$@" \
        "$simulation_url"
    )"; then
      :
    else
      code="000"
    fi

    bytes=$(wc -c < "$out" | tr -d ' ')
    verbose "request method=$method attempt=$attempt http=$code bytes=$bytes"

    if [[ "$code" == "200" && -s "$out" ]]; then
      if python3 -c "import json; json.load(open('$out'))" 2>/dev/null; then
        return 0
      else
        verbose "response is not valid JSON, treating as failure"
      fi
    fi

    if [[ "$attempt" != "5" ]] && should_retry "$code"; then
      sleep_seconds=$((attempt * 10))
      verbose "retrying method=$method next_attempt=$((attempt + 1)) sleep=${sleep_seconds}s"
      sleep "$sleep_seconds"
      continue
    fi

    # last attempt or non-retryable code: show error
    local body_preview
    body_preview="$(head -c 240 "$out" | tr '\n' ' ')"
    echo "error: request failed method=$method http=$code after $((attempt)) attempt(s)" >&2
    [ -n "$body_preview" ] && echo "error: response body: $body_preview" >&2
    return 1
  done
}

air_patch() {
  local out="$1"
  local attempt code bytes sleep_seconds

  for attempt in 1 2 3 4 5; do
    : > "$out"
    if code="$(
      curl --ipv4 -sS --max-time 60 \
        --noproxy '*' \
        -o "$out" \
        -w '%{http_code}' \
        -X PATCH \
        "${headers[@]}" \
        --data @"$payload" \
        "$simulation_url"
    )"; then
      :
    else
      code="000"
    fi

    bytes=$(wc -c < "$out" | tr -d ' ')
    verbose "request method=PATCH attempt=$attempt http=$code bytes=$bytes"

    if [[ "$code" == "200" && -s "$out" ]]; then
      if python3 -c "import json; json.load(open('$out'))" 2>/dev/null; then
        return 0
      else
        verbose "response is not valid JSON, treating as failure"
      fi
    fi

    if [[ "$attempt" != "5" ]] && should_retry "$code"; then
      sleep_seconds=$((attempt * 10))
      verbose "retrying method=PATCH next_attempt=$((attempt + 1)) sleep=${sleep_seconds}s"
      sleep "$sleep_seconds"
      continue
    fi

    local body_preview
    body_preview="$(head -c 240 "$out" | tr '\n' ' ')"
    echo "error: request failed method=PATCH http=$code after $((attempt)) attempt(s)" >&2
    [ -n "$body_preview" ] && echo "error: response body: $body_preview" >&2
    return 1
  done
}

echo "info: simulation_id=$SIMULATION_ID target_sleep_at=$target_sleep_at"

echo "step: reading current sleep_at"
air_request GET "$before_body"
before_sleep_at="$(json_field "$before_body" sleep_at)"
echo "info: before_sleep_at=$before_sleep_at"

if [[ "$before_sleep_at" == "$target_sleep_at" ]]; then
  echo "info: sleep_at already set to target, skipping update"
  echo "ok"
  exit 0
fi

echo "step: updating sleep_at ($before_sleep_at -> $target_sleep_at)"
air_patch "$patch_body"

echo "step: verifying update"
air_request GET "$after_body"
after_sleep_at="$(json_field "$after_body" sleep_at)"
echo "info: after_sleep_at=$after_sleep_at"

if [[ "$after_sleep_at" != "$target_sleep_at" ]]; then
  echo "error: verify failed expected=$target_sleep_at actual=$after_sleep_at" >&2
  exit 1
fi

echo "ok"
