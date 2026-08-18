"""Unit tests for Skills / Rules / AGENTS.md instruction MVP."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ.setdefault("AETHERCHAT_DATA_DIR", tempfile.mkdtemp(prefix="aetherchat-skills-rules-"))

from app.services.skills_rules_service import (  # noqa: E402
    RuleCreate,
    SkillCreate,
    SkillsRulesService,
)


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    return SkillsRulesService()


def test_seed_contains_default_rule_and_skill(service: SkillsRulesService):
    assert len(service.list_rules()) >= 1
    assert len(service.list_skills()) >= 1
    assert any(item["name"] == "默认安全协作规则" for item in service.list_rules())


def test_skill_triggers_on_keywords(service: SkillsRulesService):
    skill = service.create_skill(
        SkillCreate(
            name="部署清单",
            body="按清单检查回滚步骤",
            trigger_keywords=["部署", "deploy"],
            always_on=False,
        )
    )
    miss = service.resolve(user_message="今天天气如何")
    assert skill["id"] not in {item["id"] for item in miss["manifest"] if item["kind"] == "skill"}

    hit = service.resolve(user_message="请帮我做一次部署检查")
    assert any(item["id"] == skill["id"] for item in hit["manifest"] if item["kind"] == "skill")
    assert "部署清单" in hit["context"]


def test_always_on_skill_always_included(service: SkillsRulesService):
    skill = service.create_skill(
        SkillCreate(name="常驻语气", body="语气专业简洁", always_on=True, trigger_keywords=[]),
    )
    resolved = service.resolve(user_message="随便聊一句")
    assert any(item["id"] == skill["id"] for item in resolved["manifest"])


def test_disabled_rule_excluded(service: SkillsRulesService):
    from app.services.skills_rules_service import RuleUpdate

    rule = service.create_rule(RuleCreate(name="临时规则", body="永远说谢谢", enabled=True))
    service.update_rule(rule["id"], RuleUpdate(enabled=False))
    resolved = service.resolve(user_message="hi")
    assert all(item["id"] != rule["id"] for item in resolved["manifest"])


def test_agents_md_loaded_from_workspace(service: SkillsRulesService, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Repo Agents\n- 使用 pytest\n", encoding="utf-8")
    resolved = service.resolve(user_message="写测试", workspace_roots=[str(repo)])
    assert any(item["kind"] == "agents_md" for item in resolved["manifest"])
    assert "使用 pytest" in resolved["context"]


def test_import_agents_md_as_rule(service: SkillsRulesService, tmp_path: Path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("必须跑 lint", encoding="utf-8")
    imported = service.import_agents_md_as_rule(agents)
    assert imported["source"] == "agents_md"
    assert "必须跑 lint" in imported["body"]
    again = service.import_agents_md_as_rule(agents)
    assert again["id"] == imported["id"]


def test_manifest_includes_content_hash(service: SkillsRulesService):
    resolved = service.resolve(user_message="请 code review 一下")
    assert resolved["layer_count"] >= 1
    for layer in resolved["manifest"]:
        assert layer.get("content_hash")
        assert layer.get("kind") in {"rule", "skill", "agents_md"}


def test_publish_and_rollback_skill_uses_published_body(service: SkillsRulesService):
    from app.services.skills_rules_service import PublishRequest, RollbackRequest, SkillUpdate

    skill = service.create_skill(
        SkillCreate(
            name="版本化技能",
            body="草稿 v1",
            version="1.0.0",
            always_on=True,
        )
    )
    service.update_skill(skill["id"], SkillUpdate(body="草稿 v2 未发布"))
    # resolve must still use published 1.0.0 body
    mid = service.resolve(user_message="x")
    assert "草稿 v1" in mid["context"]
    assert "草稿 v2 未发布" not in mid["context"]

    published = service.publish_skill(
        skill["id"],
        PublishRequest(version="1.1.0", changelog="发布第二版"),
    )
    assert published["published_version"] == "1.1.0"
    assert published["draft_dirty"] is False
    after = service.resolve(user_message="x")
    assert "草稿 v2 未发布" in after["context"]
    assert any(
        item["version"] == "1.1.0" for item in after["manifest"] if item["id"] == skill["id"]
    )

    rolled = service.rollback_skill(skill["id"], RollbackRequest(version="1.0.0"))
    assert rolled["published_version"] == "1.0.0"
    assert rolled["body"] == "草稿 v1"
    back = service.resolve(user_message="x")
    assert "草稿 v1" in back["context"]


def test_skill_typed_io_appears_in_context(service: SkillsRulesService):
    skill = service.create_skill(
        SkillCreate(
            name="契约技能",
            body="按契约输出",
            always_on=True,
            input_schema={
                "type": "object",
                "properties": {"goal": {"type": "string", "description": "目标"}},
                "required": ["goal"],
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
        )
    )
    resolved = service.resolve(user_message="hello")
    assert "输入契约" in resolved["context"]
    assert "`goal`" in resolved["context"]
    assert "输出契约" in resolved["context"]
    assert skill["id"] in {item["id"] for item in resolved["manifest"]}


def test_publish_rejects_duplicate_version(service: SkillsRulesService):
    from app.services.skills_rules_service import PublishRequest

    skill = service.create_skill(SkillCreate(name="dup", body="a", version="1.0.0", always_on=True))
    with pytest.raises(ValueError, match="已存在"):
        service.publish_skill(skill["id"], PublishRequest(version="1.0.0"))
