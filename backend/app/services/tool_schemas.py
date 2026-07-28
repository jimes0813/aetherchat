# backend/app/services/tool_schemas.py
# 完整工具定义 - 按用户设计


from ..models.schemas import ToolDefinition

CORE_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="upsert_memory_entity",
        description="写入或更新当前会话的跨 Agent 共享实体。",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "kind": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["key", "value"],
        },
    ),
    ToolDefinition(
        name="claim_task",
        description="从 backlog 认领任务。执行自由（用户决策）。",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"]
        }
    ),
    ToolDefinition(
        name="subdivide_task",
        description="拆分任务。",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "subtasks": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["task_id", "subtasks"]
        }
    ),
    ToolDefinition(
        name="write_artifact",
        description="写入工件并 git commit。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "commit_message": {"type": "string"},
                "task_id": {
                    "type": "string",
                    "description": "可选关联任务；执行时自动转为进行中，成功后转为评审",
                },
            },
            "required": ["path", "content"]
        }
    ),
    ToolDefinition(
        name="read_artifact",
        description="读取工件。",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    ),
    ToolDefinition(
        name="run_command",
        description="沙箱执行命令（Docker 严格限制）。",
        parameters={
            "type": "object",
            "properties": {
                "cmd": {"type": "string"},
                "cwd": {"type": "string", "default": "."},
                "timeout": {"type": "integer", "default": 30},
                "task_id": {
                    "type": "string",
                    "description": "可选关联任务；依赖完成后才能执行，成功后自动转为评审",
                },
            },
            "required": ["cmd"]
        }
    ),
    ToolDefinition(
        name="propose_merge",
        description="提出合并提案。合并需要 2 票或 Facilitator + 人类 veto。",
        parameters={
            "type": "object",
            "properties": {
                "artifact_path": {"type": "string"},
                "rationale": {"type": "string"}
            },
            "required": ["artifact_path", "rationale"]
        }
    ),
    ToolDefinition(
        name="vote_on_proposal",
        description="投票。",
        parameters={
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "approve": {"type": "boolean"}
            },
            "required": ["proposal_id", "approve"]
        }
    ),
    ToolDefinition(name="list_tasks", description="列出任务"),
    ToolDefinition(name="get_progress_summary", description="获取进度总结"),
]

# OpenAI 兼容 JSON schema 示例
def to_openai_tools(tools: list[ToolDefinition]):
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters
            }
        } for t in tools
    ]

# Anthropic input_schema 示例
def to_anthropic_tools(tools: list[ToolDefinition]):
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters
        } for t in tools
    ]
