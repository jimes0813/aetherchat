from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="aetherchat-tests-"))
os.environ["AETHERCHAT_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["AETHERCHAT_ALLOW_UNAUTHENTICATED"] = "1"
for key_name in (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "XAI_API_KEY",
    "QWEN_API_KEY",
    "KIMI_API_KEY",
):
    os.environ.pop(key_name, None)


@atexit.register
def _cleanup_test_data():
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


from app import main as main_module  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_rate_limit_store(monkeypatch):
    monkeypatch.setattr(main_module, "redis_client", None)
    main_module.rate_limit_store.clear()
    yield
    main_module.rate_limit_store.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
