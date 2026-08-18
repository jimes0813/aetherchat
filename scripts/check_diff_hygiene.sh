#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Cover unstaged and staged local work first.
git diff --check
git diff --cached --check

# CI supplies the PR base or the previous push SHA. A standalone local run
# checks the current commit as well, so committed whitespace cannot hide behind
# a clean worktree.
base="${AETHERCHAT_DIFF_BASE:-}"
zero_sha="0000000000000000000000000000000000000000"
if [[ -n "$base" && "$base" != "$zero_sha" ]] && git cat-file -e "$base^{commit}" 2>/dev/null; then
  git diff --check "$base...HEAD"
elif git rev-parse --verify HEAD >/dev/null 2>&1; then
  git show --check --format= HEAD
fi
