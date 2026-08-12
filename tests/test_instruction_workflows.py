"""Instruction workflow interval/webhook/manual triggers."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ["AETHERCHAT_DATA_DIR"] = tempfile.mkdtemp(prefix="aetherchat-workflows-")

from app.services.instruction_workflow_service import (  # noqa: E402
    WorkflowCreate,
    InstructionWorkflowService,
)
from app.services.skills_rules_service import SkillCreate, SkillsRulesService  # noqa: E402


@pytest.fixture()
def services(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    skills = SkillsRulesService()
    monkeypatch.setattr(
        "app.services.instruction_workflow_service.skills_rules_service",
        skills,
    )
    monkeypatch.setattr(
        "app.services.instruction_dry_run.skills_rules_service",
        skills,
    )
    workflows = InstructionWorkflowService()
    return skills, workflows


def test_manual_dry_run_workflow(services):
    skills, workflows = services
    skill = skills.create_skill(
        SkillCreate(name="晨报", body="输出三条要点", always_on=True),
    )
    item = workflows.create(
        WorkflowCreate(
            name="晨报工作流",
            session_id="11111111-1111-4111-8111-111111111111",
            skill_id=skill["id"],
            message="生成今日摘要",
            trigger_type="manual",
            execution_mode="dry_run",
        )
    )
    assert item["trigger_type"] == "manual"
    result = workflows.fire(item, trigger="manual")
    assert result["execution_mode"] == "dry_run"
    assert result["dry_run"]["executed"] is False
    assert "晨报" in result["prompt"] or "要点" in result["prompt"]
    assert result["job"] is None


def test_webhook_token_lookup(services):
    skills, workflows = services
    item = workflows.create(
        WorkflowCreate(
            name="钩子",
            session_id="11111111-1111-4111-8111-111111111111",
            message="ping",
            trigger_type="webhook",
            execution_mode="dry_run",
        )
    )
    token = item.get("webhook_token")
    assert token
    found = workflows.find_by_webhook_token(token)
    assert found is not None
    assert found["id"] == item["id"]
    assert workflows.find_by_webhook_token("wrong-token") is None
    # public view strips token
    public = workflows.public_view(item, include_token=False)
    assert "webhook_token" not in public
    assert public["has_webhook"] is True


def test_build_prompt_includes_skill(services):
    skills, workflows = services
    skill = skills.create_skill(
        SkillCreate(name="部署检查", body="检查回滚步骤", always_on=False, trigger_keywords=["部署"]),
    )
    item = workflows.create(
        WorkflowCreate(
            name="部署流",
            session_id="11111111-1111-4111-8111-111111111111",
            skill_id=skill["id"],
            prompt_template="任务：{{message}}\n技能：{{skill_name}}",
            message="准备上线",
            trigger_type="manual",
        )
    )
    prompt = workflows._build_prompt(item)
    assert "准备上线" in prompt
    assert "部署检查" in prompt
