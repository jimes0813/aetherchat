# AetherChat - 本地优先的多模型 Agent 工作台

Linux 桌面版的构建与安装说明见 [`frontend/LINUX_PACKAGING.md`](frontend/LINUX_PACKAGING.md)，本次迁移基线和验证结果见 [`docs/LINUX_PORT_REPORT.md`](docs/LINUX_PORT_REPORT.md)。

完整实现启动项目，基于之前的设计文档。

当前桌面版支持把 PDF、DOCX、XLSX、PPTX、文本和常见代码文件拖入聊天输入区。资料按会话保存在本机，可在“资料”面板全文检索；随消息发送后，模型上下文会携带页码、工作表、段落或幻灯片位置，便于生成可核对的来源引用。

模型请求运行命令、写入文件、访问电脑或执行其他副作用操作时，会在对话流中显示独立授权卡片。用户可选择允许一次、本会话允许或拒绝；普通聊天文本中的“允许”不会被视为授权。PNG、JPEG 和 WebP 图片可作为多模态附件发送，上传后会先在本机安全重编码。

项目资料检索采用完全离线的 FTS5 + 本地哈希向量 + 重排，不会把资料发送给外部 embedding 服务。“资料”面板会显示检索得分和处理进度；PDF 与 Office 只预览抽取文本，图片只预览安全重编码后的副本。

每个会话还拥有独立的本地持久记忆：滚动摘要、用户明确表达的偏好和决定、未完成事项，以及可手动维护的项目固定说明。模型调用前会使用这些内容和当前任务板补全上下文；用户可以在“记忆”面板审阅、修改或清空，助手自己的说法不会自动变成用户偏好。

Agent 之间还可通过结构化共享实体和标准生成物元数据交换上下文。ComfyUI 成功生成后会自动记录 prompt、模型、seed、尺寸、工作流节点/依赖和真实输出，并写入 `generated/comfyui/*.metadata.json`；任务支持依赖、阻塞检测及关联工具的自动状态同步。协议与调试行为见 [`docs/AGENT_COLLABORATION_PROTOCOL.md`](docs/AGENT_COLLABORATION_PROTOCOL.md)。

模型生成或写入的文件会和对应回复一起持久化，在聊天卡片中可直接打开，或在系统文件管理器中显示所在目录。全部消息仍保存在本机 SQLite 中，“历史聊天”支持跨会话全文检索，最近会话可连同其任务、资料与生成物一起删除。桌面输入区和聊天内容提供原生右键复制、剪切、粘贴与全选菜单。

模型运行时，顶部状态条会显示真实阶段、上下文消息数、估算 token 数、已暴露工具数和首 token 延迟，并配有遵守“减少动态效果”设置的流式动画。本地模型默认只装载当前请求需要的工具，并把有效上下文和单次输出限制在可配置的较小窗口，避免 CPU 推理被无关工具 schema 与过长历史拖慢。ComfyUI 工具在本机服务未运行时可受控自动启动；AetherChat 只跟踪并在退出时终止由自己启动的 ComfyUI 进程，不会关闭用户预先运行的实例。

每次多模型执行会持久记录为 Agent Run。项目协作模式进一步建立 Goal Spec 和 Planner → 并行 Worker → Verifier → Synthesizer 的持久 Plan DAG：Planner 先给出可编辑草稿，用户确认前不会调用 Worker；每次人工修改和 Verifier 返工都有版本快照。中间候选与核验结论留在运行记录，主聊天只接收一份最终交付；核验失败会进行有限次数返工，持续失败则阻止未经验证的综合。应用异常退出后，等待确认的草稿会保留，执行中的 Run 可从未完成 Plan Step 继续。

桌面版还可为会话选择一个干净的 Git 仓库。代码 Worker 从计划确认时冻结的提交创建独立 detached worktree，只能通过限定工具访问该 worktree；结束后由断网 Docker 沙箱运行最多四条已确认命令，并持久化真实 diff、退出码和输出哈希。候选只有经过 Electron 原生确认，且再次通过仓库 HEAD、干净状态和补丁哈希校验后，才会以 `git apply` 写回主工作树；不会自动 commit、push、fetch 或访问网络。浏览器模式和未绑定仓库的 Worker 仍保持只读。安全边界、状态语义和已知限制见 [`docs/GIT_AGENT_WORKSPACES.md`](docs/GIT_AGENT_WORKSPACES.md)。

“连接”面板支持标准 MCP stdio 和 Streamable HTTP 服务器。用户可以检查服务器公开的工具，并逐项决定哪些工具允许进入模型上下文；模型实际调用时仍会弹出独立授权卡片。Bearer/OAuth 凭据、刷新令牌和动态客户端 secret 只写入系统密钥环，OAuth 浏览器授权使用本机回调与 PKCE/state 校验。

## 项目结构

```
aetherchat/
├── backend/              # FastAPI 后端
│   ├── app/
│   │   ├── main.py
│   │   ├── models/       # Pydantic schemas
│   │   ├── providers/    # LLM 抽象层
│   │   ├── services/     # Task, Artifact, Orchestrator
│   │   ├── sandbox/      # Docker 执行器
│   │   └── routers/      # API 路由 + WS
│   ├── requirements.txt
│   └── alembic/
├── frontend/             # React + TS + Vite
│   ├── src/
│   │   ├── components/   # UI 组件 (Feed, TaskBoard, Composer 等)
│   │   ├── stores/       # Zustand 状态管理
│   │   ├── types/
│   │   ├── ws/           # WebSocket 客户端
│   │   └── App.tsx
│   └── package.json
├── docker-compose.yml
├── docs/
│   └── facilitator_prompts.md
└── README.md
```

## 快速开始（本地开发）

```bash
# 1. 启动基础设施
docker-compose up -d

# 2. 后端
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
AETHERCHAT_ENABLE_DEMO_AUTH=1 uvicorn app.main:app --reload --port 8000

# 3. 前端 (新终端)
cd frontend
npm install
npm run dev   # http://localhost:5173
```

访问 http://localhost:5173 即可使用基础界面。

## 已包含内容（根据“全部”需求生成）

- 完整目录结构
- docker-compose.yml (PostgreSQL + Redis + MinIO + 后端/前端)
- 沙箱 Dockerfile 示例
- 数据库 Schema + Pydantic 模型
- LLM Provider 抽象 + 工具 Schema
- WebSocket 完整协议实现骨架
- 核心服务（版本化工件、Git Agent 工作区、Sandbox）
- UI 原型实现（React + shadcn 风格组件）
- Zustand 状态管理
- Facilitator 提示词模板
- 详细 IMPLEMENTATION_GUIDE（PR 计划细化）

## 后续开发顺序建议

参考设计文档中的 13 个 PR（已按顺序推进）：
- PR0~PR5：基础设施、模型、Auth、Provider、WS、Context/Budget（已完成）
- PR6：Modes + Targeting + Interrupts（已完成）
- PR7：Task + Claim（已完成）
- PR8：版本化工件（已完成）
- PR9：Sandbox + Merge（已完成）
- PR10：Facilitator（已完成）
- PR11：Export + i18n + Vault（已添加 export /export/session , 基本 i18n toggle, vault stub）
- PR12：Rate limit + Polish（已添加 rate limit middleware, errors, UI buttons）
- PR13：E2E + Load + Hardening（已添加 tests/test_basic.py stub, 建议 k6）

当前状态：所有 13 个 PR 已按顺序基本实现（代码骨架 + 核心逻辑 + 前端交互）。
- 支持真实/模拟多模型并发流
- 模式切换（休闲/圆桌/项目）、@提及、interrupt
- 任务认领、Git 工件、沙箱工具、合并 2 票
- Facilitator 循环检测 + 注入
- Export、rate limit、auth、i18n stub
- 可直接 docker 启动测试

运行：
1. docker-compose up -d
2. 后端: cd backend && uvicorn app.main:app --reload
3. 前端: cd frontend && npm run dev
4. 浏览器访问 localhost:5173 ，发送消息测试多模型协作

.env.example 已提供，用于配置真实 API keys（设置后 providers 会自动使用真实调用）。

## 用户决策已固化

- 默认角色：Claude=Architect, DeepSeek=Coder 等
- 沙箱：Docker + 严格限制
- 合并规则：执行自由，合并需 2 票或 Facilitator + 人类 veto

详细规格见桌面资料包中的 Markdown 文件。

测试：
cd backend && AETHERCHAT_ENABLE_DEMO_AUTH=1 uvicorn app.main:app --reload
cd frontend && npm run dev

如需继续细化（e.g. 真实 tool_calls 解析、完整 E2E），告诉我！
