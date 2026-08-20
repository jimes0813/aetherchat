import os
import stat
from pathlib import Path

import pytest
from app.data_dir import default_data_dir, packaged_legacy_data_dir, xdg_data_dir
from app.services.chat_database import ChatDatabase
from app.services.knowledge_service import KnowledgeService


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


@pytest.mark.skipif(os.name == "nt", reason="Unix permission contract")
def test_application_databases_and_data_root_are_private(tmp_path):
    root = tmp_path / "app-data"
    root.mkdir(mode=0o775)

    database = ChatDatabase(root / "aetherchat.sqlite3")
    KnowledgeService(root)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.path.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "knowledge.sqlite3").stat().st_mode) == 0o600
