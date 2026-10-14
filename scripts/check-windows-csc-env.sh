#!/usr/bin/env bash
# Fail Windows release builds when Authenticode CSC secrets are missing.
# Skip when not targeting Windows (default) or when AETHERCHAT_SKIP_WINDOWS_CSC_CHECK=1.
set -euo pipefail

target="${1:-${AETHERCHAT_BUILD_TARGET:-}}"
platform="${AETHERCHAT_BUILD_PLATFORM:-}"

is_windows=0
case "${target,,}" in
  *win*|*windows*) is_windows=1 ;;
esac
case "${platform,,}" in
  win|windows|win32) is_windows=1 ;;
esac
# electron-builder style: npm run dist:win / --win
if [[ "${npm_lifecycle_event:-}" == *win* ]] || [[ " $* " == *" --win "* ]]; then
  is_windows=1
fi
if [[ "${AETHERCHAT_REQUIRE_WINDOWS_CSC:-}" == "1" ]]; then
  is_windows=1
fi

if [[ "${AETHERCHAT_SKIP_WINDOWS_CSC_CHECK:-}" == "1" ]]; then
  echo "check-windows-csc-env: skipped (AETHERCHAT_SKIP_WINDOWS_CSC_CHECK=1)"
  exit 0
fi

if [[ "$is_windows" -ne 1 ]]; then
  echo "check-windows-csc-env: not a Windows target; ok"
  exit 0
fi

missing=()
[[ -n "${WINDOWS_CSC_LINK:-}" ]] || missing+=("WINDOWS_CSC_LINK")
[[ -n "${WINDOWS_CSC_KEY_PASSWORD:-}" ]] || missing+=("WINDOWS_CSC_KEY_PASSWORD")

if [[ "${#missing[@]}" -ne 0 ]]; then
  printf 'check-windows-csc-env: public Windows release blocked; missing %s\n' "${missing[*]}" >&2
  printf 'public_1.0 / stable Windows builds require WINDOWS_CSC_LINK and WINDOWS_CSC_KEY_PASSWORD.\n' >&2
  printf 'See docs/PUBLIC_RC_CHECKLIST.md and frontend/WINDOWS_PACKAGING.md.\n' >&2
  exit 1
fi

echo "check-windows-csc-env: WINDOWS_CSC_* present"
