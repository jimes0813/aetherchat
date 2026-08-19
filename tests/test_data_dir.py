from pathlib import Path

from app.data_dir import default_data_dir, packaged_legacy_data_dir, xdg_data_dir


def test_default_data_dir_prefers_explicit_env(tmp_path):
    configured = tmp_path / "explicit"
    assert default_data_dir(env={"AETHERCHAT_DATA_DIR": str(configured)}, home=tmp_path) == configured


def test_default_data_dir_keeps_packaged_legacy_when_sqlite_exists(tmp_path):
    home = tmp_path / "home"
    packaged = packaged_legacy_data_dir(env={"XDG_CONFIG_HOME": str(home / ".config")}, home=home)
    packaged.mkdir(parents=True)
    (packaged / "aetherchat.sqlite3").write_bytes(b"")
    xdg = xdg_data_dir(env={"XDG_DATA_HOME": str(home / ".local" / "share")}, home=home)
    xdg.mkdir(parents=True)
    (xdg / "aetherchat.sqlite3").write_bytes(b"")
    assert default_data_dir(
        env={
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
        },
        home=home,
    ) == packaged


def test_default_data_dir_uses_xdg_when_only_share_sqlite_exists(tmp_path):
    home = tmp_path / "home"
    xdg = xdg_data_dir(env={"XDG_DATA_HOME": str(home / ".local" / "share")}, home=home)
    xdg.mkdir(parents=True)
    (xdg / "aetherchat.sqlite3").write_bytes(b"")
    assert default_data_dir(
        env={"XDG_DATA_HOME": str(home / ".local" / "share"), "XDG_CONFIG_HOME": str(home / ".config")},
        home=home,
    ) == xdg


def test_default_data_dir_new_install_uses_xdg(tmp_path):
    home = tmp_path / "home"
    expected = Path(home / ".local" / "share") / "aetherchat"
    assert default_data_dir(env={}, home=home) == expected
