#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${AETHERCHAT_QUALITY_VENV:-$ROOT/.venv-linux}"
BOOTSTRAP=false

usage() {
  printf 'Usage: %s [--bootstrap]\n' "${0##*/}"
  printf '  --bootstrap  recreate the selected Python venv and run npm ci first\n'
}

case "${1:-}" in
  "") ;;
  --bootstrap) BOOTSTRAP=true ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if [[ "$BOOTSTRAP" == "true" ]]; then
  python3 -m venv --clear "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check \
    --requirement "$ROOT/backend/requirements.txt" \
    --requirement "$ROOT/backend/requirements-dev.txt"
  (cd "$ROOT/frontend" && npm ci)
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  printf 'Missing Python environment: %s (run with --bootstrap)\n' "$VENV_DIR" >&2
  exit 1
fi

"$ROOT/scripts/check_diff_hygiene.sh"
bash "$ROOT/scripts/check_module_boundaries.sh"
"$VENV_DIR/bin/ruff" check --config "$ROOT/backend/pyproject.toml" \
  "$ROOT/backend/app" "$ROOT/tests" "$ROOT/scripts"
(cd "$ROOT/backend" && "$VENV_DIR/bin/mypy")
env PYTHONPATH="$ROOT" "$VENV_DIR/bin/pytest" -q "$ROOT/tests"

(
  cd "$ROOT/frontend"
  npm run check:i18n
  npm run test:electron
  npm run test:unit
  npm run build
  npm run test:e2e
)

"$ROOT/scripts/check_diff_hygiene.sh"
