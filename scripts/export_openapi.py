#!/usr/bin/env python3
"""Export FastAPI OpenAPI schema for frontend type generation.

Usage (from repo root):
  .venv-linux/bin/python scripts/export_openapi.py
  npm --prefix frontend run generate:api
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUT = ROOT / "frontend" / "openapi" / "openapi.json"


def main() -> int:
    sys.path.insert(0, str(BACKEND))
    from app.main import app

    schema = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths = len(schema.get("paths") or {})
    print(f"wrote {OUT} ({paths} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
