import os
import sys
from pathlib import Path

import uvicorn


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    if sys.argv[1:2] == ["local-audit-mcp"]:
        from mcp_servers.aetherchat_local_audit import main as local_audit_main

        local_audit_main(sys.argv[2:])
        return

    if os.name == "nt":
        default_data_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat"
    else:
        default_data_dir = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aetherchat"
    data_dir = Path(
        os.environ.get(
            "AETHERCHAT_DATA_DIR",
            default_data_dir,
        )
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AETHERCHAT_DATA_DIR", str(data_dir))
    os.chdir(_runtime_root())
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.environ.get("AETHERCHAT_PORT", "8000")),
        log_level=os.environ.get("AETHERCHAT_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
