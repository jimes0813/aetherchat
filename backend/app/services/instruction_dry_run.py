"""Dry-run for Skills/Rules: resolve layers without execution + permission checklist."""

from __future__ import annotations

import re
from typing import Any

from .approval_service import approval_policy
from .skills_rules_service import skills_rules_service

# Known tools the product ships with (names as seen by models).
KNOWN_TOOLS: dict[str, dict[str, str]] = {
    "run_command": {
        "risk": "command_execution",
        "label": "沙箱命令",
        "level": "high",
    },
    "write_artifact": {
        "risk": "workspace_write",
        "label": "工作区写文件",
        "level": "medium",
    },
    "create_merge_proposal": {
        "risk": "project_change",
        "label": "合并提案",
        "level": "high",
    },
    "propose_merge": {
        "risk": "project_change",
        "label": "合并提案",
        "level": "high",
    },
    "vote_on_proposal": {
        "risk": "project_change",
        "label": "合并投票",
        "level": "high",
    },
    "plugin__browser_links__open_url": {
        "risk": "external_navigation",
        "label": "打开外部链接",
        "level": "medium",
    },
    "plugin__documents__export_to_local": {
        "risk": "local_file_write",
        "label": "导出到本机",
        "level": "high",
    },
    "plugin__desktop_control__get_state": {
        "risk": "computer_observation",
        "label": "观察电脑",
        "level": "low",
    },
    "plugin__desktop_control__click": {
        "risk": "computer_control",
        "label": "操作电脑",
        "level": "high",
    },
    "plugin__desktop_control__type_text": {
        "risk": "computer_control",
        "label": "键入文本",
        "level": "high",
    },
    "plugin__comfyui__generate_image": {
        "risk": "local_model_execution",
        "label": "本地图像生成",
        "level": "medium",
    },
    "plugin__comfyui__generate_video": {
        "risk": "local_model_execution",
        "label": "本地视频生成",
        "level": "medium",
    },
    "upsert_memory_entity": {
        "risk": "memory_write",
        "label": "写入共享记忆",
        "level": "low",
    },
    "create_task": {
        "risk": "task_write",
        "label": "创建任务",
        "level": "low",
    },
}

# Chinese/English phrases that often imply side effects even without exact tool names.
IMPLIED_CAPABILITIES: list[tuple[re.Pattern[str], dict[str, str]]] = [
    (re.compile(r"run_command|执行命令|终端|shell|bash", re.I), {
        "tool": "run_command", "note": "正文提及命令执行",
    }),
    (re.compile(r"write_artifact|写入文件|修改代码|写文件", re.I), {
        "tool": "write_artifact", "note": "正文提及工作区写入",
    }),
    (re.compile(r"open_url|打开链接|浏览器|访问 http", re.I), {
        "tool": "plugin__browser_links__open_url", "note": "正文提及外链导航",
    }),
    (re.compile(r"export_to_local|本机文件|桌面路径|写入本机", re.I), {
        "tool": "plugin__documents__export_to_local", "note": "正文提及本机写盘",
    }),
    (re.compile(r"desktop_control|键鼠|点击|截屏|操作电脑", re.I), {
        "tool": "plugin__desktop_control__click", "note": "正文提及 Computer Use",
    }),
    (re.compile(r"comfyui|生成图像|生成图片|文生图|generate_image", re.I), {
        "tool": "plugin__comfyui__generate_image", "note": "正文提及本地生成",
    }),
    (re.compile(r"删除|rm\s|drop table|format ", re.I), {
        "tool": "run_command", "note": "正文含破坏性措辞（需人工确认）",
    }),
]

LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _schema_checklist(schema: dict[str, Any] | None, kind: str) -> list[dict[str, Any]]:
    if not schema or not isinstance(schema, dict):
        return []
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    items = []
    for name, meta in list(props.items())[:40]:
        items.append({
            "kind": kind,
            "name": str(name),
            "type": str((meta or {}).get("type") or "string") if isinstance(meta, dict) else "string",
            "required": str(name) in required,
            "description": str((meta or {}).get("description") or "") if isinstance(meta, dict) else "",
        })
    return items


def _tool_entry(tool_name: str, *, source: str, note: str = "") -> dict[str, Any]:
    known = KNOWN_TOOLS.get(tool_name, {})
    policy = approval_policy(tool_name, {})
    risk = known.get("risk") or (policy or {}).get("risk") or "unknown"
    level = known.get("level") or ("high" if policy else "low")
    label = known.get("label") or tool_name
    requires_approval = policy is not None or risk not in {"memory_write", "task_write", "unknown"}
    if risk == "unknown" and not policy:
        requires_approval = False
        level = "low"
    return {
        "tool": tool_name,
        "label": label,
        "risk": risk,
        "level": level,
        "requires_approval": requires_approval,
        "allow_session": bool((policy or {}).get("allow_session", True)),
        "source": source,
        "note": note,
        "approval_title": (policy or {}).get("title"),
    }


def _scan_text_for_tools(text: str, source: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    # Exact known tool ids appearing as bare tokens
    for tool_name in KNOWN_TOOLS:
        if re.search(rf"(?<![\w.]){re.escape(tool_name)}(?![\w.])", text or ""):
            found[tool_name] = _tool_entry(tool_name, source=source, note="正文点名工具")
    for pattern, meta in IMPLIED_CAPABILITIES:
        if pattern.search(text or ""):
            tool_name = meta["tool"]
            if tool_name not in found:
                found[tool_name] = _tool_entry(
                    tool_name, source=source, note=meta.get("note") or "能力暗示",
                )
    # Explicit mcp__* tokens
    for match in re.finditer(r"\bmcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+\b", text or ""):
        tool_name = match.group(0)
        found[tool_name] = _tool_entry(tool_name, source=source, note="正文点名 MCP 工具")
    return list(found.values())


def dry_run_instructions(
    *,
    user_message: str = "",
    session_id: str | None = None,
    workspace_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve instruction layers and build a non-executing permission report."""
    if session_id and not workspace_roots:
        resolved = skills_rules_service.resolve_for_session(session_id, user_message=user_message)
    else:
        resolved = skills_rules_service.resolve(
            user_message=user_message,
            session_id=session_id,
            workspace_roots=workspace_roots or [],
        )

    layers = list(resolved.get("manifest") or [])
    context = str(resolved.get("context") or "")

    # Collect published skill/rule bodies for scanning (from context sections + skills list)
    permission_map: dict[str, dict[str, Any]] = {}
    io_checklist: list[dict[str, Any]] = []
    layer_summaries: list[dict[str, Any]] = []

    for layer in layers:
        kind = str(layer.get("kind") or "")
        name = str(layer.get("name") or layer.get("id") or "")
        version = str(layer.get("version") or "")
        layer_summaries.append({
            "kind": kind,
            "id": layer.get("id"),
            "name": name,
            "version": version,
            "content_hash": layer.get("content_hash"),
            "draft_dirty": bool(layer.get("draft_dirty")),
        })

    # Scan full resolved context once
    for entry in _scan_text_for_tools(context, source="resolved_context"):
        existing = permission_map.get(entry["tool"])
        if not existing or LEVEL_ORDER.get(entry["level"], 0) > LEVEL_ORDER.get(existing.get("level"), 0):
            permission_map[entry["tool"]] = entry

    # Enrich with explicit skill allowed_tools + typed IO from published skills that matched
    for skill in skills_rules_service.list_skills():
        view = skills_rules_service._published_skill_view(skill)
        if not skills_rules_service._skill_matches(view, user_message):
            continue
        for tool_name in view.get("allowed_tools") or []:
            tool_name = str(tool_name).strip()
            if not tool_name:
                continue
            entry = _tool_entry(tool_name, source=f"skill:{view.get('name')}", note="技能声明 allowed_tools")
            permission_map[tool_name] = entry
        io_checklist.extend(
            _schema_checklist(view.get("input_schema"), f"skill_input:{view.get('name')}")
        )
        io_checklist.extend(
            _schema_checklist(view.get("output_schema"), f"skill_output:{view.get('name')}")
        )

    permissions = sorted(
        permission_map.values(),
        key=lambda item: (-LEVEL_ORDER.get(str(item.get("level")), 0), str(item.get("tool"))),
    )
    high_count = sum(1 for item in permissions if item.get("level") in {"high", "critical"})
    approval_required = [item for item in permissions if item.get("requires_approval")]

    issues: list[str] = []
    if any(layer.get("draft_dirty") for layer in layers):
        issues.append("部分层存在未发布草稿；dry-run 仅使用已发布版本")
    if high_count:
        issues.append(f"检测到 {high_count} 项高风险能力，执行前可能弹出授权卡片")
    if not layers:
        issues.append("当前消息不会注入任何 Rules/Skills/AGENTS.md 层")

    status = "ok"
    if high_count and layers:
        status = "needs_review"
    if not layers:
        status = "empty"

    return {
        "dry_run": True,
        "executed": False,
        "status": status,
        "user_message": user_message,
        "layer_count": len(layers),
        "layers": layer_summaries,
        "context_preview": context[:4000],
        "permissions": permissions,
        "approval_required": approval_required,
        "io_checklist": io_checklist,
        "summary": {
            "permission_count": len(permissions),
            "high_risk_count": high_count,
            "approval_count": len(approval_required),
            "io_field_count": len(io_checklist),
        },
        "issues": issues,
        "notes": [
            "本报告不启动模型、不调用工具、不写入工作区。",
            "实际授权仍由对话中的授权卡片在运行时确认。",
        ],
    }


def catalog_permission_matrix() -> list[dict[str, Any]]:
    """Static catalog for UI: all known tools and their default risk levels."""
    rows = []
    for tool_name, meta in sorted(KNOWN_TOOLS.items()):
        entry = _tool_entry(tool_name, source="catalog")
        entry["label"] = meta.get("label") or entry["label"]
        rows.append(entry)
    return rows
