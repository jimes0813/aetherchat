# AetherChat - 本地优先的多模型 Agent 工作台

AetherChat 0.2.1-rc.3 是一个 **Linux/Windows 桌面工作台**（React + Electron + FastAPI），把多模型对话、工具授权、本地资料检索、后台自动化和 Agent Run 审计放在同一套本机 SQLite 存储上。仓库 homepage：<https://github.com/jimes0813/aetherchat>。许可：[MIT](LICENSE)。隐私说明：[PRIVACY.md](PRIVACY.md)。代码签名政策：[CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)。

Linux 桌面版的构建与安装说明见 [`frontend/LINUX_PACKAGING.md`](frontend/LINUX_PACKAGING.md)，Windows EXE 安装包说明见 [`frontend/WINDOWS_PACKAGING.md`](frontend/WINDOWS_PACKAGING.md)。

> 早期 web 原型（PostgreSQL + MinIO + Alembic + PR0–PR13 叙事）已经不是当前架构。`docs/CLAUDE_DESKTOP_PARITY_2026-08-01.md`、`docs/LINUX_PORT_REPORT.md` 等标注为历史报告，仅供参考。

当前桌面版支持把 PDF、DOCX、XLSX、PPTX、文本和常见代码文件拖入聊天输入区。资料按会话保存在本机，可在“资料”面板全文检索；随消息发送后，模型上下文会携带页码、工作表、段落或幻灯片位置，便于生成可核对的来源引用。

模型请求运行命令、写入文件、访问电脑或执行其他副作用操作时，会在对话流中显示独立授权卡片。用户可选择允许一次、本会话允许或拒绝；普通聊天文本中的“允许”不会被视为授权。PNG、JPEG 和 WebP 图片可作为多模态附件发送，上传后会先在本机安全重编码。

项目资料检索采用完全离线的 FTS5 + 本地哈希向量 + 重排，不会把资料发送给外部 embedding 服务。“资料”面板会显示检索得分和处理进度；PDF 与 Office 只预览抽取文本，图片只预览安全重编码后的副本。

每个会话还拥有独立的本地持久记忆：滚动摘要、用户明确表达的偏好和决定、未完成事项，以及可手动维护的项目固定说明。模型调用前会使用这些内容和当前任务板补全上下文；用户可以在“记忆”面板审阅、修改或清空，助手自己的说法不会自动变成用户偏好。

Agent 之间还可通过结构化共享实体和标准生成物元数据交换上下文。ComfyUI 成功生成后会自动记录 prompt、模型、seed、尺寸、工作流节点/依赖和真实输出，并写入 `generated/comfyui/*.metadata.json`；任务支持依赖、阻塞检测及关联工具的自动状态同步。协议与调试行为见 [`docs/AGENT_COLLABORATION_PROTOCOL.md`](docs/AGENT_COLLABORATION_PROTOCOL.md)。

模型生成或写入的文件会和对应回复一起持久化，在聊天卡片中可直接打开，或在系统文件管理器中显示所在目录。全部消息仍保存在本机 SQLite 中，“历史聊天”支持跨会话全文检索，最近会话可连同其任务、资料与生成物一起删除。桌面输入区和聊天内容提供原生右键复制、剪切、粘贴与全选菜单。

模型运行时，顶部状态条会显示真实阶段、上下文消息数、估算 token 数、已暴露工具数和首 token 延迟，并配有遵守“减少动态效果”设置的流式动画。本地模型默认只装载当前请求需要的工具，并把有效上下文和单次输出限制在可配置的较小窗口，避免 CPU 推理被无关工具 schema 与过长历史拖慢。ComfyUI 工具在本机服务未运行时可受控自动启动；AetherChat 只跟踪并在退出时终止由自己启动的 ComfyUI 进程，不会关闭用户预先运行的实例。

助手完成处理后，聊天中会显示“实际执行与交付”收据：只统计 AetherChat 实际观察到的处理摘要、工具请求、工具结果和已保存文件，不把模型文字本身当作执行凭证。生成文件会显示真实本机位置，支持安全预览、打开和定位目录，并可一键进入 Agent Run 审计。

桌面版支持全局快速输入和安全深链。Linux/Windows 默认使用 `Ctrl+Alt+Space`，macOS 使用 `Cmd+Option+Space`；快捷键只唤起窗口并聚焦输入框。`aetherchat://chat?prompt=...&mode=project` 可填入当前会话，`aetherchat://new?prompt=...&mode=project` 可新建会话。两者都不会自动发送或绕过授权。

“自动化”面板支持本机单次任务、循环任务和手动立即运行。每个后台 Job 都保存不可变执行参数、只读权限快照、单次预算、SQLite Worker 租约、尝试次数、Agent Run、结果摘要和复核状态；离线跨过多个周期时只补一个追赶任务。关闭工作台窗口后应用驻留系统托盘继续执行，完全退出时未完成 Job 会安全回队并在下次启动恢复。后台任务当前默认只读，不继承聊天中的临时会话授权；即使模型伪造写文件或命令工具调用，也会在真正执行前拒绝。架构、API、状态语义和边界见 [`docs/BACKGROUND_AUTOMATION_2026-08-01.md`](docs/BACKGROUND_AUTOMATION_2026-08-01.md)。

每次多模型执行会持久记录为 Agent Run。项目协作模式进一步建立 Goal Spec 和 Planner → 并行 Worker → Verifier →  Synthesizer 的持久 Plan DAG：Planner 先给出可编辑草稿，用户确认前不会调用 Worker；每次人工修改和 Verifier 返工都有版本快照。中间候选与核验结论留在运行记录，主聊天只接收一份最终交付；核验失败会进行有限次数返工，持续失败则阻止未经验证的综合。应用异常退出后，等待确认的草稿会保留，执行中的 Run 可从未完成 Plan Step 继续。

桌面版还可为会话选择一个干净的 Git 仓库。代码 Worker 从计划确认时冻结的提交创建独立 detached worktree，只能通过限定工具访问该 worktree；结束后由断网 Docker 沙箱运行最多四条已确认命令，并持久化真实 diff、退出码和输出哈希。候选只有经过 Electron 原生确认，且再次通过仓库 HEAD、干净状态和补丁哈希校验后，才会以 `git apply` 写回主工作树；不会自动 commit、push、fetch 或访问网络。浏览器模式和未绑定仓库的 Worker 仍保持只读。安全边界、状态语义和已知限制见 [`docs/GIT_AGENT_WORKSPACES.md`](docs/GIT_AGENT_WORKSPACES.md)。

“连接”面板支持标准 MCP stdio 和 Streamable HTTP 服务器。用户可以检查服务器公开的工具，并逐项决定哪些工具允许进入模型上下文；模型实际调用时仍会弹出独立授权卡片。Bearer/OAuth 凭据、刷新令牌和动态客户端 secret 只写入系统密钥环，OAuth 浏览器授权使用本机回调与 PKCE/state 校验。

桌面版还包含本机语音模式（Piper / CosyVoice）、X11 桌面中介和插件中心。公共 1.0 发布清单见 [`docs/PUBLIC_RC_CHECKLIST.md`](docs/PUBLIC_RC_CHECKLIST.md)。

## 项目结构

```
aetherchat/
├── backend/          # FastAPI 后端（SQLite 单进程）
│   ├── app/{main.py, models/, providers/, services/, sandbox/, routers/}
│   ├── requirements.txt
│   └── pyproject.toml
├── frontend/         # React + TS + Vite + Electron
│   ├── src/
│   ├── electron/
│   └── package.json
├── docs/             # 设计/迁移/安全/阶段报告
├── scripts/          # 质量门禁与发布工具
├── tests/            # 后端测试（必须从仓库根运行，或依赖根目录 pytest.ini）
├── mcp_servers/      # 本地审计 MCP 服务器
└── docker-compose.yml  # 仅可选 Redis；桌面版不使用 Postgres/MinIO
```

Schema 迁移内联于 `backend/app/services/chat_database.py`（及拆分后的 `backend/app/services/db/`），没有独立 Alembic 目录。

## 快速开始（本地开发）

桌面工作台是默认路径。不需要 PostgreSQL 或 MinIO。

```bash
# 1. 后端
python3 -m venv .venv-linux
source .venv-linux/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
# 可选：本机 Redis 用于锁和滑动窗口限流
# docker compose up -d redis
cd backend
AETHERCHAT_ENABLE_DEMO_AUTH=1 uvicorn app.main:app --reload --port 8000

# 2. 前端 / Electron（新终端）
cd frontend
npm install
npm run electron:dev
```

仅调试渲染进程时可用 `npm run dev`（http://localhost:5173），并确保后端已启动。API key 写在仓库根 `.env`（见 `.env.example`）。

## 测试

始终以**仓库根**为工作目录，这样 `mcp_servers` 和 `tests` 都能导入。根目录 `pytest.ini` 已固定 `pythonpath = .`。

```bash
# 统一质量门禁（ruff / mypy / pytest / 前端单元与 e2e）
bash scripts/quality_gate.sh

# 只跑后端
PYTHONPATH=. .venv-linux/bin/python -m pytest -q

# 从 backend/ 子目录跑也应与根目录结果一致
cd backend && ../.venv-linux/bin/python -m pytest ../tests -q
```

前端：

```bash
cd frontend
npm run test:electron
npm run test:unit
npm run test:e2e
```
