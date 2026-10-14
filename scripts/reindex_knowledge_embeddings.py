#!/usr/bin/env python3
"""Rebuild AetherChat knowledge vectors (network embeddings with local-hash fallback).

Usage:
  AETHERCHAT_DATA_DIR=~/.config/aetherchat-frontend/backend-data \\
    python scripts/reindex_knowledge_embeddings.py

  AETHERCHAT_EMBEDDING_PROVIDER=local-hash python scripts/reindex_knowledge_embeddings.py --force-local
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.environ.get("AETHERCHAT_DATA_DIR"))
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--force-local", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["AETHERCHAT_DATA_DIR"] = str(Path(args.data_dir).expanduser())
    elif "AETHERCHAT_DATA_DIR" not in os.environ:
        packaged = Path.home() / ".config" / "aetherchat-frontend" / "backend-data"
        if (packaged / "knowledge.sqlite3").is_file():
            os.environ["AETHERCHAT_DATA_DIR"] = str(packaged)

    from app.services.knowledge_service import KnowledgeService

    service = KnowledgeService()
    if args.status_only:
        print(json.dumps(service.embedding_status(), ensure_ascii=False, indent=2))
        return 0
    result = service.reindex_vectors(args.session_id, force_local=args.force_local)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result.get("awaiting_network") else 2


if __name__ == "__main__":
    raise SystemExit(main())
