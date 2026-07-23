from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..services.artifact_service import ArtifactPathError, ArtifactService, MergeProposalStore
from ..services.tool_registry import tool_registry

router = APIRouter()


async def _session_snapshot(session_id: str) -> dict[str, Any]:
    artifact_svc = ArtifactService()
    artifact_svc.normalize_session_id(session_id)
    files = await artifact_svc.list_files(session_id)

    artifacts: list[dict[str, Any]] = []
    all_history: list[dict[str, Any]] = []
    for metadata in files:
        path = metadata["path"]
        try:
            content = await artifact_svc.read(session_id, path)
        except (FileNotFoundError, UnicodeDecodeError):
            content = None
        history = await artifact_svc.history(session_id, path)
        all_history.extend(history)
        artifacts.append({**metadata, "content": content, "history": history})

    # Tasks currently live behind the task router.  Reading the backing models
    # directly keeps this export offline and, importantly, enforces the session
    # filter even for older task-router implementations.
    from .tasks import fake_tasks

    tasks = [
        task.model_dump(mode="json")
        for task in fake_tasks
        if str(task.session_id) == str(session_id)
    ]
    merge_proposals = MergeProposalStore().list(session_id)
    runs = tool_registry.list_runs(session_id=session_id, limit=None)
    used_tool_ids = {run.get("tool_id") for run in runs if run.get("tool_id")}
    tools = [
        tool
        for tool in tool_registry.list(session_id=session_id)
        if tool.get("session_id") == str(session_id) or tool.get("id") in used_tool_ids
    ]

    return {
        "schema_version": 2,
        "session_id": str(session_id),
        "exported_at": datetime.now(UTC).isoformat(),
        "tasks": tasks,
        "merge_proposals": merge_proposals,
        "artifacts": artifacts,
        # Retained as a top-level index for clients of the original export API.
        "history": sorted(all_history, key=lambda item: item.get("created_at", ""), reverse=True),
        "tools": tools,
        "tool_runs": runs,
    }


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n```\n"


def _render_markdown(snapshot: dict[str, Any]) -> str:
    # Every section is generated from the exact same snapshot returned by the
    # JSON endpoint.  Nothing is sampled or truncated.
    sections = [
        "# AetherChat Session Export\n",
        f"**Session ID**: {snapshot['session_id']}  ",
        f"**Exported**: {snapshot['exported_at']}  ",
        f"**Schema version**: {snapshot['schema_version']}\n",
        "## Tasks\n",
        _json_block(snapshot["tasks"]),
        "## Merge proposals\n",
        _json_block(snapshot["merge_proposals"]),
        "## Artifacts (content and complete history)\n",
        _json_block(snapshot["artifacts"]),
        "## History index\n",
        _json_block(snapshot["history"]),
        "## Tools used in this session\n",
        _json_block(snapshot["tools"]),
        "## Tool runs\n",
        _json_block(snapshot["tool_runs"]),
    ]
    return "\n".join(sections)


@router.get("/session/{session_id}")
async def export_session(session_id: str, format: str = "markdown"):
    """Export one isolated session as equivalent JSON or Markdown data."""

    normalized_format = format.strip().lower()
    if normalized_format not in {"json", "markdown", "md"}:
        raise HTTPException(status_code=400, detail="format must be 'json' or 'markdown'")
    try:
        snapshot = await _session_snapshot(session_id)
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if normalized_format == "json":
        return snapshot

    markdown = _render_markdown(snapshot)
    return StreamingResponse(
        io.StringIO(markdown),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=session_{session_id}.md"},
    )


@router.get("/project/{session_id}")
async def export_project(session_id: str):
    """Compatibility bundle backed by the same complete session snapshot."""

    try:
        snapshot = await _session_snapshot(session_id)
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    bundle = {
        artifact["path"]: artifact["content"]
        for artifact in snapshot["artifacts"]
        if artifact["content"] is not None
    }
    return {**snapshot, "bundle": bundle}
