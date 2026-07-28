# Agent 协作与可审计上下文协议

本文描述 AetherChat 在同一会话内交换生成物、关键实体、任务状态和工具事件的稳定契约。

## 1. 生成物元数据

ComfyUI 的 `generate_image`、`generate_video` 和 `run_workflow` 成功后会自动创建：

```text
generated/comfyui/<prompt_id>.metadata.json
```

文件遵循 [`schemas/agent-artifact-v1.schema.json`](schemas/agent-artifact-v1.schema.json)，核心字段包括：

- `parameters`：直接生成调用的完整 prompt、negative prompt、模型、实际 seed、尺寸和采样参数；
- `workflow`：节点 ID/名称、依赖边、可覆盖输入及类型；
- `provenance`：工作流实际引用的 prompt 文本、模型、seed 和尺寸；
- `outputs`：真实文件名、媒体类型与 ComfyUI URL；
- `producer`：发起生成的 Agent、插件和动作；
- `submitted_at` / `completed_at`：生成时间边界。

工具结果会同时返回 `metadata_artifact.path`。后续 Agent 应直接读取该 JSON，而不是再次询问前一个 Agent。

## 2. Agent 共享实体

会话记忆新增 `entities` 数组，每项使用稳定 `key`：

```json
{
  "key": "character:lin-xia",
  "kind": "character",
  "value": "林夏，小说主角",
  "source": "model:qwen",
  "metadata": {},
  "updated_at": "2026-07-28T00:00:00Z"
}
```

所有会话 Agent 在下一次模型调用前都会获得这些实体。ComfyUI 生成物会自动登记为
`asset:comfyui:<prompt_id>`，并指向元数据工件。实体按会话隔离，用户可在“记忆”面板审阅或删除。
Agent 可调用 `upsert_memory_entity` 新增或更新角色、URL、项目事实等实体；同一 `key` 会覆盖旧值。

## 3. ComfyUI 工作流发现与调试

- `status` 的每个工作流条目包含 `node_map`、`dependency_text` 和 `overridable_inputs`。
- `workflow_schema(name)` 返回单个工作流的完整可覆盖输入清单。
- `run_workflow` 会拒绝不存在的节点/输入、错误 JSON 类型、越界数值和非法枚举，并返回可用字段。
- ComfyUI 校验或执行错误会附带 `node_id -> node_name`，避免只报告裸节点编号。

建议调用顺序：

```text
status -> workflow_schema -> run_workflow
```

## 4. Computer Use 稳定定位

`get_state` 的每个 UI 元素同时返回：

- `element_id`：优先使用的稳定 UIA 标识；
- `selector`：automation ID、控件类型与文本；
- `index`：仅用于兼容旧调用的快照临时索引。

`click`、`type_text` 和 `set_value` 支持 `element_id`，也支持
`element_text + control_type` 模糊定位。默认 `auto_retry=true`：快照失效或目标在旧快照中消失时，
服务会重新获取无障碍树并重试一次。动作完成后仍应再次调用 `get_state` 验证界面结果。

## 5. 任务依赖与工具联动

任务的 `depends_on` 是任务 ID 数组。API 负责：

- 拒绝跨会话依赖、自依赖和循环依赖；
- 返回实时计算的 `blocked_by` 和 `ready`；
- 当前置任务未进入 `merged` 或 `done` 时，拒绝认领、推进和关联工具执行。

`write_artifact`、`run_command`、Worker 工作区的写入/删除/命令工具，以及支持 `task_id`
的插件动作会自动：

```text
开始执行 -> in_progress
成功完成 -> review
```

失败时任务保留在 `in_progress`，等待重试或人工处理。工具运行记录和任务事件均持久化。

## 6. 检索、推理透明度与隐私

当消息没有显式附件时，AetherChat 会自动检索当前会话的本地 FTS5/哈希向量知识库，
只把达到相关性阈值的片段注入上下文，并要求模型以 `[来源: 文件名 · 位置]` 标注证据。
资料不会发送到外部 embedding 服务。

“运行”面板的协作审计日志展示 Agent 状态、消息交接预览、计划步骤、工具参数和截断后的结果/错误。
它刻意不保存或展示模型的私密思维链；需要调试的逻辑应写成可核验的结论、证据、
决策和失败原因。

## 7. 实时状态

顶部状态区显示当前活跃 Agent、正在生成/等待授权/调用的工具和已运行时间。
若当前会话存在已完成运行，预计剩余时间使用历史完成时长的中位数估算；没有样本时明确显示“估算中”，
不会伪造精确 ETA。
