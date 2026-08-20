from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def ensure_private_data_dir(path: Path) -> Path:
    """Create a dedicated application data directory with private Unix modes.

    A configured data root can point at a shared parent such as ``/tmp``. Never
    chmod those broad system/home directories; individual private files are
    still hardened by their owning services.
    """
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        return path

    home = Path.home().resolve()
    protected_roots = {
        Path("/"),
        Path("/home"),
        Path("/tmp"),
        Path("/var/tmp"),
        home,
        home.parent,
    }
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        protected_roots.add(Path(runtime_dir).expanduser().resolve())
    if path not in protected_roots:
        path.chmod(0o700)
    return path


def harden_private_file(path: Path) -> None:
    """Restrict an existing application data file to its Unix owner."""
    if os.name != "nt" and path.is_file():
        path.chmod(0o600)


def packaged_legacy_data_dir(*, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    environment = env if env is not None else os.environ
    home = home or Path.home()
    if os.name == "nt":
        roaming = Path(environment.get("APPDATA", str(home / "AppData" / "Roaming")))
        return roaming / "aetherchat-frontend" / "backend-data"
    config_home = Path(environment.get("XDG_CONFIG_HOME", home / ".config"))
    return config_home / "aetherchat-frontend" / "backend-data"


def xdg_data_dir(*, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    environment = env if env is not None else os.environ
    home = home or Path.home()
    if os.name == "nt":
        return Path(environment.get("LOCALAPPDATA", str(home))) / "AetherChat"
    return Path(environment.get("XDG_DATA_HOME", home / ".local" / "share")) / "aetherchat"


def default_data_dir(*, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """Resolve the backend data root without moving existing databases.

    Packed Electron installs historically wrote SQLite under
    ``userData/backend-data``. Standalone docs and the backend entrypoint use
    the XDG data directory. Prefer an explicit env, then whichever location
    already has ``aetherchat.sqlite3``, then XDG for new installs.
    """
    environment = env if env is not None else os.environ
    home = home or Path.home()
    configured = environment.get("AETHERCHAT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    packaged = packaged_legacy_data_dir(env=environment, home=home)
    xdg = xdg_data_dir(env=environment, home=home)
    if (packaged / "aetherchat.sqlite3").is_file():
        return packaged
    if (xdg / "aetherchat.sqlite3").is_file():
        return xdg
    return xdg
