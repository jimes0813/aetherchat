#!/usr/bin/env bash
# Fail if launch helpers unconditionally exec with --no-sandbox.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
failures=0

check_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  # Flag lines that exec ... --no-sandbox without being inside an opt-in branch.
  # Heuristic: bare `exec "$SOMETHING" --no-sandbox` that is not preceded by
  # want_no_sandbox / ALLOW_NO_SANDBOX in the same function is banned when the
  # script never mentions AETHERCHAT_ALLOW_NO_SANDBOX.
  if grep -qE 'exec[[:space:]].*--no-sandbox' "$file"; then
    if ! grep -q 'AETHERCHAT_ALLOW_NO_SANDBOX' "$file"; then
      echo "FAIL: $file embeds --no-sandbox without AETHERCHAT_ALLOW_NO_SANDBOX opt-in" >&2
      failures=$((failures + 1))
    fi
  fi
}

check_file "$ROOT/scripts/aetherchat-user-launch.sh"
check_file "$ROOT/frontend/linux/aetherchat"
# Desktop wrappers / templates
while IFS= read -r -d '' file; do
  check_file "$file"
done < <(find "$ROOT/scripts" "$ROOT/frontend/linux" -type f \( -name '*launch*' -o -name '*aetherchat*' -o -name '*fixed*' \) -print0 2>/dev/null || true)

if [[ "$failures" -ne 0 ]]; then
  exit 1
fi
echo "check-no-unconditional-no-sandbox: ok"
