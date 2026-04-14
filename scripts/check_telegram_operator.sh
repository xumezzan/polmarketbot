#!/usr/bin/env bash

set -u

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
WAIT_CYCLE=false

if [[ "${1:-}" == "--wait-cycle" ]]; then
  WAIT_CYCLE=true
fi

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

request_json() {
  local url="$1"
  curl -fsS "$url"
}

post_json() {
  local url="$1"
  local payload="$2"
  curl -fsS -X POST "$url" -H 'Content-Type: application/json' -d "$payload"
}

extract_owner_chat_id() {
  local owner="${TELEGRAM_CHAT_ID:-}"
  if [[ -z "$owner" ]]; then
    owner="$(docker compose exec -T api sh -lc 'printf "%s" "${TELEGRAM_CHAT_ID:-}"' 2>/dev/null || true)"
  fi

  if [[ "$owner" =~ ^-?[0-9]+$ ]]; then
    printf '%s' "$owner"
    return 0
  fi

  # Fallback id for webhook smoke checks; may be treated as unauthorized in app logic.
  printf '1'
}

run_bot_text_dump() {
  docker compose exec -T api python - <<'PY'
import asyncio
from app.database import AsyncSessionLocal
from app.main import (
    _build_operator_service,
    _format_status_message,
    _format_recent_trades_message,
    _format_pnl_message,
)

async def main() -> None:
    async with AsyncSessionLocal() as session:
        status = await _build_operator_service(session).get_status()
        status_text = _format_status_message(status)
        trades_text = await _format_recent_trades_message(session=session)
        pnl_text = await _format_pnl_message(session=session)

    print("--- BOT STATUS TEXT ---")
    print(status_text)
    print()
    print("--- BOT TRADES TEXT ---")
    print(trades_text)
    print()
    print("--- BOT PNL TEXT ---")
    print(pnl_text)

asyncio.run(main())
PY
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
    "B. admin/status has api_alive=true" \
    "$STATUS_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("api_alive") is True else 1)'
  check_json \
    "B. admin/status has last scheduler cycle timestamp" \
    "$STATUS_JSON" \
    'import json,sys; d=json.load(sys.stdin); v=d.get("last_scheduler_cycle_finished_at"); raise SystemExit(0 if isinstance(v,str) and v else 1)'
  check_json \
    "C. admin/status exposes persistence counters" \
    "$STATUS_JSON" \
    'import json,sys; d=json.load(sys.stdin); keys=["news_items_count","analyses_count","signals_count","paper_trades_count"]; ok=all(isinstance(d.get(k),int) and d.get(k)>=0 for k in keys); raise SystemExit(0 if ok else 1)'
else
  log_fail "B/C. /admin/status endpoint is reachable"
fi

SIGNALS_JSON="$(request_json "${API_BASE_URL}/admin/signals/recent?limit=5" 2>/dev/null || true)"
if [[ -n "$SIGNALS_JSON" ]]; then
  check_json \
    "C. /admin/signals/recent returns count + items" \
    "$SIGNALS_JSON" \
    'import json,sys; d=json.load(sys.stdin); c=d.get("count"); items=d.get("items"); ok=isinstance(c,int) and isinstance(items,list) and c==len(items); raise SystemExit(0 if ok else 1)'
else
  log_fail "C. /admin/signals/recent endpoint is reachable"
fi

PAPER_STATS_JSON="$(request_json "${API_BASE_URL}/admin/paper/stats" 2>/dev/null || true)"
if [[ -n "$PAPER_STATS_JSON" ]]; then
  check_json \
    "C. /admin/paper/stats returns stats payload" \
    "$PAPER_STATS_JSON" \
    'import json,sys; d=json.load(sys.stdin); s=d.get("stats") or {}; keys=["total_trades","closed_trades","open_positions","win_rate","total_pnl"]; ok=all(k in s for k in keys); raise SystemExit(0 if ok else 1)'
else
  log_fail "C. /admin/paper/stats endpoint is reachable"
fi

KILL_JSON="$(request_json "${API_BASE_URL}/admin/kill-switch/status" 2>/dev/null || true)"
if [[ -n "$KILL_JSON" ]]; then
  check_json \
    "B/C. /admin/kill-switch/status returns enabled flag" \
    "$KILL_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if isinstance(d.get("enabled"),bool) else 1)'
else
  log_fail "B/C. /admin/kill-switch/status endpoint is reachable"
fi

OWNER_CHAT_ID="$(extract_owner_chat_id)"
log_info "Webhook smoke test chat_id=${OWNER_CHAT_ID}"

WEBHOOK_START_JSON="$(
  post_json \
    "${API_BASE_URL}/telegram/webhook" \
    "{\"update_id\":9001,\"message\":{\"message_id\":1,\"chat\":{\"id\":${OWNER_CHAT_ID}},\"text\":\"/start\"}}" \
    2>/dev/null || true
)"
if [[ -n "$WEBHOOK_START_JSON" ]]; then
  check_json \
    "B. /telegram/webhook accepts /start update" \
    "$WEBHOOK_START_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("ok") is True else 1)'
else
  log_fail "B. /telegram/webhook /start request failed"
fi

WEBHOOK_STATUS_JSON="$(
  post_json \
    "${API_BASE_URL}/telegram/webhook" \
    "{\"update_id\":9002,\"callback_query\":{\"id\":\"cb-status\",\"from\":{\"id\":${OWNER_CHAT_ID}},\"data\":\"status\",\"message\":{\"message_id\":2,\"chat\":{\"id\":${OWNER_CHAT_ID}}}}}" \
    2>/dev/null || true
)"
if [[ -n "$WEBHOOK_STATUS_JSON" ]]; then
  check_json \
    "B. /telegram/webhook accepts callback update" \
    "$WEBHOOK_STATUS_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("ok") is True else 1)'
else
  log_fail "B. /telegram/webhook callback request failed"
fi

BOT_TEXT_OUTPUT="$(run_bot_text_dump 2>/dev/null || true)"
if [[ -n "$BOT_TEXT_OUTPUT" ]]; then
  if printf '%s' "$BOT_TEXT_OUTPUT" | grep -q 'BOT STATUS TEXT' && \
     printf '%s' "$BOT_TEXT_OUTPUT" | grep -q 'BOT TRADES TEXT' && \
     printf '%s' "$BOT_TEXT_OUTPUT" | grep -q 'BOT PNL TEXT'; then
    log_pass "Telegram bot formatted responses are generated"
    printf '%s\n' "$BOT_TEXT_OUTPUT"
  else
    log_fail "Telegram bot formatted responses validation failed"
  fi
else
  log_fail "Could not render Telegram bot formatted responses"
fi

if [[ "$WAIT_CYCLE" == true ]]; then
  BEFORE_FINISHED_AT="$(printf '%s' "$STATUS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("last_scheduler_cycle_finished_at") or "")' 2>/dev/null || true)"
  INTERVAL_MIN="$(docker compose exec -T api sh -lc 'printf "%s" "${SCHEDULER_INTERVAL_MINUTES:-15}"' 2>/dev/null || true)"
  if ! [[ "$INTERVAL_MIN" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    INTERVAL_MIN="15"
  fi

  SLEEP_SECONDS="$(python3 -c "print(int(float('${INTERVAL_MIN}')*60 + 20))")"
  log_info "D. waiting ${SLEEP_SECONDS}s to verify scheduler automation..."
  sleep "$SLEEP_SECONDS"

  STATUS_AFTER_JSON="$(request_json "${API_BASE_URL}/admin/status" 2>/dev/null || true)"
  AFTER_FINISHED_AT="$(printf '%s' "$STATUS_AFTER_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("last_scheduler_cycle_finished_at") or "")' 2>/dev/null || true)"

  if [[ -n "$BEFORE_FINISHED_AT" && -n "$AFTER_FINISHED_AT" && "$BEFORE_FINISHED_AT" != "$AFTER_FINISHED_AT" ]]; then
    log_pass "D. scheduler advanced automatically (last cycle timestamp changed)"
  else
    log_fail "D. scheduler did not advance within expected interval"
  fi
else
  log_info "D. automation deep check skipped (run with --wait-cycle)."
fi

printf '\nSUMMARY: PASS=%d FAIL=%d\n' "$PASS_COUNT" "$FAIL_COUNT"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
