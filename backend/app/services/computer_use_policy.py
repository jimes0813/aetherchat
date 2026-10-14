"""Computer Use capability gating (default OFF).

Enterprise hard-disable: AETHERCHAT_DISABLE_COMPUTER_USE=1
Explicit enable: settings file or AETHERCHAT_COMPUTER_USE_ENABLED=1
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from ..data_dir import ensure_private_data_dir, harden_private_file


def _settings_path() -> Path:
    configured = os.environ.get("AETHERCHAT_DATA_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aetherchat"
    ensure_private_data_dir(root)
    return root / "computer_use_policy.json"


class ComputerUsePolicy:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _settings_path()
        self._lock = RLock()
        self._enabled = False
        self._load()

    def _load(self) -> None:
        if os.environ.get("AETHERCHAT_DISABLE_COMPUTER_USE", "").strip() in {"1", "true", "yes", "on"}:
            self._enabled = False
            return
        env = os.environ.get("AETHERCHAT_COMPUTER_USE_ENABLED", "").strip().lower()
        if env in {"1", "true", "yes", "on"}:
            self._enabled = True
            return
        if env in {"0", "false", "no", "off"}:
            self._enabled = False
            return
        if not self.path.is_file():
            self._enabled = False
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self._enabled = False
            return
        self._enabled = bool(raw.get("enabled")) if isinstance(raw, dict) else False

    def is_hard_disabled(self) -> bool:
        return os.environ.get("AETHERCHAT_DISABLE_COMPUTER_USE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def is_enabled(self) -> bool:
        with self._lock:
            self._load()
            if self.is_hard_disabled():
                return False
            return bool(self._enabled)

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if self.is_hard_disabled():
            raise PermissionError(
                "Computer Use is hard-disabled by AETHERCHAT_DISABLE_COMPUTER_USE=1"
            )
        with self._lock:
            self._enabled = bool(enabled)
            payload = {"enabled": self._enabled, "schema": "aetherchat-computer-use-policy-v1"}
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            harden_private_file(self.path)
            return dict(payload)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.is_enabled(),
            "hard_disabled": self.is_hard_disabled(),
            "default": False,
            "settings_path": str(self.path),
        }


computer_use_policy = ComputerUsePolicy()
