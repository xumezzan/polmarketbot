#!/usr/bin/env bash

set -u

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
PASS_COUNT=0
FAIL_COUNT=0

log_info() {
  printf '[INFO] %s\n' "$1"
}

log_pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '[PASS] %s\n' "$1"
}

log_fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf '[FAIL] %s\n' "$1"
}

request_json() {
  local url="$1"
  curl -fsS "$url"
}

check_json() {
  local description="$1"
  local json_payload="$2"
  local python_code="$3"

  if printf '%s' "$json_payload" | python3 -c "$python_code" 2>/dev/null; then
    log_pass "$description"
  else
    log_fail "$description"
  fi
}

run_sql() {
  local query="$1"
  docker compose exec -T db psql -U polymarket -d polymarket -t -A -c "$query" 2>/dev/null || true
}

log_info "API base: ${API_BASE_URL}"

if docker compose ps --services --status running | grep -qx 'api'; then
  log_pass "Docker service api is running"
else
  log_fail "Docker service api is not running"
fi

if docker compose ps --services --status running | grep -qx 'scheduler'; then
  log_pass "Docker service scheduler is running"
else
  log_fail "Docker service scheduler is not running"
fi

HEALTH_JSON="$(request_json "${API_BASE_URL}/health" 2>/dev/null || true)"
if [[ -n "$HEALTH_JSON" ]]; then
  check_json \
    "A. /health returns {status: ok}" \
    "$HEALTH_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("status")=="ok" else 1)'
else
  log_fail "A. /health endpoint is reachable"
fi

STATUS_JSON="$(request_json "${API_BASE_URL}/admin/status" 2>/dev/null || true)"
if [[ -n "$STATUS_JSON" ]]; then
  check_json \
    "B. /admin/status has api_alive=true" \
    "$STATUS_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("api_alive") is True else 1)'
  check_json \
    "B. /admin/status has recent scheduler cycle" \
    "$STATUS_JSON" \
    'import json,sys,datetime as dt; d=json.load(sys.stdin); v=d.get("last_scheduler_cycle_finished_at"); raise SystemExit(1 if not v else 0)'
  check_json \
    "B/C. /admin/status shows zero open positions after cleanup" \
    "$STATUS_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("open_positions_count")==0 else 1)'
else
  log_fail "B/C. /admin/status endpoint is reachable"
fi

POSITIONS_JSON="$(request_json "${API_BASE_URL}/admin/positions/open" 2>/dev/null || true)"
if [[ -n "$POSITIONS_JSON" ]]; then
  check_json \
    "C. /admin/positions/open returns count=0" \
    "$POSITIONS_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("count")==0 and d.get("items")==[] else 1)'
else
  log_fail "C. /admin/positions/open endpoint is reachable"
fi

PAPER_STATS_JSON="$(request_json "${API_BASE_URL}/admin/paper/stats" 2>/dev/null || true)"
if [[ -n "$PAPER_STATS_JSON" ]]; then
  check_json \
    "C. /admin/paper/stats reports open_positions=0" \
    "$PAPER_STATS_JSON" \
    'import json,sys; d=json.load(sys.stdin); s=d.get("stats") or {}; raise SystemExit(0 if s.get("open_positions")==0 else 1)'
  check_json \
    "C. /admin/paper/stats has no impossible trade totals" \
    "$PAPER_STATS_JSON" \
    'import json,sys; d=json.load(sys.stdin); s=d.get("stats") or {}; total=s.get("total_trades"); closed=s.get("closed_trades"); openp=s.get("open_positions"); ok=all(isinstance(x,(int,float)) for x in [total,closed,openp]) and total >= closed >= 0 and openp == 0; raise SystemExit(0 if ok else 1)'
else
  log_fail "C. /admin/paper/stats endpoint is reachable"
fi

OPEN_POSITIONS_DB="$(run_sql "select count(*) from positions where status = 'OPEN';")"
if [[ "$OPEN_POSITIONS_DB" == "0" ]]; then
  log_pass "C. DB has zero OPEN positions"
else
  log_fail "C. DB still has OPEN positions (count=${OPEN_POSITIONS_DB:-n/a})"
fi

OPEN_TRADES_DB="$(run_sql "select count(*) from paper_trades where status = 'OPEN';")"
if [[ "$OPEN_TRADES_DB" == "0" ]]; then
  log_pass "C. DB has zero OPEN paper_trades"
else
  log_fail "C. DB still has OPEN paper_trades (count=${OPEN_TRADES_DB:-n/a})"
fi

LEGACY_OPEN_DB="$(
  run_sql "select count(*) from positions where status = 'OPEN' and id in (2,3,4,5);"
)"
if [[ "$LEGACY_OPEN_DB" == "0" ]]; then
  log_pass "C. Legacy positions 2/3/4/5 are no longer OPEN"
else
  log_fail "C. Legacy positions 2/3/4/5 still have OPEN rows (count=${LEGACY_OPEN_DB:-n/a})"
fi

KILL_JSON="$(request_json "${API_BASE_URL}/admin/kill-switch/status" 2>/dev/null || true)"
if [[ -n "$KILL_JSON" ]]; then
  check_json \
    "D. /admin/kill-switch/status returns enabled flag" \
    "$KILL_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if isinstance(d.get("enabled"), bool) else 1)'
else
  log_fail "D. /admin/kill-switch/status endpoint is reachable"
fi

printf '\nSUMMARY: PASS=%d FAIL=%d\n' "$PASS_COUNT" "$FAIL_COUNT"
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
