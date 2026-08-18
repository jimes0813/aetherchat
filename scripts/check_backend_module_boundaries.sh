#!/usr/bin/env bash
# Line-budget gate for backend service modules.
# Default cap is 2000 lines. Oversized legacy files are grandfathered at their
# current ceiling and must not grow; new files may not exceed the default.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICES="$ROOT/backend/app/services"
DEFAULT_MAX=2000
failed=0

# Grandfathered ceilings: shrink these when further splits land, never raise them.
declare -A LEGACY_MAX=(
  [run.py]=2100
)

check_max_lines() {
  local relative_path="$1"
  local maximum="$2"
  local actual
  actual="$(wc -l < "$ROOT/$relative_path")"
  if (( actual > maximum )); then
    printf 'Backend module boundary exceeded: %s has %s lines (maximum %s)\n' \
      "$relative_path" "$actual" "$maximum" >&2
    failed=1
  else
    printf 'Backend module boundary OK: %s %s/%s lines\n' \
      "$relative_path" "$actual" "$maximum"
  fi
}

shopt -s nullglob
for path in "$SERVICES"/*.py; do
  name="$(basename "$path")"
  [[ "$name" == "__init__.py" ]] && continue
  maximum="${LEGACY_MAX[$name]:-$DEFAULT_MAX}"
  check_max_lines "backend/app/services/$name" "$maximum"
done

# Split package modules inherit the default budget.
if [[ -d "$SERVICES/db" ]]; then
  for path in "$SERVICES/db"/*.py; do
    name="$(basename "$path")"
    [[ "$name" == "__init__.py" ]] && continue
    check_max_lines "backend/app/services/db/$name" "$DEFAULT_MAX"
  done
fi
if [[ -d "$SERVICES/orchestrator" ]]; then
  for path in "$SERVICES/orchestrator"/*.py; do
    name="$(basename "$path")"
    [[ "$name" == "__init__.py" ]] && continue
    maximum="${LEGACY_MAX[$name]:-$DEFAULT_MAX}"
    check_max_lines "backend/app/services/orchestrator/$name" "$maximum"
  done
fi

exit "$failed"
