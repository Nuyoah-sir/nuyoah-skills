#!/usr/bin/env bash
# =====================================================================
# test.sh — 黑盒测试（由 multi-turn-eval skill Phase 3 自动生成）
# 从 spec 提取 API/错误码/状态迁移 生成
# 退出码 0 = 全过，非 0 = 有失败
# =====================================================================
set -euo pipefail

BASE_URL="${BASE_URL:-{{BASE_URL}}}"
ADMIN_TOKEN="${ADMIN_TOKEN:-{{ADMIN_TOKEN_VALUE}}}"
WAIT_SEC="${WAIT_SEC:-5}"

PASSED=0; FAILED=0; FAIL_NAMES=()
AUTH_TOKEN=""
AUTH_FALLBACK_COUNT=0

# --- Colors ---
if [[ -t 1 ]]; then
    C_G=$'\033[0;32m'; C_R=$'\033[0;31m'; C_B=$'\033[0;34m'; C_Z=$'\033[0m'
else
    C_G=""; C_R=""; C_B=""; C_Z=""
fi

PASS() { echo "${C_G}[PASS]${C_Z} $*"; PASSED=$((PASSED+1)); }
FAIL() { echo "${C_R}[FAIL]${C_Z} $*"; FAILED=$((FAILED+1)); FAIL_NAMES+=("$1"); }
INFO() { echo "${C_B}[INFO]${C_Z} $*"; }

# --- Utilities ---

gen_uuid() {
    python3 -c "import uuid;print(uuid.uuid4())" 2>/dev/null \
    || python -c "import uuid;print(uuid.uuid4())" 2>/dev/null \
    || uuidgen 2>/dev/null | tr '[:upper:]' '[:lower:]' \
    || echo "00000000-0000-4000-8000-$(date +%s)000"
}

utc_now() {
    python3 -c "from datetime import datetime,timezone;print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))" 2>/dev/null \
    || date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null
}

utc_offset() {
    local offset_sec="$1"
    python3 -c "
from datetime import datetime,timezone,timedelta
dt = datetime.now(timezone.utc) + timedelta(seconds=$offset_sec)
print(dt.strftime('%Y-%m-%dT%H:%M:%SZ'))
" 2>/dev/null || date -u -d "$1 seconds" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null
}

# NOTE: jq returns lowercase JSON booleans (true/false), NOT Python-style True/False.
#       Use "true"/"false" in assert_eq comparisons.
#       Use bracket notation for numeric array indices: .items[0], NOT .items.0
json_get() {
    local j="$1" p="$2"
    if command -v jq >/dev/null 2>&1; then
        echo "$j" | jq -r "$p // empty" 2>/dev/null || true
    else
        echo "$j" | python3 -c "
import sys,json
d=json.load(sys.stdin)
path='$p'.lstrip('.').split('.')
for k in path:
    if isinstance(d,dict) and k in d:
        d=d[k]
    else:
        d=''
        break
if d is None:
    d=''
print(d)
" 2>/dev/null || echo ""
    fi
}

http() {
    local method="$1" path="$2"; shift 2
    local tmp; tmp=$(mktemp)
    CURL_CODE=$(curl -s -o "$tmp" -w "%{http_code}" "$@" -X "$method" "${BASE_URL}${path}" 2>/dev/null || echo "000")
    HTTP_BODY=$(cat "$tmp" 2>/dev/null || echo "")
    rm -f "$tmp"
}

http_auth() {
    local method="$1" path="$2" token="$3"; shift 3
    local tmp; tmp=$(mktemp)
    CURL_CODE=$(curl -s -o "$tmp" -w "%{http_code}" -H "Authorization: Bearer $token" "$@" -X "$method" "${BASE_URL}${path}" 2>/dev/null || echo "000")
    HTTP_BODY=$(cat "$tmp" 2>/dev/null || echo "")
    rm -f "$tmp"
}

assert_eq() {
    if [[ "$2" == "$3" ]]; then PASS "$1"; else FAIL "$1 (expected='$2', actual='$3')"; fi
}
assert_status() {
    if [[ "$2" == "$3" ]]; then PASS "$1 (HTTP $3)"; else FAIL "$1 (expected HTTP $2, got $3)"; fi
}
assert_contains() {
    if [[ "$3" == *"$2"* ]]; then PASS "$1"; else FAIL "$1 (expected to contain '$2', got '$3')"; fi
}

{{API_WRAPPERS}}

wait_consume() { sleep "$WAIT_SEC"; }

# === Auth header detection ===
# Models may use different auth headers: Authorization: Bearer, X-Token, X-Username, etc.
# Probe: try token against a known endpoint with each header format, pick the one that works.
AUTH_HEADER=""
AUTH_TOKEN=""
PROBE_PATH="{{HEALTH_PATH}}"   # used only for header detection

detect_auth_header() {
    local token="$1"
    local probe_path="${2:-$PROBE_PATH}"

    # Candidate header formats (most common first)
    local candidates=(
        "Authorization: Bearer"
        "X-Token"
        "X-Username"
        "X-Auth-Token"
        "Authorization: Token"
        "token"
    )

    for hdr in "${candidates[@]}"; do
        local tmp; tmp=$(mktemp)
        local code
        code=$(curl -s -o "$tmp" -w "%{http_code}" -H "$hdr: $token" -X GET "${BASE_URL}${probe_path}" 2>/dev/null || echo "000")
        rm -f "$tmp"
        if echo "$code" | grep -qE "^(200|404)$"; then
            AUTH_HEADER="$hdr"
            INFO "Auth header detected: '$hdr' (probe HTTP $code on $probe_path)"
            return 0
        fi
        # Also accept 403 (valid auth but insufficient role) as proof header works
        if [ "$code" = "403" ]; then
            AUTH_HEADER="$hdr"
            INFO "Auth header detected: '$hdr' (probe HTTP 403 — valid auth, insufficient role)"
            return 0
        fi
    done

    INFO "Auth header detection failed — none of ${candidates[*]} worked"
    return 1
}

# === Auth bootstrap: register+login to get a token, then detect header format ===
bootstrap_auth() {
    local test_user="eval_fallback_$(date +%s)"
    local test_pass="eval_pass_123"
    local reg_ok=false

    # Step 1: Try register
    http POST "/api/auth/register" -H "Content-Type: application/json" \
        -d "{\"username\":\"$test_user\",\"password\":\"$test_pass\"}"
    if echo "$CURL_CODE" | grep -qE "^(200|201)$"; then
        reg_ok=true
        AUTH_TOKEN=$(json_get "$HTTP_BODY" ".data.token")
        if [ -n "$AUTH_TOKEN" ] && [ "$AUTH_TOKEN" != "null" ]; then
            detect_auth_header "$AUTH_TOKEN" && return 0
        fi
    fi

    # Step 2: Register succeeded but no token → login with registered user
    if [ "$reg_ok" = true ]; then
        http POST "/api/auth/login" -H "Content-Type: application/json" \
            -d "{\"username\":\"$test_user\",\"password\":\"$test_pass\"}"
        AUTH_TOKEN=$(json_get "$HTTP_BODY" ".data.token")
        if [ -n "$AUTH_TOKEN" ] && [ "$AUTH_TOKEN" != "null" ]; then
            detect_auth_header "$AUTH_TOKEN" && return 0
        fi
    fi

    # Step 3: Try default admin
    http POST "/api/auth/login" -H "Content-Type: application/json" \
        -d '{"username":"admin","password":"admin"}'
    AUTH_TOKEN=$(json_get "$HTTP_BODY" ".data.token")
    if [ -n "$AUTH_TOKEN" ] && [ "$AUTH_TOKEN" != "null" ]; then
        detect_auth_header "$AUTH_TOKEN" && return 0
    fi

    AUTH_TOKEN=""
    INFO "Auth bootstrap: no token obtained (auth may not be implemented)"
    return 1
}

# === http_or_auth: try without auth first; if 401/403, retry with detected header ===
USED_AUTH=0
http_or_auth() {
    local method="$1" path="$2"; shift 2

    # Attempt 1: no auth
    http "$method" "$path" "$@"
    local code1="$CURL_CODE"

    # If not 401/403, or no token/header available, return as-is
    if ! echo "$code1" | grep -qE "^(401|403)$" || [ -z "$AUTH_TOKEN" ] || [ -z "$AUTH_HEADER" ]; then
        USED_AUTH=0
        return 0
    fi

    # Attempt 2: with detected auth header and token
    INFO "Auth fallback: $method $path → retrying with '$AUTH_HEADER'"
    local tmp; tmp=$(mktemp)
    CURL_CODE=$(curl -s -o "$tmp" -w "%{http_code}" -H "$AUTH_HEADER: $AUTH_TOKEN" "$@" -X "$method" "${BASE_URL}${path}" 2>/dev/null || echo "000")
    HTTP_BODY=$(cat "$tmp" 2>/dev/null || echo "")
    rm -f "$tmp"
    USED_AUTH=1
    AUTH_FALLBACK_COUNT=$((AUTH_FALLBACK_COUNT + 1))
}

# =====================================================================
# Pre-check
# =====================================================================
echo "====================================================================="
echo "  Multi-Turn Eval — Black-box Test Suite"
echo "  Target: $BASE_URL"
echo "====================================================================="
echo ""

# Accept 200 (open), 401/403 (auth required), 404 (not found but service alive)
INFO "Waiting for service..."
for i in $(seq 1 30); do
    http GET "{{HEALTH_PATH}}"
    if echo "$CURL_CODE" | grep -qE "^(200|401|403|404)$"; then
        INFO "Service reachable at $BASE_URL"
        echo "Service HTTP: $CURL_CODE"
        break
    fi
    [ "$i" -eq 30 ] && { echo "${C_R}FATAL: Service not reachable at $BASE_URL${C_Z}"; exit 2; }
    sleep 1
done
echo ""

# === Auth bootstrap for fallback testing ===
bootstrap_auth
echo ""

# =====================================================================
{{TEST_CASES}}
# =====================================================================

# --- Summary ---
echo ""
echo "====================================================================="
echo "  Results: ${C_G}$PASSED passed${C_Z}, ${C_R}$FAILED failed${C_Z}"
echo "  Total: $((PASSED + FAILED)) assertions"
if [ "$AUTH_FALLBACK_COUNT" -gt 0 ]; then
    echo "  ${C_B}Auth fallback used: $AUTH_FALLBACK_COUNT test(s)${C_Z} — model requires auth on spec-public endpoints"
fi
echo "====================================================================="

if [[ $FAILED -gt 0 ]]; then
    echo ""
    echo "Failed tests:"
    for n in "${FAIL_NAMES[@]}"; do
        echo "  ${C_R}- $n${C_Z}"
    done
    exit 1
fi

echo ""
echo "${C_G}All tests passed.${C_Z}"
exit 0
