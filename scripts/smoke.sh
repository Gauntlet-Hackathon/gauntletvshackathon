#!/usr/bin/env bash
# End-to-end smoke check (H10 offline -> reconnect, H7 rehearsals).
# Usage: scripts/smoke.sh [--no-docker]
#   FIELD_URL (default http://localhost:8000), CLOUD_URL (default http://localhost:4000)
#   --no-docker skips the cloud stop/start steps (for a deployed stack); the offline
#   steps are then skipped and the 5 extra requests are only checked for arrival.
set -euo pipefail

FIELD_URL="${FIELD_URL:-http://localhost:8000}"
CLOUD_URL="${CLOUD_URL:-http://localhost:4000}"
WAIT_SECS="${WAIT_SECS:-90}"
DOCKER=1
for arg in "$@"; do
  case "$arg" in
    --no-docker) DOCKER=0 ;;
    -h|--help) sed -n 2,6p "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
FAILED=0
RUN_ID="smoke-$(date +%s)-$$"
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILED=1; }
step() { # step "name" cmd...
  local name="$1"; shift
  if "$@"; then pass "$name"; else fail "$name"; fi
}
finish() {
  if [ "$FAILED" -eq 0 ]; then echo "PASS  smoke (all steps)"; exit 0; fi
  echo "FAIL  smoke (see FAIL lines above)"; exit 1
}
# Never leave the cloud stopped if we die midway.
CLOUD_STOPPED=0
trap '[ "$CLOUD_STOPPED" -eq 1 ] && docker compose start cloud >/dev/null 2>&1 || true' EXIT

http_ok() { curl -fsS -m 10 -o /dev/null "$1"; }

create_requests() { # create_requests label count -> appends ids to $IDS_FILE
  local label="$1" n="$2" i out id
  for i in $(seq 1 "$n"); do
    out="$(curl -fsS -m 60 -X POST "$FIELD_URL/requests" -H 'Content-Type: application/json' \
      -d "{\"raw_text\":\"$RUN_ID $label $i: family of 4 on roof, water rising, need boat\",\"channel\":\"form\"}")" || return 1
    id="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')" || return 1
    echo "$id" >>"$IDS_FILE"
  done
}

in_cloud() { # exactly one row for the id on the records page
  local count
  count="$(curl -fsS -m 20 "$CLOUD_URL/records" | grep -o "id=\"record-$1\"" | wc -l)"
  [ "$count" -eq 1 ]
}
all_in_cloud() { local id; while read -r id; do in_cloud "$id" || return 1; done <"$1"; }
wait_all_in_cloud() { # file
  local deadline=$((SECONDS + WAIT_SECS))
  while [ $SECONDS -lt $deadline ]; do
    if all_in_cloud "$1" 2>/dev/null; then return 0; fi
    sleep 3
  done
  return 1
}
backlog() { curl -fsS -m 10 "$FIELD_URL/sync/status" | python3 -c 'import json,sys; print(json.load(sys.stdin)["outbox_backlog"])'; }
backlog_at_least() { [ "$(backlog)" -ge 1 ]; }
no_dupes() { [ "$(sort "$1" | uniq -d | wc -l)" -eq 0 ] && [ "$(sort -u "$1" | wc -l)" -eq "$2" ]; }

IDS_A="$(mktemp)"; IDS_B="$(mktemp)"

# 1. health
step "field healthy ($FIELD_URL)" http_ok "$FIELD_URL/"
step "cloud healthy ($CLOUD_URL)" http_ok "$CLOUD_URL/health"
[ "$FAILED" -eq 0 ] || finish

# 2. three requests reach the cloud
IDS_FILE="$IDS_A"
step "create 3 requests" create_requests online 3
step "3 requests reach the cloud (<= ${WAIT_SECS}s)" wait_all_in_cloud "$IDS_A"
[ "$FAILED" -eq 0 ] || finish

IDS_FILE="$IDS_B"
if [ "$DOCKER" -eq 1 ]; then
  # 3. cloud down: requests queue in the outbox
  step "stop cloud" docker compose stop cloud
  CLOUD_STOPPED=1
  step "create 5 requests while cloud is down" create_requests offline 5
  sleep 2
  step "outbox holds queued events (backlog=$(backlog 2>/dev/null || echo '?'))" backlog_at_least
  # 4. cloud back: everything arrives once
  step "start cloud" docker compose start cloud
  CLOUD_STOPPED=0
else
  echo "SKIP  stop/start cloud and outbox check (--no-docker)"
  step "create 5 more requests" create_requests more 5
fi
step "5 requests arrive in the cloud (<= ${WAIT_SECS}s)" wait_all_in_cloud "$IDS_B"
cat "$IDS_A" "$IDS_B" >"$IDS_A.all"
step "no duplicates (8 unique ids)" no_dupes "$IDS_A.all" 8
rm -f "$IDS_A" "$IDS_B" "$IDS_A.all"
finish
