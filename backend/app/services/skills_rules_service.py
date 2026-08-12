"""Skills / Rules / AGENTS.md layered instruction service.

Batch 34: CRUD + resolve + AGENTS.md
Batch 35: skill/rule published versions (publish/rollback) + typed IO schemas
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _data_root() -> Path:
    root = Path(
        os.environ.get(
            "AETHERCHAT_DATA_DIR",
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AetherChat",
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bump_patch(version: str) -> str:
    parts = re.split(r"[.\-+]", (version or "1.0.0").strip())
    nums = []
    for part in parts[:3]:
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    nums[2] += 1
    return f"{nums[0]}.{nums[1]}.{nums[2]}"


def _validate_json_schema(schema: dict[str, Any] | None, label: str) -> dict[str, Any] | None:
    if schema is None:
        return None
    if not isinstance(schema, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    if schema.get("type") not in {None, "object"}:
        raise ValueError(f"{label}.type 目前仅支持 object")
    props = schema.get("properties")
    if props is not None and not isinstance(props, dict):
        raise ValueError(f"{label}.properties 必须是对象")
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list) or not all(isinstance(item, str) for item in required)
    ):
        raise ValueError(f"{label}.required 必须是字符串数组")
    # Keep a compact, serializable subset
    cleaned: dict[str, Any] = {"type": "object"}
    if props:
        cleaned["properties"] = {
            str(key)[:80]: {
                "type": str(value.get("type") or "string")[:32]
                if isinstance(value, dict)
                else "string",
                "description": str(value.get("description") or "")[:240]
                if isinstance(value, dict)
                else "",
            }
            for key, value in list(props.items())[:40]
        }
    if required:
        cleaned["required"] = [str(item)[:80] for item in required[:40]]
    return cleaned


def _format_schema_block(title: str, schema: dict[str, Any] | None) -> str:
    if not schema:
        return ""
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not props:
        return f"#### {title}\n（无字段）"
    lines = [f"#### {title}"]
    for name, meta in props.items():
        typ = (meta or {}).get("type") or "string"
        desc = (meta or {}).get("description") or ""
        flag = "必填" if name in required else "可选"
        line = f"- `{name}` ({typ}, {flag})"
        if desc:
            line += f"：{desc}"
        lines.append(line)
    return "\n".join(lines)


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=12000)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)
    scope: Literal["global", "session"] = "global"
    session_id: str | None = None


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    body: str | None = Field(default=None, min_length=1, max_length=12000)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    version: str = Field(default="1.0.0", max_length=32)
    enabled: bool = True
    always_on: bool = False
    trigger_keywords: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schema_ok(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_json_schema(value, "schema")


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    enabled: bool | None = None
    always_on: bool | None = None
    trigger_keywords: list[str] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schema_ok(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _validate_json_schema(value, "schema")


class PublishRequest(BaseModel):
    version: str | None = Field(default=None, max_length=32)
    changelog: str = Field(default="", max_length=2000)


class RollbackRequest(BaseModel):
    version: str = Field(min_length=1, max_length=32)


class SkillsRulesService:
    """File-backed registry for instruction layers with published versions."""

    def __init__(self) -> None:
        self._root = _data_root()
        self.rules_path = self._root / "rules.json"
        self.skills_path = self._root / "skills.json"
        self.rules: dict[str, dict[str, Any]] = {}
        self.skills: dict[str, dict[str, Any]] = {}
        if not self._load():
            self._seed()
            self._save()
        else:
            # Migrate legacy records in place
            changed = False
            for key, rule in list(self.rules.items()):
                normalized = self._normalize_rule(rule)
                if normalized != rule:
                    self.rules[key] = normalized
                    changed = True
            for key, skill in list(self.skills.items()):
                normalized = self._normalize_skill(skill)
                if normalized != skill:
                    self.skills[key] = normalized
                    changed = True
            if changed:
                self._save()

    def _load(self) -> bool:
        loaded_any = False
        if self.rules_path.is_file():
            try:
                rows = json.loads(self.rules_path.read_text(encoding="utf-8"))
                if isinstance(rows, list):
                    self.rules = {
                        str(item["id"]): self._normalize_rule(item)
                        for item in rows
                        if isinstance(item, dict) and item.get("id")
                    }
                    loaded_any = True
            except Exception:
                pass
        if self.skills_path.is_file():
            try:
                rows = json.loads(self.skills_path.read_text(encoding="utf-8"))
                if isinstance(rows, list):
                    self.skills = {
                        str(item["id"]): self._normalize_skill(item)
                        for item in rows
                        if isinstance(item, dict) and item.get("id")
                    }
                    loaded_any = True
            except Exception:
                pass
        return loaded_any and bool(self.rules or self.skills)

    def _save(self) -> None:
        self.rules_path.write_text(
            json.dumps(list(self.rules.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.skills_path.write_text(
            json.dumps(list(self.skills.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _snapshot_from_skill_draft(skill: dict[str, Any], *, version: str, changelog: str, published_at: str) -> dict[str, Any]:
        body = str(skill.get("body") or "")
        return {
            "version": version,
            "body": body,
            "description": str(skill.get("description") or ""),
            "always_on": bool(skill.get("always_on")),
            "trigger_keywords": list(skill.get("trigger_keywords") or []),
            "input_schema": skill.get("input_schema"),
            "output_schema": skill.get("output_schema"),
            "changelog": changelog,
            "content_hash": _content_hash(body),
            "published_at": published_at,
        }

    @staticmethod
    def _snapshot_from_rule_draft(rule: dict[str, Any], *, version: str, changelog: str, published_at: str) -> dict[str, Any]:
        body = str(rule.get("body") or "")
        return {
            "version": version,
            "body": body,
            "changelog": changelog,
            "content_hash": _content_hash(body),
            "published_at": published_at,
        }

    def _normalize_skill(self, raw: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw)
        body = str(item.get("body") or "")
        version = str(item.get("published_version") or item.get("version") or "1.0.0")
        versions = item.get("versions")
        if not isinstance(versions, list) or not versions:
            now = item.get("updated_at") or item.get("created_at") or _now()
            versions = [
                self._snapshot_from_skill_draft(
                    item, version=version, changelog="初始版本", published_at=str(now),
                )
            ]
        item["versions"] = versions
        item["published_version"] = str(item.get("published_version") or versions[-1]["version"])
        item["version"] = item["published_version"]  # compat alias for list UI
        item["content_hash"] = _content_hash(body)
        item.setdefault("input_schema", None)
        item.setdefault("output_schema", None)
        item.setdefault("draft_dirty", False)
        # dirty if draft body differs from published snapshot
        published = self._find_version(item["versions"], item["published_version"])
        if published and (
            str(published.get("body") or "") != body
            or list(published.get("trigger_keywords") or []) != list(item.get("trigger_keywords") or [])
            or bool(published.get("always_on")) != bool(item.get("always_on"))
            or (published.get("description") or "") != (item.get("description") or "")
            or published.get("input_schema") != item.get("input_schema")
            or published.get("output_schema") != item.get("output_schema")
        ):
            item["draft_dirty"] = True
        else:
            item["draft_dirty"] = False
        return item

    def _normalize_rule(self, raw: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw)
        body = str(item.get("body") or "")
        version = str(item.get("published_version") or "1.0.0")
        versions = item.get("versions")
        if not isinstance(versions, list) or not versions:
            now = item.get("updated_at") or item.get("created_at") or _now()
            versions = [
                self._snapshot_from_rule_draft(
                    item, version=version, changelog="初始版本", published_at=str(now),
                )
            ]
        item["versions"] = versions
        item["published_version"] = str(item.get("published_version") or versions[-1]["version"])
        item["content_hash"] = _content_hash(body)
        published = self._find_version(item["versions"], item["published_version"])
        item["draft_dirty"] = bool(published and str(published.get("body") or "") != body)
        return item

    @staticmethod
    def _find_version(versions: list[dict[str, Any]], version: str) -> dict[str, Any] | None:
        for item in versions:
            if str(item.get("version")) == version:
                return item
        return None

    def _seed(self) -> None:
        now = _now()
        rule_body = (
            "默认协作规则：\n"
            "1. 优先使用中文清晰回复。\n"
            "2. 高风险操作（删除文件、外发、执行命令）必须先说明再等待授权。\n"
            "3. 不确定时明确说明假设，不要编造本机状态。"
        )
        skill_body = (
            "## 代码评审技能\n"
            "当用户要求评审或 code review 时：\n"
            "1. 先概括变更意图；\n"
            "2. 按正确性 / 安全 / 可维护性 / 测试 四项列出问题；\n"
            "3. 每条问题给出严重度与可执行修复建议；\n"
            "4. 最后给出「可合并 / 需修改」结论。"
        )
        rule_id = str(uuid.uuid4())
        skill_id = str(uuid.uuid4())
        rule = {
            "id": rule_id,
            "name": "默认安全协作规则",
            "body": rule_body,
            "enabled": True,
            "priority": 10,
            "scope": "global",
            "session_id": None,
            "source": "builtin",
            "created_at": now,
            "updated_at": now,
        }
        skill = {
            "id": skill_id,
            "name": "代码评审",
            "description": "结构化 code review 输出格式",
            "body": skill_body,
            "enabled": True,
            "always_on": False,
            "trigger_keywords": ["评审", "review", "code review", "代码审查"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "diff_or_code": {
                        "type": "string",
                        "description": "待评审的 diff 或代码片段",
                    },
                    "focus": {
                        "type": "string",
                        "description": "可选关注点：安全/性能/可测性",
                    },
                },
                "required": ["diff_or_code"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "变更意图摘要"},
                    "issues": {"type": "array", "description": "问题列表"},
                    "verdict": {"type": "string", "description": "可合并/需修改"},
                },
                "required": ["summary", "verdict"],
            },
            "created_at": now,
            "updated_at": now,
        }
        self.rules[rule_id] = self._normalize_rule(rule)
        self.skills[skill_id] = self._normalize_skill(skill)

    # --- Rules CRUD ---

    def list_rules(self, session_id: str | None = None) -> list[dict[str, Any]]:
        rows = list(self.rules.values())
        if session_id:
            rows = [
                item
                for item in rows
                if item.get("scope") == "global"
                or (item.get("scope") == "session" and item.get("session_id") == session_id)
            ]
        return sorted(rows, key=lambda item: (int(item.get("priority") or 100), item.get("name") or ""))

    def create_rule(self, payload: RuleCreate) -> dict[str, Any]:
        now = _now()
        rule_id = str(uuid.uuid4())
        body = payload.body.strip()
        item = {
            "id": rule_id,
            "name": payload.name.strip(),
            "body": body,
            "enabled": payload.enabled,
            "priority": payload.priority,
            "scope": payload.scope,
            "session_id": payload.session_id if payload.scope == "session" else None,
            "source": "user",
            "created_at": now,
            "updated_at": now,
        }
        item = self._normalize_rule(item)
        self.rules[rule_id] = item
        self._save()
        return item

    def update_rule(self, rule_id: str, payload: RuleUpdate) -> dict[str, Any] | None:
        item = self.rules.get(rule_id)
        if not item:
            return None
        data = payload.model_dump(exclude_unset=True)
        if "name" in data and data["name"] is not None:
            item["name"] = str(data["name"]).strip()
        if "body" in data and data["body"] is not None:
            item["body"] = str(data["body"]).strip()
        if "enabled" in data and data["enabled"] is not None:
            item["enabled"] = bool(data["enabled"])
        if "priority" in data and data["priority"] is not None:
            item["priority"] = int(data["priority"])
        item["updated_at"] = _now()
        self.rules[rule_id] = self._normalize_rule(item)
        self._save()
        return self.rules[rule_id]

    def delete_rule(self, rule_id: str) -> bool:
        if rule_id not in self.rules:
            return False
        del self.rules[rule_id]
        self._save()
        return True

    def list_rule_versions(self, rule_id: str) -> list[dict[str, Any]] | None:
        item = self.rules.get(rule_id)
        if not item:
            return None
        return list(item.get("versions") or [])

    def publish_rule(self, rule_id: str, payload: PublishRequest) -> dict[str, Any]:
        item = self.rules.get(rule_id)
        if not item:
            raise KeyError(rule_id)
        current = str(item.get("published_version") or "1.0.0")
        version = (payload.version or "").strip() or _bump_patch(current)
        if self._find_version(item.get("versions") or [], version):
            raise ValueError(f"版本 {version} 已存在")
        now = _now()
        snapshot = self._snapshot_from_rule_draft(
            item, version=version, changelog=(payload.changelog or "").strip() or f"发布 {version}",
            published_at=now,
        )
        item.setdefault("versions", []).append(snapshot)
        item["published_version"] = version
        item["updated_at"] = now
        self.rules[rule_id] = self._normalize_rule(item)
        self._save()
        return self.rules[rule_id]

    def rollback_rule(self, rule_id: str, payload: RollbackRequest) -> dict[str, Any]:
        item = self.rules.get(rule_id)
        if not item:
            raise KeyError(rule_id)
        snapshot = self._find_version(item.get("versions") or [], payload.version.strip())
        if not snapshot:
            raise ValueError(f"版本 {payload.version} 不存在")
        item["published_version"] = snapshot["version"]
        item["body"] = snapshot["body"]
        item["updated_at"] = _now()
        self.rules[rule_id] = self._normalize_rule(item)
        self._save()
        return self.rules[rule_id]

    # --- Skills CRUD ---

    def list_skills(self) -> list[dict[str, Any]]:
        return sorted(
            self.skills.values(),
            key=lambda item: (item.get("name") or "").lower(),
        )

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        return self.skills.get(skill_id)

    def create_skill(self, payload: SkillCreate) -> dict[str, Any]:
        now = _now()
        skill_id = str(uuid.uuid4())
        body = payload.body.strip()
        keywords = [str(item).strip() for item in payload.trigger_keywords if str(item).strip()]
        version = payload.version.strip() or "1.0.0"
        item = {
            "id": skill_id,
            "name": payload.name.strip(),
            "description": (payload.description or "").strip(),
            "body": body,
            "enabled": payload.enabled,
            "always_on": payload.always_on,
            "trigger_keywords": keywords,
            "input_schema": payload.input_schema,
            "output_schema": payload.output_schema,
            "published_version": version,
            "created_at": now,
            "updated_at": now,
        }
        item = self._normalize_skill(item)
        self.skills[skill_id] = item
        self._save()
        return item

    def update_skill(self, skill_id: str, payload: SkillUpdate) -> dict[str, Any] | None:
        item = self.skills.get(skill_id)
        if not item:
            return None
        data = payload.model_dump(exclude_unset=True)
        for key in ("name", "description"):
            if key in data and data[key] is not None:
                item[key] = str(data[key]).strip()
        if "body" in data and data["body"] is not None:
            item["body"] = str(data["body"]).strip()
        if "enabled" in data and data["enabled"] is not None:
            item["enabled"] = bool(data["enabled"])
        if "always_on" in data and data["always_on"] is not None:
            item["always_on"] = bool(data["always_on"])
        if "trigger_keywords" in data and data["trigger_keywords"] is not None:
            item["trigger_keywords"] = [
                str(item_kw).strip() for item_kw in data["trigger_keywords"] if str(item_kw).strip()
            ]
        if "input_schema" in data:
            item["input_schema"] = data["input_schema"]
        if "output_schema" in data:
            item["output_schema"] = data["output_schema"]
        item["updated_at"] = _now()
        self.skills[skill_id] = self._normalize_skill(item)
        self._save()
        return self.skills[skill_id]

    def delete_skill(self, skill_id: str) -> bool:
        if skill_id not in self.skills:
            return False
        del self.skills[skill_id]
        self._save()
        return True

    def list_skill_versions(self, skill_id: str) -> list[dict[str, Any]] | None:
        item = self.skills.get(skill_id)
        if not item:
            return None
        return list(item.get("versions") or [])

    def publish_skill(self, skill_id: str, payload: PublishRequest) -> dict[str, Any]:
        item = self.skills.get(skill_id)
        if not item:
            raise KeyError(skill_id)
        current = str(item.get("published_version") or "1.0.0")
        version = (payload.version or "").strip() or _bump_patch(current)
        if self._find_version(item.get("versions") or [], version):
            raise ValueError(f"版本 {version} 已存在")
        now = _now()
        snapshot = self._snapshot_from_skill_draft(
            item,
            version=version,
            changelog=(payload.changelog or "").strip() or f"发布 {version}",
            published_at=now,
        )
        item.setdefault("versions", []).append(snapshot)
        item["published_version"] = version
        item["updated_at"] = now
        self.skills[skill_id] = self._normalize_skill(item)
        self._save()
        return self.skills[skill_id]

    def rollback_skill(self, skill_id: str, payload: RollbackRequest) -> dict[str, Any]:
        item = self.skills.get(skill_id)
        if not item:
            raise KeyError(skill_id)
        snapshot = self._find_version(item.get("versions") or [], payload.version.strip())
        if not snapshot:
            raise ValueError(f"版本 {payload.version} 不存在")
        item["published_version"] = snapshot["version"]
        item["body"] = snapshot["body"]
        item["description"] = snapshot.get("description") or ""
        item["always_on"] = bool(snapshot.get("always_on"))
        item["trigger_keywords"] = list(snapshot.get("trigger_keywords") or [])
        item["input_schema"] = snapshot.get("input_schema")
        item["output_schema"] = snapshot.get("output_schema")
        item["updated_at"] = _now()
        self.skills[skill_id] = self._normalize_skill(item)
        self._save()
        return self.skills[skill_id]

    # --- AGENTS.md ---

    @staticmethod
    def read_agents_md(path: str | Path) -> dict[str, Any] | None:
        root = Path(path).expanduser()
        agents = root if root.is_file() and root.name.upper() == "AGENTS.MD" else root / "AGENTS.md"
        if not agents.is_file():
            return None
        try:
            body = agents.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not body:
            return None
        return {
            "kind": "agents_md",
            "id": f"agents-md:{agents.resolve()}",
            "name": "AGENTS.md",
            "path": str(agents.resolve()),
            "body": body[:12000],
            "version": "file",
            "content_hash": _content_hash(body),
            "source": "repository",
        }

    def import_agents_md_as_rule(self, path: str | Path) -> dict[str, Any]:
        loaded = self.read_agents_md(path)
        if not loaded:
            raise ValueError("未找到可读的 AGENTS.md")
        existing = next(
            (
                item
                for item in self.rules.values()
                if item.get("source") == "agents_md" and item.get("path") == loaded["path"]
            ),
            None,
        )
        now = _now()
        if existing:
            existing["body"] = loaded["body"]
            existing["updated_at"] = now
            existing["enabled"] = True
            self.rules[existing["id"]] = self._normalize_rule(existing)
            self._save()
            return self.rules[existing["id"]]
        rule_id = str(uuid.uuid4())
        item = {
            "id": rule_id,
            "name": f"AGENTS.md · {Path(loaded['path']).parent.name}",
            "body": loaded["body"],
            "enabled": True,
            "priority": 5,
            "scope": "global",
            "session_id": None,
            "source": "agents_md",
            "path": loaded["path"],
            "created_at": now,
            "updated_at": now,
        }
        item = self._normalize_rule(item)
        self.rules[rule_id] = item
        self._save()
        return item

    # --- Resolve ---

    @staticmethod
    def _published_skill_view(skill: dict[str, Any]) -> dict[str, Any]:
        """Runtime view of the active published skill snapshot."""
        published = SkillsRulesService._find_version(
            skill.get("versions") or [],
            str(skill.get("published_version") or skill.get("version") or "1.0.0"),
        )
        if not published:
            return skill
        view = dict(skill)
        view["body"] = published.get("body") or skill.get("body")
        view["description"] = published.get("description", skill.get("description"))
        view["always_on"] = bool(published.get("always_on", skill.get("always_on")))
        view["trigger_keywords"] = list(published.get("trigger_keywords") or skill.get("trigger_keywords") or [])
        view["input_schema"] = published.get("input_schema", skill.get("input_schema"))
        view["output_schema"] = published.get("output_schema", skill.get("output_schema"))
        view["version"] = published.get("version")
        view["content_hash"] = published.get("content_hash") or _content_hash(str(view.get("body") or ""))
        return view

    @staticmethod
    def _published_rule_view(rule: dict[str, Any]) -> dict[str, Any]:
        published = SkillsRulesService._find_version(
            rule.get("versions") or [],
            str(rule.get("published_version") or "1.0.0"),
        )
        if not published:
            return rule
        view = dict(rule)
        view["body"] = published.get("body") or rule.get("body")
        view["version"] = published.get("version")
        view["content_hash"] = published.get("content_hash") or _content_hash(str(view.get("body") or ""))
        return view

    @staticmethod
    def _skill_matches(skill: dict[str, Any], user_message: str) -> bool:
        if not skill.get("enabled"):
            return False
        if skill.get("always_on"):
            return True
        keywords = [str(item).lower() for item in (skill.get("trigger_keywords") or []) if str(item).strip()]
        if not keywords:
            return False
        haystack = (user_message or "").lower()
        return any(keyword in haystack for keyword in keywords)

    def resolve(
        self,
        *,
        user_message: str = "",
        session_id: str | None = None,
        workspace_roots: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build instruction context from **published** layers + AGENTS.md."""
        layers: list[dict[str, Any]] = []
        sections: list[str] = []

        for root in workspace_roots or []:
            loaded = self.read_agents_md(root)
            if not loaded:
                continue
            layers.append(
                {
                    "kind": "agents_md",
                    "id": loaded["id"],
                    "name": loaded["name"],
                    "version": loaded["version"],
                    "content_hash": loaded["content_hash"],
                    "path": loaded["path"],
                }
            )
            sections.append(f"### AGENTS.md（{loaded['path']}）\n{loaded['body']}")

        for rule in self.list_rules(session_id=session_id):
            if not rule.get("enabled"):
                continue
            if rule.get("scope") == "session" and rule.get("session_id") not in {None, session_id}:
                continue
            published = self._published_rule_view(rule)
            layers.append(
                {
                    "kind": "rule",
                    "id": published["id"],
                    "name": published["name"],
                    "version": published.get("version") or published.get("published_version") or "1.0.0",
                    "content_hash": published.get("content_hash"),
                    "priority": published.get("priority"),
                    "source": published.get("source"),
                    "draft_dirty": bool(rule.get("draft_dirty")),
                }
            )
            sections.append(f"### 规则：{published['name']}（v{layers[-1]['version']}）\n{published['body']}")

        for skill in self.list_skills():
            published = self._published_skill_view(skill)
            if not self._skill_matches(published, user_message):
                continue
            version = published.get("version") or "1.0.0"
            layers.append(
                {
                    "kind": "skill",
                    "id": published["id"],
                    "name": published["name"],
                    "version": version,
                    "content_hash": published.get("content_hash"),
                    "draft_dirty": bool(skill.get("draft_dirty")),
                }
            )
            description = published.get("description") or ""
            header = f"### 技能：{published['name']}（v{version}）"
            if description:
                header += f"\n{description}"
            schema_bits = [
                _format_schema_block("输入契约", published.get("input_schema")),
                _format_schema_block("输出契约", published.get("output_schema")),
            ]
            schema_text = "\n".join(bit for bit in schema_bits if bit)
            body = published.get("body") or ""
            if schema_text:
                sections.append(f"{header}\n{schema_text}\n\n{body}")
            else:
                sections.append(f"{header}\n{body}")

        if not sections:
            return {"context": "", "manifest": [], "layer_count": 0}

        context = (
            "以下是 AetherChat 分层指令（Rules / Skills / AGENTS.md，均为已发布版本）。"
            "按出现顺序遵守；与用户明确要求冲突时以用户当次要求为准。\n\n"
            + "\n\n".join(sections)
        )
        return {
            "context": context,
            "manifest": layers,
            "layer_count": len(layers),
        }

    def resolve_for_session(self, session_id: str, user_message: str = "") -> dict[str, Any]:
        roots: list[str] = []
        try:
            from .chat_database import chat_database

            for repo in chat_database.list_workspace_repositories(session_id):
                root = repo.get("root_path")
                if root:
                    roots.append(str(root))
        except Exception:
            roots = []
        return self.resolve(
            user_message=user_message,
            session_id=session_id,
            workspace_roots=roots,
        )


skills_rules_service = SkillsRulesService()
