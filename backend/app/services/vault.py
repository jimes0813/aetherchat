import base64
import json
import os
from pathlib import Path

try:
    import win32crypt
except ImportError:  # pragma: no cover - non-Windows development
    win32crypt = None

try:
    import keyring as system_keyring
except ImportError:  # pragma: no cover - reported when a key is saved
    system_keyring = None


DPAPI_PREFIX = "dpapi:"
KEYRING_MARKER = "keyring"
KEYRING_SERVICE = "com.aetherchat.desktop"


class SimpleVault:
    """Local desktop vault.

    Windows uses DPAPI. Linux and macOS store values in the operating-system
    credential service; vault.json contains references only.
    """

    def __init__(self):
        self._keys: dict[str, str] = {}
        self._refs: set[str] = set()
        self.path = self._storage_path()
        self._load()

    def _storage_path(self) -> Path:
        if os.name == "nt":
            default_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat"
        else:
            default_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aetherchat"
        root = Path(
            os.environ.get(
                "AETHERCHAT_DATA_DIR",
                default_root,
            )
        )
        root.mkdir(parents=True, exist_ok=True)
        return root / "vault.json"

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        migrated = False
        if isinstance(data, dict):
            for ref, stored in data.items():
                if not stored:
                    continue
                if os.name != "nt":
                    ref = str(ref)
                    if stored == KEYRING_MARKER:
                        self._refs.add(ref)
                        continue
                    if system_keyring is not None and isinstance(stored, str):
                        try:
                            system_keyring.set_password(KEYRING_SERVICE, ref, stored)
                        except Exception:
                            continue
                        self._refs.add(ref)
                        migrated = True
                    continue
                value = self._decode_value(str(stored))
                if value is None:
                    continue
                self._keys[str(ref)] = value
                if not str(stored).startswith(DPAPI_PREFIX) and win32crypt is not None and os.name == "nt":
                    migrated = True
        if migrated:
            self._save()

    def _save(self):
        stored = (
            {ref: self._encode_value(value) for ref, value in self._keys.items()}
            if os.name == "nt"
            else dict.fromkeys(sorted(self._refs), KEYRING_MARKER)
        )
        self.path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _encode_value(self, value: str) -> str:
        if win32crypt is None or os.name != "nt":
            return value
        protected = win32crypt.CryptProtectData(
            value.encode("utf-8"),
            "AetherChat API key",
            None,
            None,
            None,
            0,
        )
        encrypted = protected[1] if isinstance(protected, tuple) else protected
        return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")

    def _decode_value(self, stored: str) -> str | None:
        if not stored.startswith(DPAPI_PREFIX):
            return stored
        if win32crypt is None or os.name != "nt":
            return None
        try:
            encrypted = base64.b64decode(stored[len(DPAPI_PREFIX):], validate=True)
            decrypted = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1]
            return decrypted.decode("utf-8")
        except Exception:
            return None

    def get_key(self, ref: str) -> str | None:
        env_key = os.getenv(ref)
        if env_key:
            return env_key
        if os.name == "nt":
            return self._keys.get(ref)
        if system_keyring is None or ref not in self._refs:
            return None
        try:
            return system_keyring.get_password(KEYRING_SERVICE, ref)
        except Exception:
            return None

    def set_key(self, ref: str, key: str):
        if os.name == "nt":
            self._keys[ref] = key
            self._save()
            return
        if system_keyring is None:
            raise RuntimeError("系统密钥环不可用；请安装 keyring/Secret Service 后重试")
        try:
            system_keyring.set_password(KEYRING_SERVICE, ref, key)
        except Exception as exc:
            raise RuntimeError("无法将 API Key 保存到系统密钥环") from exc
        self._refs.add(ref)
        self._save()

    def delete_key(self, ref: str) -> None:
        if os.name == "nt":
            self._keys.pop(ref, None)
            self._save()
            return
        if system_keyring is not None and ref in self._refs:
            try:
                system_keyring.delete_password(KEYRING_SERVICE, ref)
            except Exception:
                pass
        self._refs.discard(ref)
        self._save()


vault = SimpleVault()
