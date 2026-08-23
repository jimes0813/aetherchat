#!/usr/bin/env bash
set -euo pipefail

readonly APP=/usr/bin/aetherchat
readonly SANDBOX=/opt/AetherChat/chrome-sandbox
readonly DESKTOP=/usr/share/applications/aetherchat.desktop
readonly API_PORT="${AETHERCHAT_SMOKE_PORT:-18765}"
readonly DEBUG_PORT="${AETHERCHAT_SMOKE_DEBUG_PORT:-19223}"

test "$(stat -c '%U:%G %a' "$SANDBOX")" = 'root:root 4755'
desktop-file-validate "$DESKTOP"
grep -F 'Actions=NewWindow;QuickEntry;' "$DESKTOP" >/dev/null
grep -F 'StartupNotify=true' "$DESKTOP" >/dev/null

smoke_root="$(mktemp -d -t aetherchat-installed-smoke.XXXXXX)"
app_pid=''
cleanup() {
  if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
    kill "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
  fi
  rm -rf "$smoke_root"
}
trap cleanup EXIT

env \
  AETHERCHAT_ALLOW_UNAUTHENTICATED=0 \
  AETHERCHAT_DATA_DIR="$smoke_root/data" \
  AETHERCHAT_PORT="$API_PORT" \
  "$APP" --user-data-dir="$smoke_root/electron" --remote-debugging-port="$DEBUG_PORT" \
  >"$smoke_root/app.log" 2>&1 &
app_pid=$!

ready=false
for _attempt in $(seq 1 60); do
  if curl -fsS --max-time 1 "http://127.0.0.1:$API_PORT/health/ready" >/dev/null; then
    ready=true
    break
  fi
  if ! kill -0 "$app_pid" 2>/dev/null; then
    cat "$smoke_root/app.log" >&2
    exit 1
  fi
  sleep 1
done
[[ "$ready" == true ]] || { cat "$smoke_root/app.log" >&2; exit 1; }

test "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$API_PORT/sessions/")" = 401
curl -fsS "http://127.0.0.1:$DEBUG_PORT/json/list" | grep -F 'AetherChat' >/dev/null

descendants() {
  local parent="$1"
  local child
  while read -r child; do
    [[ -n "$child" ]] || continue
    printf '%s\n' "$child"
    descendants "$child"
  done < <(pgrep -P "$parent" 2>/dev/null || true)
}

mapfile -t children < <(descendants "$app_pid")
for pid in "$app_pid" "${children[@]}"; do
  args="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$args" != *'--no-sandbox'* ]]
done
renderer_pid=''
for pid in "${children[@]}"; do
  args="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  if [[ "$args" == *'--type=renderer'* ]]; then renderer_pid="$pid"; break; fi
done
[[ -n "$renderer_pid" ]]
grep -F $'NoNewPrivs:\t1' "/proc/$renderer_pid/status" >/dev/null
grep -F $'Seccomp:\t2' "/proc/$renderer_pid/status" >/dev/null
python3 "$(dirname -- "$0")/measure_aetherchat_memory.py" \
  --root-pid "$app_pid" \
  --max-pss-mib 500 \
  --output "$smoke_root/memory.json"

timeout 5 "$APP" \
  --user-data-dir="$smoke_root/electron" \
  'aetherchat://chat?prompt=installed-smoke&mode=casual' \
  >/dev/null 2>&1
echo 'Installed Linux smoke passed: window/CDP, backend readiness, auth 401, deep link, setuid sandbox, NoNewPrivs and Seccomp.'
