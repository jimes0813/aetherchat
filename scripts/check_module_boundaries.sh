#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
failed=0

check_max_lines() {
  local relative_path="$1"
  local maximum="$2"
  local actual
  actual="$(wc -l < "$ROOT/$relative_path")"
  if (( actual > maximum )); then
    printf 'Module boundary exceeded: %s has %s lines (maximum %s)\n' \
      "$relative_path" "$actual" "$maximum" >&2
    failed=1
  else
    printf 'Module boundary OK: %s %s/%s lines\n' "$relative_path" "$actual" "$maximum"
  fi
}

# Product boundary: App is an orchestration shell, not a feature implementation.
check_max_lines frontend/src/App.tsx 800

# Staged anti-regression caps. Inspector was reduced from 2,308 lines by moving
# desktop control and plugin/ComfyUI ownership into independent modules.
check_max_lines frontend/src/components/InspectorPanel.tsx 1700
check_max_lines frontend/src/components/DesktopControlPanel.tsx 300
check_max_lines frontend/src/components/PluginCenter.tsx 550

# Existing extracted feature modules may not silently grow back into monoliths.
check_max_lines frontend/src/components/RunPanel.tsx 1050
check_max_lines frontend/src/components/SkillsRulesPanel.tsx 1000

# Backend service modules have their own budget script so the two trees
# can evolve independently. Failures here still fail this combined gate.
if ! bash "$ROOT/scripts/check_backend_module_boundaries.sh"; then
  failed=1
fi

exit "$failed"
