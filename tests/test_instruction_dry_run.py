"""Dry-run + permission checklist for Skills/Rules."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

os.environ["AETHERCHAT_DATA_DIR"] = tempfile.mkdtemp(prefix="aetherchat-dry-run-")

from app.services.instruction_dry_run import (  # noqa: E402
    catalog_permission_matrix,
    dry_run_instructions,
)
from app.services.skills_rules_service import (  # noqa: E402
    SkillCreate,
    SkillsRulesService,
)


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERCHAT_DATA_DIR", str(tmp_path))
    svc = SkillsRulesService()
    monkeypatch.setattr(
        "app.services.instruction_dry_run.skills_rules_service",
        svc,
    )
    return svc


def test_dry_run_does_not_execute_and_lists_layers(service: SkillsRulesService):
    report = dry_run_instructions(user_message="请 code review 一下这段代码")
    assert report["dry_run"] is True
    assert report["executed"] is False
    assert report["layer_count"] >= 1
    assert any(layer["kind"] in {"rule", "skill"} for layer in report["layers"])


def test_dry_run_detects_command_risk_from_skill_body(service: SkillsRulesService):
    service.create_skill(
        SkillCreate(
            name="运维排查",
            body="需要时调用 run_command 查看日志，禁止 rm -rf /",
            always_on=True,
            trigger_keywords=[],
        )
    )
    report = dry_run_instructions(user_message="随便")
    tools = {item["tool"] for item in report["permissions"]}
    assert "run_command" in tools
    assert report["summary"]["high_risk_count"] >= 1
    assert report["status"] in {"needs_review", "ok"}
    assert any(item["requires_approval"] for item in report["permissions"])


def test_dry_run_includes_typed_io_checklist(service: SkillsRulesService):
    service.create_skill(
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
    report = dry_run_instructions(user_message="x")
    names = {item["name"] for item in report["io_checklist"]}
    assert "goal" in names
    assert "result" in names


def test_catalog_permission_matrix_has_known_tools():
    rows = catalog_permission_matrix()
    tools = {item["tool"] for item in rows}
    assert "run_command" in tools
    assert "write_artifact" in tools
