# AetherChat 桌面 Agent 市场对标与进化建议

评估日期：2026-07-19  
评估对象：AetherChat 0.2.0 Linux 工作副本  
主要对标：Claude Cowork / Claude Desktop、OpenAI Codex 桌面端  
辅助参照：Cursor Background Agents、Devin Desktop / Cascade

## 1. 结论

AetherChat 已经不是普通的多模型聊天壳。它具备本地资料库、可审阅记忆、标准 MCP、模型工具调用、授权卡片、沙箱、版本化工件、Office 输出、运行恢复、本地模型和 Linux ARM64 打包等完整基础设施。

但它目前更准确的定义是：

> **本地优先、多模型、可审计的 AI 工作台 Beta**

它还不是与 Claude Cowork 或 Codex 同成熟度的自主桌面 Agent。最大差距不在模型数量，也不在是否内置“神经网络”。项目模式现在已经形成第一版持久闭环：

> 目标 → 可审阅计划 → 分工执行 → 动态转向 → 模型核验 → 单一交付物 → 可恢复/可追责

其中 Goal Spec、Plan DAG、执行前计划编辑/确认、独立 Worker 上下文、Verifier、Synthesizer、单一最终交付、计划级恢复和一次默认有界返工已经具备基础实现。Agent Run 2.3 又加入桌面端可信仓库选择、每个 Worker 的 detached worktree、真实 Git diff/命令退出码证据，以及原生确认后的安全 apply。尚未完成的是按 issue 精确修改 DAG、分层项目规则、后台自动化和 commit/branch/PR/冲突处理；因此仍不能把当前闭环等同于领先产品的成熟长任务能力。日常与圆桌模式仍保留较轻的直接/轮换语义。

建议不要继续横向增加一级面板、模型或桌面动作。未来 12 周应集中完成真正的 Agent Run 内核，并把产品定位收敛为：

> **面向中文、Linux、本地/私有环境的多模型 Agent 控制台：模型可替换、数据可本地、过程可审计、结果可验证。**

## 2. 评估边界与实测结果

本次完成了：

- 阅读前后端、Electron、模型适配、编排、任务、工件、知识库、记忆、MCP、插件、授权、桌面控制和发布代码；
- 在隔离的临时数据目录启动后端和最新 Web 界面；
- 检查现有桌面截图、无障碍树和 13 个一级功能入口；
- 核对 Claude、Codex 等产品在评估日期前后的官方公开能力；
- 运行现有自动化验证。

验证结果：

- 后端：77 passed，1 skipped；
- Electron 更新验证：3 passed；
- 前端生产构建：通过，主 JS 314.29 KB（gzip 95.97 KB）；
- Chromium E2E：29 passed；
- 主聊天路径的现有 axe、键盘、缩放和减少动画测试通过。

评估后已直接落实的低风险 P0 改进：

- Anthropic 适配器现在合并并保留全部系统消息；
- 模型配置中的 `persona` 已进入每个 Agent 的系统提示；
- WebSocket 运行与接收循环解耦，运行期间可即时处理全局或指定模型中断；
- 并发发送统一串行化，避免后台运行和控制消息争用连接；
- UI 不再自称“Claude + Codex 框架”或显示并不存在的 Git `main` 工作区；
- 非学习型检索标签从“语义”改为“哈希相似”；
- 工具往返新增供应商无关的 `ToolCall`/结果关联字段，并分别编码为 OpenAI `tool_calls/tool_call_id` 与 Anthropic `tool_use/tool_result`；
- 并行工具结果按调用 ID 一一回传，错误和工具截图保留在对应结果中，上下文按完整用户回合裁剪；
- Agent Run 现已持久化完整工具调用记录、脱敏参数、参数/结果哈希、供应商 tool call ID、幂等身份和规范化对话 checkpoint；
- 已完成副作用结果会在重试时直接复用；结果不确定的副作用默认阻止重放并要求人工核查；
- 活跃 Run 中的新消息会持久化为 steer，并在下一安全边界替换尚未执行的旧方向；中断可按 `run_id + step_id` 定位；
- 项目模式现在持久化 Goal Spec 与四阶段 Plan DAG，按 Planner → 并行 Worker → Verifier → Synthesizer 调度；
- Worker 结果以结构化 handoff 进入 Verifier，最终只有 Synthesizer 的单一交付进入主聊天；计划失败重试会复用已完成 Worker；
- 新项目 Run 会先停在持久化计划草稿；用户可编辑 Goal 和步骤职责/模型，基于 revision 确认或拒绝，确认前不会调用 Worker；
- Verifier 的 `needs_revision/fail` 会触发默认一次、最多两次的有界返工；重复 `fail` 会阻断未经核验的 Synthesizer 输出；
- 桌面端可通过原生目录选择器登记干净 Git 仓库，并在计划中冻结 HEAD、分支和核验命令；
- 每个代码 Worker 在 AetherChat 自管的 detached worktree 中修改，模型只接收不透明 workspace ID；未绑定仓库的 Worker 仍保持只读；
- Worker 结束后运行断网 Docker 核验，持久化真实 Git diff、文件清单、退出码和输出哈希；模型 Verifier 不能把“全部无 diff/命令失败”包装为通过；
- 用户可在运行面板审阅候选并经 Electron 原生确认应用或丢弃；应用会重查 session、Run 终态、HEAD、主树干净状态和补丁哈希，不会 commit、push、fetch 或联网；
- 已为上述协议与控制行为增加回归测试。

限制：

- 没有调用付费云模型，因此没有把真实模型完成率当成已验证能力；
- E2E 的模型和 WebSocket 流主要使用模拟数据，不能替代真实长任务评测；
- 当前 Planner 仍是确定性模板；模型 Verifier 已叠加 Git diff 和配置命令的确定性证据，但尚不能按 issue 动态改图，核验覆盖也取决于用户确认或仓库推断出的命令；
- 基础幂等无法消除外部副作用已发生但本地结果尚未提交时的崩溃窗口；跨系统 exactly-once 仍需连接器原生幂等令牌或事务 outbox；
- 当前 AetherChat 源码工作副本没有 `.git` 元数据，因此仍无法核对本项目自身的提交历史、CI 和 PR；Git Agent 流程则已用临时真实仓库验证 worktree、diff、核验和 apply。

## 3. 市场基线

截至本次评估，领先桌面 Agent 的基线已经从“能聊天、能调用工具”升级为“能完成并交付长任务”。

| 产品 | 当前公开基线 | 对 AetherChat 的直接启示 |
| --- | --- | --- |
| Claude Cowork | 先创建计划，必要时拆成子任务；在隔离环境执行代码与命令；可协调并行子 Agent；过程可见、可中途转向；可后台持续和定时运行；最终交付可预览文件 | “项目模式”必须由持久计划和验收条件驱动，而不是把多个回复放入同一聊天流 |
| Claude Desktop Computer Use | 优先连接器，其次浏览器，最后才使用屏幕点击；按应用授权；明确提示截图和直接桌面控制风险 | Linux 不应先投入坐标式电脑控制；优先补齐 MCP/原生工具和浏览器自动化 |
| Codex 桌面端 | 多 Agent 独立线程、并行任务、Git worktree 隔离、线程内 diff 评审、可复用 Skills、后台 Automations、系统级沙箱 | AetherChat 的任务、工件、运行和插件需要合并为一个“计划—执行—评审”主流程 |
| Cursor Background Agents | 异步远程 Agent、隔离环境、独立分支、可跟进或接管 | “可恢复”之外还需要真正的后台 Job、隔离 workspace 和清晰的接管语义 |
| Devin Desktop / Cascade | Rules、`AGENTS.md`、Workflows、Skills、workspace memory 和 worktrees 形成分层定制体系 | 当前“项目固定说明 + 插件 manifest”还不足以构成可靠、可版本化的工作规范 |

官方依据：

- [Claude Cowork：计划、子任务、隔离执行、并行工作流、进度与转向](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork)
- [Claude Cowork：项目、指令、上下文、记忆与定时任务](https://support.claude.com/en/articles/14116274-organize-your-tasks-with-projects-in-claude-cowork)
- [Claude Computer Use：连接器 → 浏览器 → 屏幕交互的优先级](https://support.claude.com/en/articles/14128542-let-claude-use-your-computer-in-cowork)
- [Codex 桌面端：并行 Agent、worktree、diff、Skills 与 Automations](https://openai.com/index/introducing-the-codex-app/)
- [Cursor Background Agents：异步隔离环境与独立分支](https://docs.cursor.com/background-agent)
- [Devin Desktop / Cascade：Rules、AGENTS.md、Workflows 与 Skills](https://docs.devin.ai/desktop/cascade/memories)

## 4. AetherChat 的真正优势

### 4.1 多供应商和本地模型是最清晰的护城河

Claude、Codex 等产品的体验与自家模型强绑定。AetherChat 已支持 Anthropic、OpenAI-compatible、多家国内模型和本地 GGUF/llama.cpp，并能把不同模型放在同一工作区中。

这项优势不应表现为“首屏同时显示六份回答”，而应升级为：

- 自动按能力、延迟、成本、隐私和可用状态选模型；
- 关键步骤允许双模型独立作答和交叉复核；
- 用户默认只看一份综合结论，可展开原始模型证据；
- 同一任务可以设置“只用本地模型”“敏感资料不出机”“云模型仅看脱敏摘要”等边界。

### 4.2 本地优先与 Linux 支持具有真实差异化

AetherChat 的资料、记忆、工件和密钥主要保存在本机；项目检索不依赖外部 embedding API；当前已有 Linux ARM64 AppImage/Deb。对于中文用户、Linux 用户、内网或数据敏感团队，这是比“再多一个云 Agent”更明确的市场位置。

### 4.3 授权与副作用控制设计扎实

独立授权卡片、允许一次/本会话允许/拒绝、聊天文本不能伪造授权、密钥环、MCP 工具逐项暴露、沙箱默认断网和副作用审计，明显优于大多数早期 Agent 原型。

这部分应保留为产品核心，而不是隐藏在“设置”里。最终交付页应该能一眼回答：

- 谁请求了动作；
- 实际执行了什么；
- 用户批准了什么范围；
- 产生了哪些文件、命令结果和外部副作用；
- 哪些验证已通过。

### 4.4 工件和 Office 输出比普通代码 Agent 更宽

安全预览、版本历史、diff、恢复、DOCX/XLSX/PPTX/PDF/Markdown 输出，使 AetherChat 有机会覆盖“研究—报告—表格—演示文稿”的知识工作流，而不仅是写代码。

### 4.5 工程底座超过常见 0.2 原型

现有测试覆盖拒绝路径、附件安全、MCP、OAuth、恢复、更新签名、SBOM、无障碍和压力场景。这意味着后续可以集中重构 Agent 内核，而不必推倒重来。

## 5. 主要缺点与代码级发现

### P0：直接影响 Agent 正确性和可信度

#### 5.1 Claude 适配器会丢失多条系统上下文（已修复）

评估时发现 `AnthropicProvider` 遍历消息时不断覆盖 `system`，最终只发送最后一条系统消息：

- [backend/app/providers/base.py](../backend/app/providers/base.py#L355)

而编排器会分别插入记忆、附件引用、语言、Computer Use 和 ComfyUI 等多条系统消息。结果可能是 Claude 丢失附件约束、语言要求、工具规则或安全说明。

本次已按稳定顺序合并全部系统消息，并增加“多条系统消息 + 附件 + 工具”的 Anthropic 回归测试。后续仍应为每次真实请求保存脱敏后的 context manifest 和哈希，便于审计。

#### 5.2 运行期间的中断与基础转向（已修复）

评估时 WebSocket 收到 `post_message` 后会同步等待整个 `execute_agent_run` 完成，期间不再读取同一连接上的下一条消息：

- [backend/app/routers/ws.py](../backend/app/routers/ws.py#L51)

因此 UI 虽然有“中断全部模型”，但中断消息可能直到当前运行结束后才被处理，用户也无法像 Claude Cowork 那样在长任务中途补充方向。现在 Run 已移到后台 task，receiver 会持续处理控制消息；活跃 Run 中的第二条业务消息会持久化为 steer event，而不是新建 Run 或返回 `run_in_progress`。编排器在下一次供应商响应结束、工具尚未执行的安全边界丢弃陈旧调用，把最新指令注入指定模型并从新 checkpoint 继续。

当前还不是任意 token 时刻的即时转向：远程供应商请求尚未返回、正在等待授权时，steer 只能排队；steer 暂不接受新附件。后续应：

- 为支持取消的供应商接入 request cancel/resume，使长响应能更快进入安全边界；
- 支持 steer 附件并明确其上下文版本；
- 为 `uncertain` 副作用提供人工确认、补偿或标记已完成的界面；
- 发送、接收和事件持久化进一步使用独立队列；
- 验收指标：本地中断确认 P95 < 1 秒。

#### 5.3 `persona` 已配置但没有进入模型上下文（已修复基础注入）

评估时模型配置保存了 Architect、Reviewer、Coder 等角色，但 `build_messages_for_provider` 只注入通用的 “helpful AI collaborator”：

- [backend/app/services/model_registry.py](../backend/app/services/model_registry.py#L50)
- [backend/app/services/context_builder.py](../backend/app/services/context_builder.py#L13)

本次已注入稳定 Agent 标识、职责和不可虚构验证结果的约束。下一步应把角色升级为结构化 Agent Profile：

- 目标与职责；
- 可用工具；
- 允许访问的资料；
- 输出 schema；
- 验收标准；
- 最大成本/时长；
- 失败和升级策略。

#### 5.4 工具往返不是供应商原生协议（基础协议已修复）

评估时，工具执行后会被编排器重新包装为普通文本/图片消息；OpenAI-compatible 和 Anthropic 适配器也会把 `tool` 角色转成普通 user 内容，没有完整保留 `tool_call_id`、Anthropic `tool_use/tool_result` 内容块等关联。

短任务可能可用，但长工具链会更容易出现：

- 模型忘记自己调用过哪个工具；
- 并行工具结果错配；
- 重试时重复副作用；
- 不同供应商行为不一致。

本阶段已经完成基础修复：

- `ChatMessage` 使用供应商无关的 `ToolCall`、`tool_call_id` 和 `tool_error` 字段；
- OpenAI-compatible 请求保留 assistant `tool_calls`，并以逐条 `tool` 消息和 `tool_call_id` 回传结果；
- Anthropic 请求生成 assistant `tool_use` 内容块，并把并行结果合并为紧邻的 user `tool_result` 内容块；
- 工具截图在 Anthropic 中进入对应 `tool_result`，在 OpenAI 中置于全部原生工具结果之后的视觉观察消息，避免打断并行结果序列；
- 缺失或重复的调用 ID 会在执行前修复，并且每个结果保持一一关联；
- 上下文超限时按完整用户回合裁剪，不再留下孤立的原生工具协议片段。

本阶段又补齐了跨进程基础：

- 每次工具调用持久化 Run/Step、供应商、模型、原生 tool call ID、脱敏参数、参数/结果哈希、状态、尝试次数和工具运行 ID；
- 幂等身份由模型、工具轮次、调用顺序、工具名和规范化参数共同约束；
- 已确认完成的副作用调用重放既有结果；重启或失败后结果不确定的副作用标记为 `uncertain`，默认不再执行；
- 每轮工具结果后保存供应商无关的规范化上下文 checkpoint，重试从下一工具轮次继续。

仍需补齐的是供应商通用 response/item ID、连接器原生幂等令牌或事务 outbox，以及 OpenAI/Anthropic 真实云模型的固定场景金丝雀测试。若外部动作已发生而进程在结果落库前崩溃，当前设计会安全阻断重放，但不能自动判断外部状态，也不能宣称 exactly-once。本次没有使用付费密钥，不能把离线协议测试等同于真实模型完成率。

#### 5.5 “圆桌”和 Facilitator 名称大于实际能力

当前圆桌模式只是每轮轮换选择一个模型；Facilitator 只检查最近文本是否重复，并生成“处理了多少条消息”的固定摘要：

- [backend/app/services/orchestrator.py](../backend/app/services/orchestrator.py#L26)
- [backend/app/services/orchestrator.py](../backend/app/services/orchestrator.py#L123)

建议在真正实现前修改产品文案，避免用户产生错误预期。

真正的圆桌应至少包含：

1. 各模型在互不可见条件下独立提出方案；
2. 统一 schema 对齐关键主张、证据、风险和置信度；
3. 第二轮交叉质询或专门 Critic；
4. Judge/Synthesizer 按明确评分标准产出一份结论；
5. 保留少数意见和无法裁决的分歧。

### P1：阻碍达到 Claude/Codex 的工作闭环

#### 5.6 项目模式已具备可审核 DAG 与有界返工，但还不是成熟自治调度

项目协作现在显式启用 `execution_strategy=planned`，并持久化 Goal Spec、Plan Step、依赖、角色、输入/输出契约、成功条件、步骤输出哈希和 Review。确定性 Planner 建立四阶段 DAG：

1. 多个 Worker 使用互相独立的上下文并行产出候选；
2. Verifier 读取持久化候选，返回结构化 verdict、evidence 和 issues；
3. Synthesizer 读取目标、候选与核验结论；
4. 主聊天只接收一份最终交付，中间结果留在运行记录中。

调度器会复用已完成 Worker、允许后续步骤在部分依赖失败时降级完成，并对预算、全局中断和 Plan Step 中断作出确定性处理。未绑定仓库的 Worker 与 Verifier 仍默认只读；绑定可信仓库的代码 Worker 则在各自的 AetherChat 自管 worktree 中获得受限文件/命令工具，主工作树在人工 apply 前不受污染。

Agent Run 2.2 又补上了：

- 项目模式在 Planner 后进入 `waiting_plan_confirmation`，确认前零 Worker 调用；
- Goal、成功条件、约束、交付物、步骤职责和模型可在执行前编辑；
- 每次 Planner/人工编辑/Verifier 重规划都有不可覆盖的 revision 快照，并用期望版本阻止陈旧写入；
- `needs_revision/fail` 可触发 0–2 次有界返工，Worker 会收到上一版候选与 Verifier issues；
- 返工耗尽后仍为 `fail` 时阻止最终综合；计划拒绝和等待确认重启都有确定状态。

Agent Run 2.3 又补上了：

- Electron 受信主进程选择仓库，Renderer 和模型都拿不到仓库绝对路径；
- 计划冻结干净仓库的 HEAD，每个 Worker/attempt 使用独立 detached worktree；
- 受限文件工具和断网 Docker 命令仅能作用于服务端绑定的 worktree；
- Worker 结束后采集 binary/full-index patch、变更文件、核验退出码和输出哈希；
- 模型 Verifier 叠加不可自行伪造的确定性证据；
- 用户原生确认后才可安全 apply，且不会自动 commit、push 或联网。

仍然缺少：

- 根据 issue 只重跑指定 Worker、动态增删步骤和重新排序 DAG；
- 基于能力、历史成功率、成本与延迟的 scheduler 路由；
- 读取并分层执行 `AGENTS.md`、仓库 Rules 和 Skills；
- 针对不同技术栈的更完整核验模板、依赖缓存，以及引用/格式等非代码确定性 verifier；
- patch 冲突可视化和显式 commit/branch/PR 工作流；
- 人工修复 `uncertain` 调用和跨系统 exactly-once 语义。

#### 5.7 已能从 Plan Step checkpoint 续跑，但还不是跨系统精确恢复

Agent Run 不再只保存运行、模型 step 和关键事件。现在它还会保存 Plan DAG、Review、工具调用链、供应商 tool call ID、幂等状态和每个 Plan Step 的规范化对话 checkpoint；重试会保留已完成 Worker，恢复未完成步骤的最近 checkpoint 与下一工具轮次，已完成副作用复用既有结果，结果不确定的副作用被阻断。

这已经具备“从计划级安全边界继续”的基础；等待确认的草稿也会跨重启原样保留，Verifier 返工则形成新的计划 revision。仍缺少独立 Attempt 表、多进程租约、供应商原生 response/item 续接、按 issue 精确补偿，以及面向人工处理 `uncertain` 调用的恢复界面。因此还不能称为跨进程、跨系统的完整精确续跑。

#### 5.8 版本化工件与 Git Agent 工作区已经分层

`ArtifactService` 仍使用会话目录和自有历史文件管理文档/Office 工件，它本身不是 Git：

- [backend/app/services/artifact_service.py](../backend/app/services/artifact_service.py#L134)

代码任务则通过独立的 `GitWorkspaceService` 接入真实用户仓库：

- [backend/app/services/git_workspace_service.py](../backend/app/services/git_workspace_service.py)

当前已经支持可信选择已有 repo、每个 Worker 独立 worktree、真实 diff/test 证据、脏树/HEAD 竞态拒绝和人工确认后的 apply。它刻意不创建 commit、branch 或 PR，也不支持 Git LFS、子模块、超大补丁和冲突合并；`AGENTS.md`/Rules 尚未进入分层指令系统。详细信任边界见 [Git Agent 工作区](GIT_AGENT_WORKSPACES.md)。

#### 5.9 预算目前更像 UI 演示值

每个新 WebSocket 编排器把余额重设为 `$10.00`，默认模型成本配置又是 0。成本和预算没有形成持久、可信的账本。

应区分：

- 用户设置的硬预算；
- 供应商返回的 token 使用；
- 可维护的价格表版本；
- 本地模型的耗时/电量估算；
- 预测成本与实际成本；
- 每个 Run/Step/模型的明细。

无法确定价格时显示“未知”，不要显示虚假的 `$0.00`。

#### 5.10 记忆可审阅，但智能程度有限

当前偏好、决定和待办通过关键词正则提取；摘要是最近消息片段拼接：

- [backend/app/services/memory_service.py](../backend/app/services/memory_service.py#L9)

优点是可解释、无网络、不会让助手输出自动污染用户偏好。缺点是：

- 否定、修正和过期信息不容易处理；
- 没有来源、置信度、有效期和冲突关系；
- 每次注入大量固定记忆，而非按任务相关性检索；
- 规则、项目事实、短期工作记忆混在一起。

应拆为：

- Rules/Instructions：用户明确设置，可版本化；
- Facts/Decisions：带来源、时间、置信度和替代关系；
- Working Memory：只服务当前 Run；
- Episodic Summary：按相关性召回，不默认全部注入。

#### 5.11 “语义检索”目前不是学习型语义向量

知识库的 256 维向量由词和字符 n-gram 做哈希后得到，再与 FTS、覆盖率和短语命中混合：

- [backend/app/services/knowledge_service.py](../backend/app/services/knowledge_service.py#L350)
- [backend/app/services/knowledge_service.py](../backend/app/services/knowledge_service.py#L420)

它快速、离线、零模型依赖，但本质是 lexical feature hashing，不理解真正的同义、跨语言和语义关系。UI 中“语义命中”容易高估能力。

在加入学习型 embedding 前，建议把标签改为“相似命中”或“哈希相似”。

#### 5.12 缺少后台自动化和跨设备接管

Claude Cowork、Codex 和 Cursor 已把“关掉当前窗口后继续”“定时运行”“回来审阅结果”作为基线。AetherChat 的运行依赖当前后端/WebSocket，会话断开后转为 recoverable，并不会继续完成任务。

应先实现本机 durable job daemon，再考虑可选云端：

- SQLite job queue + lease/heartbeat；
- Run 与 UI 连接解耦；
- 定时、手动触发和文件变更触发；
- 完成/需授权/失败通知；
- 回到应用后进入 review inbox。

#### 5.13 UI 能力丰富，但主线不清楚

当前左侧有 13 个一级入口：对话、记忆、资料、任务、文件、变更、工具、插件、连接、电脑、模型、运行、设置。首屏强调模式、模型数量、预算和连接状态，却没有优先展示计划、当前步骤、验证证据和最终交付物。

建议收敛为 5 个一级区域：

1. **工作**：会话、计划、运行和任务；
2. **上下文**：资料、记忆、项目规则；
3. **交付物**：文件、版本、diff、导出；
4. **能力**：模型、MCP、Skills、插件和工具；
5. **安全与设置**：权限、预算、审计和应用配置。

主工作区使用三个视图：

- Plan：目标、步骤、依赖、负责人、验收条件；
- Activity：模型、工具、授权、进度和可转向事件；
- Deliverable：最终回答/文件、引用、diff、测试和未解决风险。

原始的六个模型输出默认折叠到“过程证据”，不应成为最终结果本身。

### P2：维护性和发布成熟度

#### 5.14 前端单文件过大

`frontend/src/App.tsx` 约 3,147 行，包含数据访问、鉴权、会话、记忆、知识库、工件、模型、MCP、插件、桌面和大量 UI。继续增加 Agent 功能会显著提高回归风险。

建议按 feature 拆分：

- `features/runs`
- `features/plans`
- `features/context`
- `features/artifacts`
- `features/capabilities`
- `features/security`

API client、server state、表单状态和持久偏好应分层，不再集中在根组件。

#### 5.15 存储和运行状态分散

SQLite、JSON 文件、内存全局对象和会话目录同时承担状态。任务还保留 `fake_tasks` 全局列表和 JSON/SQLite 双写。桌面 Beta 可以工作，但不适合后台并发、多进程和故障恢复。

建议：

- 元数据统一进入带 migration 的 SQLite；
- 文件内容保留在受控 workspace；
- 每次副作用都有 idempotency key；
- 所有状态迁移使用 compare-and-set；
- JSON 只作为导入/导出格式。

#### 5.16 发布状态尚不能称为 99%

当前测试与 ARM64 包质量不错，但工作副本无 Git 历史；Linux x64/Windows matrix、正式签名、屏幕阅读器人工验收仍未完成；真实长任务和真实云模型 Agent 成功率也没有基准。

“99%”只能描述旧需求清单完成度，不能描述市场产品成熟度。建议改为分维度成熟度和可重复指标。

## 6. 推荐的产品定位

不要把 AetherChat 定义为“Claude + Codex 框架”。这会让用户用两家最强单模型/单生态体验来衡量一个仍在形成内核的产品，也弱化 AetherChat 自己的优势。

推荐定位：

> **AetherChat：本地优先的多模型 Agent 控制台。面向中文、Linux 和私有数据场景，把不同云端/本地模型组织成可审计、可验证、可恢复的工作流。**

优先打磨三个 Golden Path：

### A. 研究与报告

导入本地资料和连接器 → 多模型独立研究 → 事实/引用校验 → Critic 查漏 → 生成带引用的 DOCX/PPTX/XLSX → 用户审阅导出。

这是现有资料库、MCP、多模型和 Office 工件能力的最佳交集。

### B. 仓库改动

打开干净 repo → Planner 生成计划 → worker worktree 实现 → 运行已确认的核验命令 → reviewer 检查证据 → 用户原生确认应用。

这条路径的安全 MVP 已具备：主工作树在批准前不受污染，apply 后留下未提交改动供用户继续审阅。要达到 Codex/Cursor 的成熟度，仍需项目规则、依赖缓存、冲突处理、commit/branch/PR 和后台执行。

### C. 本地私有流程

敏感文件只由本地模型和本地工具处理 → 云模型仅接收允许的摘要 → 全程显示数据边界 → 输出本地工件。

这是 AetherChat 最有机会领先的差异化流程。

桌面坐标控制放在这三条路径之后。

## 7. 真正的 Agent 内核

推荐的主流程：

```text
用户目标
   ↓
Goal Spec（约束、完成标准、预算、数据边界）
   ↓
Planner → 持久 Plan DAG
   ↓
Policy / Budget / Capability Router
   ↓
隔离 Worker(s) → 工具、文件、连接器、checkpoint
   ↓
Verifier / Reviewer → 测试、diff、引用、格式、风险
   ↓
Synthesizer → 一份最终交付物 + 证据 + 未解决问题
   ↓
用户接受、修改、导出或继续
```

核心数据结构至少应包含：

- `Run`：目标、约束、成功条件、预算、状态；
- `PlanStep`：依赖、角色、输入、输出 schema、风险、验收命令；
- `Attempt`：模型、上下文哈希、工具事件、工件、成本、错误；
- `Review`：目标 attempt、结论、证据、失败原因；
- `Checkpoint`：可安全恢复的位置和幂等状态；
- `Deliverable`：最终文件/回答、引用、验证结果和来源链。

三种模式应有完全不同、可解释的执行语义：

| 模式 | 推荐语义 |
| --- | --- |
| 快速 | 单 Agent 或自动路由；低延迟；必要时才调用工具 |
| 评议 | 多模型盲评 + Critic/Judge；输出一份综合结论和少数意见 |
| 项目 | Plan DAG + 隔离 worker + verifier + checkpoint + 最终交付 |

如果短期无法实现，应先把“休闲/圆桌/项目”改成更准确的“单模型/轮换/并行”。

## 8. 12 周进化路线

### 第 1–2 周：正确性与真实语义

- [x] 修复 Anthropic 多系统消息覆盖；
- [x] 重构 WebSocket receiver，使中断可实时处理；
- [x] 将基础 persona/role 注入每个 Agent；
- [x] 支持运行中的基础 steer event；
- [ ] 将输出 schema、成功条件和失败升级策略纳入 Agent Profile；
- [x] 使用供应商原生 tool-use/tool-result 往返；
- 让预算、模型健康状态和成本持久且可信；
- [x] 移除 UI 中并不存在的 Git `main` 和“Claude/Codex 框架”，把“语义”改为“哈希相似”；
- [ ] 修正“圆桌”“99%”等剩余超前文案；
- 恢复项目 Git 仓库和最小 CI。

验收：

- 中断 P95 < 1 秒；
- 多系统消息在 Anthropic/OpenAI 适配器中 100% 保留；
- 真实模型工具调用 50 个固定场景成功率 ≥ 95%；
- UI 不再把未知成本或未验证模型显示成可用/在线。

### 第 3–6 周：Agent Run 2.0

- [x] 引入 Goal Spec、Plan DAG、Plan Step 和 Review；独立 Attempt 表仍待完成；
- [x] 实现确定性 Planner、依赖 Scheduler、并行 Worker、模型 Verifier 和 Synthesizer 基础；
- [x] Worker 独立上下文、持久化结构 handoff 和单一最终交付；
- [x] 完成本地持久化的副作用幂等保护基础；跨系统 exactly-once 与人工补偿仍待完成；
- [x] 支持从每个 Plan Step 最近的安全 checkpoint 继续，并复用已完成 Worker；
- [x] 增加计划确认/编辑、版本快照和默认一次的有界自动修订循环；
- [x] 为代码 Worker 增加 Git diff/命令退出码确定性验证；
- [ ] 增加按 issue 精确补偿、动态 DAG 增删和非代码确定性验证；
- [ ] 实现真正的“评议”模式。

验收：

- 30 个 Golden Task 中，端到端完成率 ≥ 80%；
- 重启恢复成功率 ≥ 95%；
- 已完成步骤不重复执行；
- 每个“完成”结果都有可检查证据或明确说明无法验证。

### 第 7–10 周：工作区与 UX

- [x] 支持桌面端选择已有干净 Git repo；
- [x] Git worktree、diff、测试证据和安全 apply；
- 分层 Rules / `AGENTS.md` / Skills；
- 将 13 个一级入口收敛为 5 个；
- Plan / Activity / Deliverable 成为主视图；
- 把原始多模型输出折叠为证据。

验收：

- 两个 Agent 并行修改同一 repo 时不污染主工作树；
- 用户能在 10 秒内回答“现在在做什么、为什么、还差什么、改了什么”；
- 从目标到可审阅 diff 的中位交互次数明显下降。

### 第 11–12 周：检索智能与自动化

- 建立检索 gold set；
- 可选安装本地 embedding + reranker；
- 增加 OCR/layout 插件（仅在目标用户需要扫描件时）；
- 本机 durable job、定时运行和 review inbox；
- 做消融实验，确认每个学习模型带来真实收益。

验收：

- 检索 Recall@5、MRR/nDCG、引用正确率显著优于哈希基线；
- 后台任务不依赖 UI/WebSocket 存活；
- 定时任务的授权边界和失败通知可审计。

## 9. 是否需要加入神经网络

### 直接答案

**不建议为 AetherChat 自研或训练一个新的通用神经网络。**

AetherChat 已经在使用神经网络：

- 云端 LLM；
- 本地 GGUF/llama.cpp 模型；
- ComfyUI 图像/视频模型；
- 支持视觉输入的模型。

应用自身的核心价值是编排、权限、状态、工具、上下文和交付。计划状态机、授权、幂等、预算和审计必须尽量确定性，不能交给一个新增神经网络“猜”。

### 值得加入的学习模型

按收益排序：

1. **本地多语言 embedding 模型**  
   用于替换/补充当前哈希相似度，改善中文同义、跨语言和概念检索。这是最有价值的神经网络增量。

2. **本地 reranker**  
   只对 FTS/embedding 召回的几十个片段重排，提升最终引用质量。不要对整个资料库全量运行。

3. **OCR/layout 模型**  
   只有当扫描 PDF、截图表格和复杂版面是核心用户需求时再加。

4. **小型任务路由器**  
   在积累足够的真实任务、模型成功率、成本和延迟数据后，用于选择模型。初期用规则和轻量 bandit 即可，不需要神经网络。

5. **视觉模型辅助 Linux 桌面理解**  
   优先级低于 MCP 和浏览器自动化。视觉模型不能解决 Wayland 权限、动作可靠性和安全边界问题。

### 不应做的事

- 不训练自有基础模型；
- 不让神经分类器成为唯一安全闸门；
- 不把所有记忆都做成不可解释的向量；
- 不把大模型权重直接塞进默认安装包；
- 不在没有离线评测集时宣称 embedding 一定更好；
- 不用神经网络掩盖状态机、协议和产品流程问题。

### 推荐接入方式

把学习模型做成可选的 `Local Intelligence Pack`：

- 首次需要时按需下载；
- 明确模型许可证、体积、RAM/VRAM 和硬件支持；
- SHA-256 校验和可卸载；
- CPU/ARM64 可运行；
- embedding、reranker、OCR 分开安装；
- 无模型时自动回退到现有 FTS + 哈希基线；
- UI 显示实际使用的是“全文 / 哈希相似 / embedding / reranker”中的哪一层。

先建立 100–300 条真实检索问答和引用标注，再决定是否默认启用。

## 10. 建议的产品指标

不要再用“需求项完成百分比”作为唯一进度。至少持续记录：

- Golden Task 端到端完成率；
- 首次交付可接受率；
- 平均人工干预次数；
- 工具调用成功率和重复副作用率；
- 中断响应时间；
- 重启恢复成功率；
- 计划步骤预测时间与实际时间；
- 每个可接受交付物的成本和耗时；
- 引用正确率、Recall@5、MRR/nDCG；
- 用户打开原始模型输出的比例；
- 授权拒绝率和授权疲劳次数；
- 自动验证覆盖率。

只有这些指标改善，才能说明产品真的“进化”，而不是功能清单继续增长。

## 11. 最终优先级

如果只能做三件事：

1. **修复 P0 协议与中断问题，确保 Claude 上下文、工具往返和用户控制真实可靠；**
2. **在已有隔离 worktree 和代码核验之上补齐按 issue 补偿、分层项目规则、冲突/提交工作流与后台执行；**
3. **围绕研究报告这一条 Golden Path 重做主界面，并用真实任务集验收。**

神经网络排在这三件事之后。届时优先加入本地 embedding + reranker，而不是训练新的“Agent 大脑”。
