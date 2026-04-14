#!/usr/bin/env bash

set -u

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

read_from_api_env() {
  local var_name="$1"
  docker compose exec -T api sh -lc "printf '%s' \"\${${var_name}:-}\"" 2>/dev/null || true
}

extract_json_field() {
  local json_payload="$1"
  local python_code="$2"
  printf '%s' "$json_payload" | python3 -c "$python_code" 2>/dev/null || true
}

check_json_predicate() {
  local description="$1"
  local json_payload="$2"
  local python_code="$3"
  if printf '%s' "$json_payload" | python3 -c "$python_code" 2>/dev/null; then
    log_pass "$description"
  else
    log_fail "$description"
  fi
}

TELEGRAM_API_BASE_URL="${TELEGRAM_API_BASE_URL:-}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_WEBHOOK_URL="${TELEGRAM_WEBHOOK_URL:-}"

if [[ -z "$TELEGRAM_API_BASE_URL" ]]; then
  TELEGRAM_API_BASE_URL="$(read_from_api_env TELEGRAM_API_BASE_URL)"
fi
if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
  TELEGRAM_BOT_TOKEN="$(read_from_api_env TELEGRAM_BOT_TOKEN)"
fi
if [[ -z "$TELEGRAM_WEBHOOK_URL" ]]; then
  TELEGRAM_WEBHOOK_URL="$(read_from_api_env TELEGRAM_WEBHOOK_URL)"
fi

if [[ -z "$TELEGRAM_API_BASE_URL" ]]; then
  TELEGRAM_API_BASE_URL="https://api.telegram.org"
fi

if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
  log_fail "TELEGRAM_BOT_TOKEN is empty (set it in .env or api container env)"
  printf '\nSUMMARY: PASS=%d FAIL=%d\n' "$PASS_COUNT" "$FAIL_COUNT"
  exit 1
fi

if [[ -z "$TELEGRAM_WEBHOOK_URL" ]]; then
  log_fail "TELEGRAM_WEBHOOK_URL is empty (set public https URL in .env)"
  printf '\nSUMMARY: PASS=%d FAIL=%d\n' "$PASS_COUNT" "$FAIL_COUNT"
  exit 1
fi

TOKEN_PREFIX="${TELEGRAM_BOT_TOKEN:0:8}"
log_info "Telegram API base: ${TELEGRAM_API_BASE_URL}"
log_info "Token prefix: ${TOKEN_PREFIX}***"
log_info "Webhook URL: ${TELEGRAM_WEBHOOK_URL}"

GET_ME_JSON="$(curl -fsS "${TELEGRAM_API_BASE_URL%/}/bot${TELEGRAM_BOT_TOKEN}/getMe" 2>/dev/null || true)"
if [[ -z "$GET_ME_JSON" ]]; then
  log_fail "Telegram getMe request failed"
else
  check_json_predicate \
    "Telegram token is valid (getMe ok=true)" \
    "$GET_ME_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("ok") is True else 1)'
  BOT_USERNAME="$(extract_json_field "$GET_ME_JSON" 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or {}).get("username",""))')"
  if [[ -n "$BOT_USERNAME" ]]; then
    log_info "Bot username: @${BOT_USERNAME}"
  fi
fi

SET_WEBHOOK_JSON="$(
  curl -fsS -X POST \
    "${TELEGRAM_API_BASE_URL%/}/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
    --data-urlencode "url=${TELEGRAM_WEBHOOK_URL}" \
    2>/dev/null || true
)"
if [[ -z "$SET_WEBHOOK_JSON" ]]; then
  log_fail "Telegram setWebhook request failed"
else
  check_json_predicate \
    "setWebhook ok=true" \
    "$SET_WEBHOOK_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("ok") is True else 1)'
fi

WEBHOOK_INFO_JSON="$(curl -fsS "${TELEGRAM_API_BASE_URL%/}/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" 2>/dev/null || true)"
if [[ -z "$WEBHOOK_INFO_JSON" ]]; then
  log_fail "Telegram getWebhookInfo request failed"
else
  check_json_predicate \
    "getWebhookInfo ok=true" \
    "$WEBHOOK_INFO_JSON" \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("ok") is True else 1)'
  check_json_predicate \
    "Webhook URL matches TELEGRAM_WEBHOOK_URL" \
    "$WEBHOOK_INFO_JSON" \
    "import json,sys; d=json.load(sys.stdin); url=(d.get('result') or {}).get('url'); raise SystemExit(0 if url=='${TELEGRAM_WEBHOOK_URL}' else 1)"

  PENDING_UPDATES="$(extract_json_field "$WEBHOOK_INFO_JSON" 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or {}).get("pending_update_count",0))')"
  LAST_ERROR_MESSAGE="$(extract_json_field "$WEBHOOK_INFO_JSON" 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or {}).get("last_error_message",""))')"
  LAST_ERROR_DATE="$(extract_json_field "$WEBHOOK_INFO_JSON" 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or {}).get("last_error_date",""))')"
  log_info "Pending updates: ${PENDING_UPDATES}"

  if [[ -n "$LAST_ERROR_MESSAGE" ]]; then
    log_fail "Webhook has last_error_message: ${LAST_ERROR_MESSAGE} (last_error_date=${LAST_ERROR_DATE})"
  else
    log_pass "Webhook has no last_error_message"
  fi
fi

printf '\nSUMMARY: PASS=%d FAIL=%d\n' "$PASS_COUNT" "$FAIL_COUNT"
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
