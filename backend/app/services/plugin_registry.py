from __future__ import annotations

import builtins
import json
import subprocess
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from string import Template
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from ..models.schemas import ToolDefinition
from .artifact_service import ArtifactService, MergeProposalStore, _data_root, _lock_for, _utc_now

Permission = Literal[
    "external_navigation",
    "window_metadata",
    "workspace_read",
    "task_read",
    "tool_read",
    "process_read",
    "local_file_read",
    "local_file_write",
    "mouse_control",
    "keyboard_control",
    "screen_capture",
    "app_launch",
]


class PluginAction(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    handler: str = "template"
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    output_template: str = "插件动作 ${action} 已执行。"
    model_callable: bool = True


class PluginManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
    description: str = Field(min_length=1, max_length=500)
    author: str = Field(default="AetherChat", max_length=80)
    category: str = Field(default="Productivity", max_length=50)
    icon: str = Field(default="Puzzle", max_length=40)
    permissions: list[Permission] = Field(default_factory=list)
    actions: list[PluginAction] = Field(min_length=1, max_length=20)
    homepage: str | None = None

    @field_validator("actions")
    @classmethod
    def unique_actions(cls, value: list[PluginAction]) -> list[PluginAction]:
        action_ids = [action.id for action in value]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Plugin action ids must be unique")
        return value


class PluginUpdate(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class PluginInvoke(BaseModel):
    action: str
    session_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def _action(
    action_id: str,
    label: str,
    description: str,
    handler: str,
    *,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    model_callable: bool = True,
) -> PluginAction:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return PluginAction(
        id=action_id,
        label=label,
        description=description,
        handler=handler,
        input_schema=schema,
        model_callable=model_callable,
    )


BUILTIN_PLUGINS = [
    PluginManifest(
        id="desktop-control",
        name="本地电脑控制",
        version="2.0.0",
        description="Codex 式模型电脑控制：观察屏幕与无障碍树，并在本机授权后点击、输入、滚动、拖拽、启动应用和编辑文档。",
        category="Desktop",
        icon="Laptop",
        permissions=[
            "window_metadata",
            "local_file_read",
            "local_file_write",
            "mouse_control",
            "keyboard_control",
            "screen_capture",
            "app_launch",
        ],
        actions=[
            _action("capabilities", "查看能力", "返回模型在当前平台可用的 Computer Use 动作。", "desktop_capabilities"),
            _action("list_apps", "列出应用", "列出当前带可见窗口的本机应用。", "computer_list_apps"),
            _action("list_windows", "列出窗口", "列出可见窗口、窗口 ID、进程与屏幕边界。", "computer_list_windows"),
            _action(
                "get_state",
                "观察窗口",
                "获取窗口或整个桌面的截图与 UI Automation 树；元素同时返回稳定 element_id、选择器和临时 index。",
                "computer_get_state",
                properties={
                    "window_id": {"type": "integer", "description": "省略时观察整个虚拟桌面"},
                    "include_screenshot": {"type": "boolean", "description": "默认 true"},
                    "include_accessibility": {"type": "boolean", "description": "默认 true"},
                },
            ),
            _action(
                "activate_window",
                "激活窗口",
                "把指定窗口恢复并置于前台。",
                "computer_activate_window",
                properties={"window_id": {"type": "integer"}},
                required=["window_id"],
            ),
            _action(
                "click",
                "点击",
                "优先按稳定 element_id 或元素文本模糊定位；快照失效/聚焦失败时可自动刷新并重试一次，也兼容 index/坐标。",
                "computer_click",
                properties={
                    "window_id": {"type": "integer"},
                    "snapshot_id": {"type": "string"},
                    "element_index": {"type": "integer"},
                    "element_id": {"type": "string", "description": "get_state 返回的稳定 UIA 标识"},
                    "element_text": {"type": "string", "description": "要模糊匹配的可见文本"},
                    "control_type": {"type": "string", "description": "可选 UIA 控件类型，例如 Button"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "coordinate_space": {"type": "string", "enum": ["window", "screen"]},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "click_count": {"type": "integer", "minimum": 1, "maximum": 3},
                    "auto_retry": {"type": "boolean", "description": "默认 true"},
                },
                required=["window_id"],
            ),
            _action(
                "type_text",
                "输入文本",
                "向当前焦点或指定快照元素输入 Unicode 文本。",
                "computer_type_text",
                properties={
                    "window_id": {"type": "integer"},
                    "text": {"type": "string"},
                    "snapshot_id": {"type": "string"},
                    "element_index": {"type": "integer"},
                    "element_id": {"type": "string"},
                    "element_text": {"type": "string"},
                    "control_type": {"type": "string"},
                    "auto_retry": {"type": "boolean", "description": "默认 true"},
                },
                required=["window_id", "text"],
            ),
            _action(
                "press_key",
                "按键",
                "发送单键或 Ctrl/Shift/Alt/Win 组合键，例如 Control_L+a、Return。",
                "computer_press_key",
                properties={"window_id": {"type": "integer"}, "key": {"type": "string"}},
                required=["window_id", "key"],
            ),
            _action(
                "set_value",
                "设置输入值",
                "按稳定 ID、文本或快照 index 定位可编辑元素，执行全选并输入新值。",
                "computer_set_value",
                properties={
                    "window_id": {"type": "integer"},
                    "snapshot_id": {"type": "string"},
                    "element_index": {"type": "integer"},
                    "element_id": {"type": "string"},
                    "element_text": {"type": "string"},
                    "control_type": {"type": "string"},
                    "value": {"type": "string"},
                    "auto_retry": {"type": "boolean", "description": "默认 true"},
                },
                required=["window_id", "value"],
            ),
            _action(
                "scroll",
                "滚动",
                "在窗口坐标处水平或垂直滚动；正 scroll_y 表示向下。",
                "computer_scroll",
                properties={
                    "window_id": {"type": "integer"},
                    "scroll_y": {"type": "integer"},
                    "scroll_x": {"type": "integer"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "coordinate_space": {"type": "string", "enum": ["window", "screen"]},
                },
                required=["window_id", "scroll_y"],
            ),
            _action(
                "drag",
                "拖拽",
                "在窗口内从一个坐标拖拽到另一个坐标。",
                "computer_drag",
                properties={
                    "window_id": {"type": "integer"},
                    "from_x": {"type": "integer"}, "from_y": {"type": "integer"},
                    "to_x": {"type": "integer"}, "to_y": {"type": "integer"},
                    "coordinate_space": {"type": "string", "enum": ["window", "screen"]},
                    "duration_ms": {"type": "integer"},
                },
                required=["window_id", "from_x", "from_y", "to_x", "to_y"],
            ),
            _action(
                "launch_app",
                "启动应用",
                "启动一个已存在的 .exe 或 .lnk，不接受命令行参数。",
                "computer_launch_app",
                properties={"executable": {"type": "string"}},
                required=["executable"],
            ),
            _action(
                "list_directory",
                "浏览目录",
                "列出本机绝对目录，或当前会话工作区中的相对目录。",
                "computer_list_directory",
                properties={"path": {"type": "string"}},
                required=["path"],
            ),
            _action(
                "read_document",
                "读取文档",
                "读取本机绝对路径文档；相对路径会从当前会话工作区读取。",
                "computer_read_document",
                properties={"path": {"type": "string"}},
                required=["path"],
            ),
            _action(
                "write_document",
                "写入文档",
                (
                    "写入本机短文档；path 必须是绝对路径。TXT/MD/JSON/CSV/TSV "
                    "可用绝对 path 和 data.text 直接新建，结构化文档需提供 source_path；"
                    "覆盖时默认创建备份。长文必须先用 write_artifact 分块，再用 "
                    "documents.export_to_local 一次性导出。"
                ),
                "computer_write_document",
                properties={
                    "path": {"type": "string"},
                    "source_path": {"type": "string"},
                    "data": {"type": "object"},
                    "create_backup": {"type": "boolean"},
                },
                required=["path", "data"],
            ),
        ],
    ),
    PluginManifest(
        id="browser-links",
        name="浏览器链接",
        version="1.0.0",
        description="安全校验并通过系统默认浏览器打开 HTTP/HTTPS 链接。",
        category="Browser",
        icon="Globe2",
        permissions=["external_navigation"],
        actions=[_action(
            "open_url",
            "打开链接",
            "只允许 http:// 或 https:// 地址；用户指定 Firefox/火狐时 browser 必须设为 firefox。",
            "open_url",
            properties={
                "url": {"type": "string", "title": "链接地址"},
                "browser": {
                    "type": "string",
                    "title": "浏览器",
                    "enum": ["default", "firefox"],
                    "description": "默认浏览器或 Firefox",
                },
            },
            required=["url"],
        )],
    ),
    PluginManifest(
        id="comfyui",
        name="本地 ComfyUI 生成",
        version="1.1.0",
        description="调用本机 ComfyUI 的全部图像和视频模型；动态盘点模型，生成图像或视频，并执行已保存工作流。",
        category="Creative",
        icon="Images",
        permissions=["local_file_read", "process_read"],
        actions=[
            _action(
                "status",
                "检查 ComfyUI",
                (
                    "只读检查本机 ComfyUI、可生成图像模型、视频管线安全状态和工作流摘要；"
                    "视频可用性必须以 generation.video_pipelines[].available 为准，"
                    "不要根据 GPU/显存自行推断。具体节点映射与输入请调用 workflow_schema。"
                ),
                "comfyui_status",
            ),
            _action(
                "workflow_schema",
                "检查工作流输入",
                "返回一个已保存工作流的节点 ID/名称、依赖关系和全部可覆盖输入类型。",
                "comfyui_workflow_schema",
                properties={
                    "name": {"type": "string", "description": "status 返回的工作流 name"},
                },
                required=["name"],
            ),
            _action(
                "generate_image",
                "生成图像",
                (
                    "用本地 SDXL checkpoint 或 Qwen-Image 生成图像；model 可省略并自动选择"
                    "可用默认模型。BigASP v2 未显式给出采样参数时自动使用 steps=40、cfg=3.4。"
                ),
                "comfyui_generate_image",
                properties={
                    "prompt": {"type": "string", "description": "完整画面提示词"},
                    "model": {
                        "type": "string",
                        "description": "可选；status 返回的模型名，省略或传 auto 时自动选择",
                    },
                    "negative_prompt": {"type": "string"},
                    "width": {"type": "integer", "minimum": 256, "maximum": 2048},
                    "height": {"type": "integer", "minimum": 256, "maximum": 2048},
                    "steps": {"type": "integer", "minimum": 1, "maximum": 100},
                    "cfg": {"type": "number", "minimum": 0.1, "maximum": 30},
                    "seed": {"type": "integer"},
                    "lora": {"type": "string"},
                    "lora_strength": {"type": "number", "minimum": -4, "maximum": 4},
                    "task_id": {"type": "string", "description": "可选关联任务 ID"},
                },
                required=["prompt"],
            ),
            _action(
                "generate_video",
                "生成视频",
                (
                    "使用 wan-i2v 把本机图片转为视频，或使用 ltx-t2v 文生视频；"
                    "wan-i2v 可直接使用本条消息的图片附件，未提供图片时会自动生成起始图；"
                    "用户明确指定的视频模型优先。"
                ),
                "comfyui_generate_video",
                properties={
                    "prompt": {"type": "string", "description": "主体动作、镜头运动与画面描述"},
                    "pipeline": {"type": "string", "enum": ["wan-i2v", "ltx-t2v"]},
                    "model": {
                        "type": "string",
                        "description": "可选；status 返回的视频模型文件名",
                    },
                    "input_image": {
                        "type": "string",
                        "description": "可选；wan-i2v 的本机图片绝对路径，有聊天图片附件时由后端自动注入",
                    },
                    "width": {"type": "integer", "minimum": 256, "maximum": 2048},
                    "height": {"type": "integer", "minimum": 256, "maximum": 2048},
                    "frames": {"type": "integer", "minimum": 1, "maximum": 161},
                    "fps": {"type": "integer", "minimum": 1, "maximum": 60},
                    "steps": {"type": "integer", "minimum": 1, "maximum": 100},
                    "cfg": {"type": "number", "minimum": 0.1, "maximum": 30},
                    "seed": {"type": "integer"},
                    "task_id": {"type": "string", "description": "可选关联任务 ID"},
                },
                required=["prompt"],
            ),
            _action(
                "run_workflow",
                "运行已保存工作流",
                "运行 status 列出的本机 ComfyUI 工作流；可按节点 ID 覆盖输入，用于 Wan S2V 等完整流程。",
                "comfyui_run_workflow",
                properties={
                    "name": {"type": "string", "description": "status 返回的工作流 name"},
                    "overrides": {
                        "type": "object",
                        "description": "可选映射：节点 ID -> 要覆盖的 inputs 字段",
                    },
                    "task_id": {"type": "string", "description": "可选关联任务 ID"},
                },
                required=["name"],
            ),
        ],
    ),
    PluginManifest(
        id="windows-inspector",
        name="Windows 窗口检查",
        version="1.0.0",
        description="以只读方式列出当前可见窗口，作为 Computer Use 的安全兼容层。",
        category="Desktop",
        icon="Monitor",
        permissions=["window_metadata"],
        actions=[_action(
            "list_windows",
            "列出窗口",
            "返回可见窗口标题，不执行点击或输入。",
            "list_windows",
            model_callable=False,
        )],
    ),
    PluginManifest(
        id="documents",
        name="文档工作区",
        version="1.0.0",
        description="扫描当前会话中的 Markdown、文本和 Word 文档工件。",
        category="Files",
        icon="FileText",
        permissions=["workspace_read", "local_file_write"],
        actions=[
            _action("inventory", "扫描文档", "列出文档工件及大小。", "inventory_documents"),
            _action(
                "export_to_local",
                "导出到本机",
                "把会话工作区中的文本或 Markdown 文档保存到本机绝对路径；例如 /home/用户名/Desktop/文件.md。",
                "export_document",
                properties={
                    "path": {"type": "string", "description": "工作区相对路径"},
                    "destination_path": {"type": "string", "description": "本机绝对目标路径"},
                },
                required=["path", "destination_path"],
            ),
        ],
    ),
    PluginManifest(
        id="pdf",
        name="PDF 工具",
        version="1.0.0",
        description="发现当前会话中的 PDF 工件并返回文件清单。",
        category="Files",
        icon="FileSearch",
        permissions=["workspace_read"],
        actions=[_action("inventory", "扫描 PDF", "列出 PDF 工件及大小。", "inventory_pdf")],
    ),
    PluginManifest(
        id="spreadsheets",
        name="表格工具",
        version="1.0.0",
        description="发现 CSV、TSV、Excel 等表格工件。",
        category="Files",
        icon="Table2",
        permissions=["workspace_read"],
        actions=[_action("inventory", "扫描表格", "列出表格工件及大小。", "inventory_spreadsheets")],
    ),
    PluginManifest(
        id="presentations",
        name="演示文稿工具",
        version="1.0.0",
        description="发现 PowerPoint、OpenDocument 演示文稿工件。",
        category="Files",
        icon="Presentation",
        permissions=["workspace_read"],
        actions=[_action("inventory", "扫描演示文稿", "列出演示文稿工件及大小。", "inventory_presentations")],
    ),
    PluginManifest(
        id="github-readonly",
        name="GitHub / Git 只读助手",
        version="1.0.0",
        description="读取当前工作区的 Git 分支和变更摘要，不提交、不推送。",
        category="Developer",
        icon="GitBranch",
        permissions=["workspace_read", "process_read"],
        actions=[_action("status", "读取 Git 状态", "执行只读 git status 检查。", "git_status")],
    ),
    PluginManifest(
        id="visualize",
        name="任务可视化",
        version="1.0.0",
        description="把当前任务状态转换为可用于图表的结构化数据。",
        category="Analytics",
        icon="ChartNoAxesColumn",
        permissions=["task_read"],
        actions=[_action("task_chart", "生成任务图表数据", "统计六个任务阶段。", "task_chart")],
    ),
    PluginManifest(
        id="qa-diagnostics",
        name="应用 QA 诊断",
        version="1.0.0",
        description="汇总会话工件、任务、合并提案和工具数量，用于本机冒烟检查。",
        category="Developer",
        icon="ShieldCheck",
        permissions=["workspace_read", "task_read", "tool_read"],
        actions=[_action("workspace_health", "工作区健康检查", "执行无模型、无外部请求的只读检查。", "workspace_health")],
    ),
]


class PluginRegistry:
    """Durable, declarative plugin registry.

    Built-ins may use a small allowlist of trusted handlers. User-installed
    plugins are template-only, so importing a manifest never executes Python,
    JavaScript, shell commands, or downloaded binaries.
    """

    def __init__(self, base_path: str | Path | None = None):
        root = _data_root(base_path) / "plugins"
        root.mkdir(parents=True, exist_ok=True)
        self.registry_path = root / "registry.json"
        self.state_path = root / "state.json"
        self.artifacts = ArtifactService(base_path)
        self.merges = MergeProposalStore(base_path)

    def list(self) -> list[dict[str, Any]]:
        manifests = {item.id: self._decorate(item.model_dump(), builtin=True) for item in BUILTIN_PLUGINS}
        for custom in self._load_custom():
            manifests[custom.id] = self._decorate(custom.model_dump(), builtin=False)
        return sorted(manifests.values(), key=lambda item: (item["category"], item["name"]))

    def get(self, plugin_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list() if item["id"] == plugin_id), None)

    def install(self, payload: PluginManifest) -> dict[str, Any]:
        if any(item.id == payload.id for item in BUILTIN_PLUGINS):
            raise ValueError("Built-in plugin ids cannot be replaced")
        for action in payload.actions:
            if action.handler != "template":
                raise ValueError("Custom plugins may only use the template handler")
        with _lock_for(self.registry_path):
            custom = {item.id: item for item in self._load_custom_unlocked()}
            custom[payload.id] = payload
            self._write_json(self.registry_path, {"version": 1, "plugins": [item.model_dump() for item in custom.values()]})
        return self.get(payload.id) or {}

    def uninstall(self, plugin_id: str) -> dict[str, Any] | None:
        if any(item.id == plugin_id for item in BUILTIN_PLUGINS):
            raise PermissionError("Built-in plugins cannot be uninstalled")
        with _lock_for(self.registry_path):
            custom = {item.id: item for item in self._load_custom_unlocked()}
            removed = custom.pop(plugin_id, None)
            if removed is not None:
                self._write_json(self.registry_path, {"version": 1, "plugins": [item.model_dump() for item in custom.values()]})
        return removed.model_dump() if removed else None

    def update(self, plugin_id: str, payload: PluginUpdate) -> dict[str, Any] | None:
        if self.get(plugin_id) is None:
            return None
        with _lock_for(self.state_path):
            states = self._load_states_unlocked()
            current = states.setdefault(plugin_id, {"enabled": True, "config": {}})
            if payload.enabled is not None:
                current["enabled"] = payload.enabled
            if payload.config is not None:
                encoded = json.dumps(payload.config, ensure_ascii=False)
                if len(encoded.encode("utf-8")) > 32_768:
                    raise ValueError("Plugin config is too large")
                current["config"] = payload.config
            current["updated_at"] = _utc_now()
            self._write_json(self.state_path, {"version": 1, "plugins": states})
        return self.get(plugin_id)

    async def invoke(
        self,
        plugin_id: str,
        payload: PluginInvoke,
        *,
        caller: str = "user",
    ) -> dict[str, Any]:
        plugin = self.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        if not plugin["enabled"]:
            raise PermissionError("Plugin is disabled")
        action = next((item for item in plugin["actions"] if item["id"] == payload.action), None)
        if action is None:
            raise KeyError(payload.action)
        self.artifacts.normalize_session_id(payload.session_id)
        self._validate_arguments(action.get("input_schema") or {}, payload.arguments)
        supports_task_link = "task_id" in (
            (action.get("input_schema") or {}).get("properties") or {}
        )
        linked_task_id = (
            str(payload.arguments.get("task_id") or "").strip()
            if supports_task_link
            else ""
        )
        task_started = False
        if linked_task_id:
            from ..routers.tasks import begin_task_tool

            begin_task_tool(linked_task_id, payload.session_id, caller)
            task_started = True
        try:
            result = await self._dispatch(action, payload.session_id, payload.arguments)
            if plugin_id == "comfyui" and isinstance(result, dict):
                result = await self._persist_comfyui_metadata(
                    payload.session_id,
                    payload.action,
                    caller,
                    result,
                )
        except Exception:
            if task_started:
                from ..routers.tasks import finish_task_tool

                finish_task_tool(linked_task_id, payload.session_id, success=False)
            raise
        if task_started:
            from ..routers.tasks import finish_task_tool

            synchronized = finish_task_tool(
                linked_task_id,
                payload.session_id,
                success=True,
            )
            if isinstance(result, dict):
                result["task_sync"] = synchronized
        return {
            "plugin_id": plugin_id,
            "plugin_name": plugin["name"],
            "action": payload.action,
            "status": "success",
            "result": result,
            "created_at": _utc_now(),
        }

    def tool_definitions(self) -> builtins.list[ToolDefinition]:
        definitions: builtins.list[ToolDefinition] = []
        for plugin in self.list():
            if not plugin["enabled"]:
                continue
            for action in plugin["actions"]:
                if not action.get("model_callable", True):
                    continue
                definitions.append(ToolDefinition(
                    name=self.tool_name(plugin["id"], action["id"]),
                    description=f"{plugin['name']}：{action['description'] or action['label']}",
                    parameters=deepcopy(action.get("input_schema") or {"type": "object", "properties": {}}),
                ))
        return definitions

    async def invoke_tool(
        self,
        tool_name: str,
        session_id: str,
        caller: str,
        arguments: dict[str, Any],
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        match = self.resolve_tool_name(tool_name)
        if match is None:
            raise KeyError(tool_name)
        from .platform_governance import platform_governance

        platform_governance.require_tool_invoke(
            tool_name,
            project_id=project_id,
            session_id=session_id,
        )
        plugin_id, action_id = match
        return await self.invoke(
            plugin_id,
            PluginInvoke(
                action=action_id,
                session_id=session_id,
                arguments=arguments,
            ),
            caller=caller,
        )

    def resolve_tool_name(self, tool_name: str) -> tuple[str, str] | None:
        for plugin in self.list():
            for action in plugin["actions"]:
                if self.tool_name(plugin["id"], action["id"]) == tool_name:
                    return plugin["id"], action["id"]
        return None

    @staticmethod
    def tool_name(plugin_id: str, action_id: str) -> str:
        return f"plugin__{plugin_id.replace('-', '_')}__{action_id.replace('-', '_')}"

    def _decorate(self, manifest: dict[str, Any], *, builtin: bool) -> dict[str, Any]:
        state = self._load_states().get(manifest["id"], {})
        return {
            **manifest,
            "builtin": builtin,
            "enabled": bool(state.get("enabled", True)),
            "config": deepcopy(state.get("config", {})),
            "status": "ready",
        }

    async def _persist_comfyui_metadata(
        self,
        session_id: str,
        action_id: str,
        caller: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        raw_metadata = result.get("generation_metadata")
        if not isinstance(raw_metadata, dict):
            return result
        prompt_id = str(
            raw_metadata.get("prompt_id")
            or result.get("prompt_id")
            or "unknown"
        )
        safe_prompt_id = "".join(
            character
            for character in prompt_id[:160]
            if character.isalnum() or character in {"-", "_"}
        ) or "unknown"
        staged_outputs: list[dict[str, Any]] = []
        generated_files: list[dict[str, Any]] = []
        for index, raw_file in enumerate(result.get("files") or []):
            if not isinstance(raw_file, dict):
                continue
            public_file = {
                str(key): value
                for key, value in raw_file.items()
                if key != "image_data_url" and not str(key).startswith("_")
            }
            source_path = raw_file.get("_local_path")
            filename = Path(str(raw_file.get("filename") or f"output-{index + 1}")).name
            safe_filename = "".join(
                character
                for character in filename[:220]
                if character.isalnum() or character in {"-", "_", "."}
            ) or f"output-{index + 1}"
            if isinstance(source_path, str) and source_path:
                output_artifact_path = (
                    f"generated/comfyui/{safe_prompt_id}/{safe_filename}"
                )
                try:
                    imported = await self.artifacts.import_file(
                        session_id,
                        output_artifact_path,
                        source_path,
                        f"Import ComfyUI output {filename}",
                        caller,
                        source_model=caller,
                        status="ready",
                    )
                except (OSError, ValueError):
                    imported = None
                if imported is not None:
                    public_file["artifact_path"] = output_artifact_path
                    generated_files.append({
                        "path": output_artifact_path,
                        "name": filename,
                        "mime_type": imported.get("mime_type"),
                        "size_bytes": imported.get("size_bytes", 0),
                        "kind": "generated_media",
                        "source": "comfyui",
                    })
            staged_outputs.append(public_file)
        result = {
            **result,
            "files": staged_outputs,
        }
        raw_metadata = {
            **raw_metadata,
            "outputs": staged_outputs,
        }
        envelope = {
            **deepcopy(raw_metadata),
            "session_id": session_id,
            "producer": {
                "agent": caller,
                "plugin": "comfyui",
                "action": action_id,
            },
        }
        artifact_path = f"generated/comfyui/{safe_prompt_id}.metadata.json"
        written = await self.artifacts.write(
            session_id,
            artifact_path,
            json.dumps(envelope, ensure_ascii=False, indent=2, default=str),
            f"Record ComfyUI generation metadata for {safe_prompt_id}",
            caller,
            source_model=caller,
            status="ready",
        )
        generated_files.append({
            "path": artifact_path,
            "name": Path(artifact_path).name,
            "mime_type": written.get("mime_type"),
            "size_bytes": written.get("size_bytes", 0),
            "kind": "metadata",
            "source": "comfyui",
        })
        files = raw_metadata.get("outputs") or []
        first_output = files[0] if files and isinstance(files[0], dict) else {}
        entity_value = str(
            first_output.get("url")
            or first_output.get("filename")
            or artifact_path
        )
        from .memory_service import memory_service

        memory_service.upsert_entity(
            session_id,
            key=f"asset:comfyui:{safe_prompt_id}",
            value=entity_value,
            kind="generated_asset",
            source=f"{caller}:{action_id}",
            metadata={
                "artifact_path": artifact_path,
                "operation": raw_metadata.get("operation"),
                "model": (
                    (raw_metadata.get("parameters") or {}).get("model")
                    or ",".join((raw_metadata.get("provenance") or {}).get("models") or [])
                ),
                "seed": (
                    (raw_metadata.get("parameters") or {}).get("seed")
                    or next(iter((raw_metadata.get("provenance") or {}).get("seeds") or []), None)
                ),
            },
        )
        return {
            **result,
            "generation_metadata": raw_metadata,
            "metadata_artifact": {
                **written,
                "schema": envelope.get("schema"),
                "artifact_type": envelope.get("artifact_type"),
            },
            "generated_files": generated_files,
        }

    async def _dispatch(self, action: dict[str, Any], session_id: str, arguments: dict[str, Any]) -> Any:
        handler = action.get("handler", "template")
        if handler == "template":
            values = {"action": action["id"], "input": json.dumps(arguments, ensure_ascii=False), **arguments}
            return {"text": Template(action.get("output_template") or "").safe_substitute(values)}
        if handler == "open_url":
            url = str(arguments.get("url") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Only valid http:// or https:// URLs are allowed")
            browser = str(arguments.get("browser") or "default").strip().lower()
            if browser not in {"default", "firefox"}:
                raise ValueError("Browser must be default or firefox")
            return {"kind": "open_external", "url": url, "browser": browser}
        if handler == "list_windows":
            return {"windows": self._visible_windows()}
        if handler == "desktop_capabilities":
            return {
                "file_read": True,
                "file_write": True,
                "document_formats": ["txt", "md", "json", "csv", "tsv", "docx", "xlsx", "pptx", "pdf"],
                "observation": ["desktop_screenshot", "window_screenshot", "uia_accessibility_tree"],
                "actions": ["activate", "click", "type_text", "press_key", "set_value", "scroll", "drag", "launch_app"],
                "mouse": "model_with_native_consent",
                "keyboard": "model_with_native_consent",
                "screenshot": "model_with_native_consent",
                "model_callable": True,
                "safety": "首次使用需在 Electron 原生对话框授权；文件写入和启动应用始终逐次确认。",
            }
        if handler.startswith("comfyui_"):
            from .comfyui_service import comfyui_service

            plugin = self.get("comfyui") or {}
            raw_config = plugin.get("config")
            config: dict[str, Any] = (
                raw_config if isinstance(raw_config, dict) else {}
            )
            selection = {
                "image_model": str(config.get("image_model") or "").strip() or None,
                "video_pipeline": str(config.get("video_pipeline") or "").strip() or None,
            }

            if handler == "comfyui_status":
                result = await comfyui_service.status()
                result["selection"] = selection
                return result
            if handler == "comfyui_workflow_schema":
                return await comfyui_service.workflow_schema(arguments["name"])
            if handler == "comfyui_generate_image":
                user_requested_model = str(
                    arguments.get("_user_requested_model") or ""
                ).strip() or None
                return await comfyui_service.generate_image(
                    arguments["prompt"],
                    model=(
                        user_requested_model
                        or selection["image_model"]
                        or arguments.get("model")
                    ),
                    negative_prompt=arguments.get(
                        "negative_prompt",
                        "blurry, low quality, distorted, watermark, text, bad anatomy",
                    ),
                    width=arguments.get("width", 768),
                    height=arguments.get("height", 768),
                    steps=arguments.get("steps"),
                    cfg=arguments.get("cfg"),
                    seed=arguments.get("seed"),
                    lora=arguments.get("lora"),
                    lora_strength=arguments.get("lora_strength", 1.0),
                )
            if handler == "comfyui_generate_video":
                user_requested_model = str(
                    arguments.get("_user_requested_model") or ""
                ).strip() or None
                return await comfyui_service.generate_video(
                    arguments["prompt"],
                    pipeline=(
                        arguments.get("pipeline", "wan-i2v")
                        if user_requested_model
                        else selection["video_pipeline"]
                        or arguments.get("pipeline", "wan-i2v")
                    ),
                    model=user_requested_model or arguments.get("model"),
                    input_image=arguments.get("input_image"),
                    width=arguments.get("width", 480),
                    height=arguments.get("height", 832),
                    frames=arguments.get("frames", 17),
                    fps=arguments.get("fps", 16),
                    steps=arguments.get("steps", 8),
                    cfg=arguments.get("cfg", 4.0),
                    seed=arguments.get("seed"),
                )
            if handler == "comfyui_run_workflow":
                return await comfyui_service.run_workflow(
                    arguments["name"], arguments.get("overrides")
                )
        if handler == "export_document":
            from .computer_use_consent import require_computer_use_consent
            from .desktop_service import DesktopDocumentService

            source_path = str(arguments["path"])
            destination_path = str(arguments["destination_path"])
            content = await self.artifacts.read(session_id, source_path)
            await require_computer_use_consent(
                "导出工作区文档",
                f"从 {source_path} 写入 {destination_path}",
                risk="file_write",
            )
            result = await DesktopDocumentService().write(
                destination_path,
                {"text": content, "encoding": "utf-8"},
            )
            return {
                **result,
                "workspace_path": source_path,
                "destination_path": str(Path(destination_path).expanduser().resolve()),
            }
        if handler.startswith("computer_"):
            return await self._dispatch_computer_use(handler, arguments, session_id)
        if handler.startswith("inventory_"):
            extension_sets = {
                "inventory_documents": {".md", ".txt", ".doc", ".docx", ".rtf"},
                "inventory_pdf": {".pdf"},
                "inventory_spreadsheets": {".csv", ".tsv", ".xls", ".xlsx", ".ods"},
                "inventory_presentations": {".ppt", ".pptx", ".odp"},
            }
            return await self._inventory(session_id, extension_sets[handler])
        if handler == "git_status":
            root = Path(await self.artifacts.prepare_checkout(session_id))
            return self._git_status(root)
        if handler == "task_chart":
            from ..routers.tasks import fake_tasks

            statuses = ["backlog", "claimed", "in_progress", "review", "merged", "done"]
            session_tasks = [task for task in fake_tasks if str(task.session_id) == str(session_id)]
            return {
                "labels": statuses,
                "values": [sum(task.status == status for task in session_tasks) for status in statuses],
                "total": len(session_tasks),
            }
        if handler == "workspace_health":
            from ..routers.tasks import fake_tasks
            from .tool_registry import tool_registry

            artifacts = await self.artifacts.list_files(session_id)
            tasks = [task for task in fake_tasks if str(task.session_id) == str(session_id)]
            return {
                "healthy": True,
                "session_id": session_id,
                "artifacts": len(artifacts),
                "tasks": len(tasks),
                "open_merges": sum(item.get("status") == "open" for item in self.merges.list(session_id)),
                "tools": len(tool_registry.list(session_id)),
            }
        raise ValueError(f"Unsupported plugin handler: {handler}")

    async def _dispatch_computer_use(
        self,
        handler: str,
        arguments: dict[str, Any],
        session_id: str,
    ) -> Any:
        from .computer_use_consent import require_computer_use_consent
        from .computer_use_service import computer_use_service
        from .desktop_service import DesktopDocumentService

        path_argument = str(arguments.get("path") or "").strip()
        is_absolute_path = bool(
            Path(path_argument).expanduser().is_absolute()
            or PureWindowsPath(path_argument).is_absolute()
        )
        if handler == "computer_read_document" and path_argument and not is_absolute_path:
            content = await self.artifacts.read(session_id, path_argument)
            return {
                "name": Path(path_argument).name,
                "format": Path(path_argument).suffix.lower().removeprefix("."),
                "size_bytes": len(content.encode("utf-8")),
                "editor_mode": "text",
                "data": {"text": content, "encoding": "utf-8"},
                "source": "session_workspace",
                "workspace_path": path_argument,
            }
        if handler == "computer_write_document" and not is_absolute_path:
            raise ValueError(
                "write_document path must be absolute; use write_artifact for a "
                "workspace-relative path, then export_to_local"
            )
        if handler == "computer_list_directory" and path_argument and not is_absolute_path:
            normalized = "" if path_argument in {".", "./"} else self.artifacts.normalize_path(path_argument)
            prefix = f"{normalized}/" if normalized else ""
            entries: dict[str, dict[str, Any]] = {}
            for artifact in await self.artifacts.list_files(session_id):
                artifact_path = str(artifact["path"])
                if not artifact_path.startswith(prefix):
                    continue
                remainder = artifact_path[len(prefix):]
                name, separator, _tail = remainder.partition("/")
                entries.setdefault(name, {
                    "name": name,
                    "type": "directory" if separator else "file",
                    "size_bytes": None if separator else artifact.get("size_bytes", 0),
                })
            return {
                "path": normalized or ".",
                "source": "session_workspace",
                "entries": sorted(entries.values(), key=lambda item: (item["type"] != "directory", item["name"])),
            }

        labels = {
            "computer_list_apps": "列出当前应用",
            "computer_list_windows": "列出当前窗口",
            "computer_get_state": "观察屏幕与无障碍树",
            "computer_activate_window": "激活窗口",
            "computer_click": "执行鼠标点击",
            "computer_type_text": "向窗口输入文本",
            "computer_press_key": "发送键盘按键",
            "computer_set_value": "修改界面输入值",
            "computer_scroll": "滚动窗口",
            "computer_drag": "拖拽鼠标",
            "computer_launch_app": "启动本机应用",
            "computer_list_directory": "浏览本机目录",
            "computer_read_document": "读取本机文档",
            "computer_write_document": "写入本机文档",
        }
        window_id = arguments.get("window_id")
        path_value = str(arguments.get("path") or arguments.get("executable") or "")
        if handler in {"computer_type_text", "computer_set_value"}:
            text_value = arguments.get("text") if handler == "computer_type_text" else arguments.get("value")
            detail = f"窗口 ID {window_id}；输入 {len(str(text_value or ''))} 个字符。"
        elif path_value:
            detail = f"目标：{Path(path_value).name or path_value}"
        elif window_id is not None:
            detail = f"窗口 ID：{window_id}"
        else:
            detail = "模型将读取当前桌面状态，并仅把结果用于本次 AetherChat 会话。"
        risk = "computer_control"
        if handler in {"computer_get_state", "computer_list_apps", "computer_list_windows"}:
            risk = "screen_read"
        elif handler in {"computer_list_directory", "computer_read_document"}:
            risk = "file_read"
        elif handler == "computer_write_document":
            risk = "file_write"
        elif handler == "computer_launch_app":
            risk = "app_launch"
        await require_computer_use_consent(labels.get(handler, handler), detail, risk=risk)

        if handler == "computer_list_apps":
            return await computer_use_service.list_apps()
        if handler == "computer_list_windows":
            return await computer_use_service.list_windows()
        if handler == "computer_get_state":
            return await computer_use_service.get_state(
                window_id=arguments.get("window_id"),
                include_screenshot=arguments.get("include_screenshot", True),
                include_accessibility=arguments.get("include_accessibility", True),
            )
        if handler == "computer_activate_window":
            return await computer_use_service.activate_window(arguments["window_id"])
        if handler == "computer_click":
            return await computer_use_service.click(
                arguments["window_id"],
                snapshot_id=arguments.get("snapshot_id"),
                element_index=arguments.get("element_index"),
                element_id=arguments.get("element_id"),
                element_text=arguments.get("element_text"),
                control_type=arguments.get("control_type"),
                x=arguments.get("x"),
                y=arguments.get("y"),
                coordinate_space=arguments.get("coordinate_space", "window"),
                button=arguments.get("button", "left"),
                click_count=arguments.get("click_count", 1),
                auto_retry=arguments.get("auto_retry", True),
            )
        if handler == "computer_type_text":
            return await computer_use_service.type_text(
                arguments["window_id"],
                arguments["text"],
                snapshot_id=arguments.get("snapshot_id"),
                element_index=arguments.get("element_index"),
                element_id=arguments.get("element_id"),
                element_text=arguments.get("element_text"),
                control_type=arguments.get("control_type"),
                auto_retry=arguments.get("auto_retry", True),
            )
        if handler == "computer_press_key":
            return await computer_use_service.press_key(arguments["window_id"], arguments["key"])
        if handler == "computer_set_value":
            return await computer_use_service.set_value(
                arguments["window_id"],
                arguments.get("snapshot_id"),
                arguments.get("element_index"),
                arguments["value"],
                element_id=arguments.get("element_id"),
                element_text=arguments.get("element_text"),
                control_type=arguments.get("control_type"),
                auto_retry=arguments.get("auto_retry", True),
            )
        if handler == "computer_scroll":
            return await computer_use_service.scroll(
                arguments["window_id"],
                scroll_y=arguments["scroll_y"],
                scroll_x=arguments.get("scroll_x", 0),
                x=arguments.get("x"),
                y=arguments.get("y"),
                coordinate_space=arguments.get("coordinate_space", "window"),
            )
        if handler == "computer_drag":
            return await computer_use_service.drag(
                arguments["window_id"],
                from_x=arguments["from_x"],
                from_y=arguments["from_y"],
                to_x=arguments["to_x"],
                to_y=arguments["to_y"],
                coordinate_space=arguments.get("coordinate_space", "window"),
                duration_ms=arguments.get("duration_ms", 500),
            )
        if handler == "computer_launch_app":
            return await computer_use_service.launch_app(arguments["executable"])
        if handler == "computer_list_directory":
            return await computer_use_service.list_directory(arguments["path"])
        documents = DesktopDocumentService()
        if handler == "computer_read_document":
            return await documents.read(arguments["path"])
        if handler == "computer_write_document":
            return await documents.write(
                arguments["path"],
                arguments["data"],
                source_path_value=arguments.get("source_path"),
                create_backup=arguments.get("create_backup", True),
            )
        raise ValueError(f"Unsupported Computer Use handler: {handler}")

    async def _inventory(self, session_id: str, extensions: set[str]) -> dict[str, Any]:
        root = Path(await self.artifacts.prepare_checkout(session_id))
        items = []
        for item in await self.artifacts.list_files(session_id):
            if Path(item["path"]).suffix.lower() in extensions:
                full_path = root / Path(*item["path"].split("/"))
                items.append({"path": item["path"], "kind": item["kind"], "size_bytes": full_path.stat().st_size})
        return {"files": items, "count": len(items)}

    @staticmethod
    def _git_status(root: Path) -> dict[str, Any]:
        if not (root / ".git").exists():
            return {"available": False, "message": "当前 AetherChat 工件目录尚未初始化为 Git 仓库。"}
        try:
            completed = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "message": str(exc)}
        return {
            "available": completed.returncode == 0,
            "output": completed.stdout.strip(),
            "error": completed.stderr.strip() if completed.returncode else "",
        }

    @staticmethod
    def _visible_windows() -> builtins.list[dict[str, Any]]:
        try:
            import win32gui
        except ImportError:
            return []
        windows: builtins.list[dict[str, Any]] = []

        def collect(hwnd: int, _extra: Any) -> None:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                if title:
                    windows.append({"title": title[:200]})

        win32gui.EnumWindows(collect, None)
        return windows[:100]

    @staticmethod
    def _validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
        if not isinstance(arguments, dict):
            raise ValueError("Plugin arguments must be an object")
        for name in schema.get("required") or []:
            if name not in arguments or arguments[name] is None or arguments[name] == "":
                raise ValueError(f"Missing required argument: {name}")
        expected_types: dict[str, type[Any] | tuple[type[Any], ...]] = {
            "string": str,
            "boolean": bool,
            "object": dict,
            "array": list,
            "integer": int,
            "number": (int, float),
        }
        for name, spec in (schema.get("properties") or {}).items():
            if name not in arguments or not isinstance(spec, dict) or not spec.get("type"):
                continue
            expected = expected_types.get(spec["type"])
            value = arguments[name]
            if expected and (not isinstance(value, expected) or spec["type"] in {"integer", "number"} and isinstance(value, bool)):
                raise ValueError(f"Invalid argument type for {name}: expected {spec['type']}")

    def _load_custom(self) -> builtins.list[PluginManifest]:
        with _lock_for(self.registry_path):
            return self._load_custom_unlocked()

    def _load_custom_unlocked(self) -> builtins.list[PluginManifest]:
        payload = self._read_json(self.registry_path)
        rows = payload.get("plugins", []) if isinstance(payload, dict) else []
        plugins: builtins.list[PluginManifest] = []
        for row in rows:
            try:
                plugins.append(PluginManifest.model_validate(row))
            except Exception:
                continue
        return plugins

    def _load_states(self) -> dict[str, dict[str, Any]]:
        with _lock_for(self.state_path):
            return self._load_states_unlocked()

    def _load_states_unlocked(self) -> dict[str, dict[str, Any]]:
        payload = self._read_json(self.state_path)
        rows = payload.get("plugins", {}) if isinstance(payload, dict) else {}
        return rows if isinstance(rows, dict) else {}

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


plugin_registry = PluginRegistry()
