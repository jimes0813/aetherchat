# AetherChat 对标 Claude Desktop 的复制完善分析

> **历史报告，仅供参考。** 本文记录 2026-08-01 时点状态，不代表当前 HEAD。当前能力、架构与门禁以仓库根 `README.md` 和最新 `docs/P0_P1_REMEDIATION_REPORT_*.md` 为准。

评估与实施日期：2026-08-01

对象：AetherChat 0.2.0 Linux 桌面工作副本

对标范围：Claude Desktop 的 Chat、Cowork、Code、Artifacts、Skills、Connectors/MCP、Computer Use、Quick Entry、深链与后台任务

## 1. 结论

AetherChat 不应做 Claude Desktop 的像素级复制品。合理的复制对象是 Claude 已验证的工作闭环：

> 快速进入目标 → 组织上下文 → 制订可审阅计划 → 调用工具执行 → 用户批准副作用 → 核对真实结果 → 交付文件或外部状态 → 保留审计记录

AetherChat 已经具备多供应商、本地模型、离线资料检索、会话记忆、MCP、插件、工具授权、版本化工件、Git worktree、确定性核验和多 Agent Plan DAG。这些能力构成比 Claude 更开放、更适合 Linux/内网/中文环境的底座。

主要差距不在“能不能调用工具”，而在四个产品层面：

1. 入口不够快：过去只能先打开主窗口，再寻找会话和输入框。
2. 实际输出不够聚合：模型答复、工具轨迹、文件、保存位置和 Run 审计分散在不同面板。
3. 长任务后台化：第二阶段已补本机后台 Job、单次/循环定时任务、托盘驻留、租约恢复和 Review Inbox；尚缺受限文件变更触发器与独立系统守护进程。
4. 可复用工作规范不足：插件和项目固定说明已经存在，但还没有形成 Skills、Rules、`AGENTS.md` 的分层指令体系。

第一阶段完成快速入口与实际交付闭环；第二阶段继续完成后台 Job 和定时任务核心。下一重点是文件变更触发、Skills/Rules/`AGENTS.md` 与统一观测。

## 2. 功能对照矩阵

| 能力域 | Claude Desktop 的公开能力 | AetherChat 当前状态 | 判断 |
| --- | --- | --- | --- |
| 日常对话 | 多轮对话、附件、引用、项目上下文 | 多模型对话、图片/PDF/Office/代码附件、离线混合检索、引用 | 已达到，AetherChat 的本地与多模型能力更强 |
| 项目与记忆 | Projects、项目指令、上下文、记忆 | 会话资料库、滚动摘要、偏好/决定/待办、项目固定说明 | 基础对等；缺少更清晰的项目主页与分层规则 |
| Cowork 长任务 | 计划、子任务、隔离执行、并行 Agent、转向、最终交付 | Goal Spec、可编辑 Plan DAG、并行 Worker、Verifier、Synthesizer、有限返工、租约恢复和托盘后台执行 | 本机核心闭环已具备；跨端执行不在当前范围 |
| Code 工作流 | 仓库任务、隔离工作区、代码修改、验证 | 可信 repo、独立 detached worktree、断网 Docker 验证、diff 审核、安全 apply | 已形成可审计基础；缺 commit/branch/PR 与冲突处理 |
| Connectors / MCP | 远程与桌面连接器、MCP | stdio、Streamable HTTP、工具逐项暴露、OAuth/密钥环 | 基础对等，AetherChat 的本机控制更细 |
| Skills / Plugins | Skills 与插件扩展工作流 | 声明式插件、工具注册表、项目固定说明 | 部分具备；缺版本化 Skills 与仓库规则解析 |
| Artifacts / 文件 | 创建与预览文档、应用和交互内容 | Markdown/HTML/SVG/代码/Office/PDF 工件、版本历史、安全预览 | 已达到；本批补上聊天内交付卡和本机路径 |
| Computer Use | 连接器优先，必要时浏览器或屏幕交互 | Windows 观察—动作—再观察闭环；Linux 已有带 Electron 回执的 Firefox/默认浏览器导航 | Linux 定向导航已产品化；通用页面观察与交互仍待扩展 |
| Quick Entry | 系统快捷入口 | 本批新增全局快捷输入并聚焦 Composer | 已补齐 |
| 深链 | 从外部链接打开 Claude 指定入口 | 本批新增 `aetherchat://chat` 与 `aetherchat://new` | 已补齐基础版本 |
| 定时与后台 | Cowork 定时任务、后台继续、跨端查看 | 单次/循环/手动 Job、SQLite 租约、心跳、崩溃恢复、只读权限快照、预算和 Review Inbox | 本机基础已补齐；缺文件变更触发和跨端服务 |
| 可观测与治理 | 管理策略、连接器治理、Cowork OpenTelemetry | 工具/Run/授权审计已有；缺统一 OTel 与企业策略分发 | 部分具备 |

## 3. AetherChat 的目标框架

```text
桌面入口层
  全局快捷键 / aetherchat:// 深链 / 桌面快捷操作
        ↓
工作区交互层
  对话 / 圆桌 / 项目模式 / 会话 / 资料 / 记忆 / 任务
        ↓
Agent 编排层
  Direct Run / Goal Spec / Plan DAG / Worker / Verifier / Synthesizer
        ↓
工具与扩展层
  内置工具 / 插件 / MCP / ComfyUI / Computer Use / Git Workspace
        ↓
安全执行层
  对话授权卡 / Electron 原生确认 / Docker 沙箱 / 幂等与恢复
        ↓
交付与审计层
  回复 / 实际执行收据 / 文件 / 本机位置 / diff / Run 审计 / 导出
```

Claude 的价值主要来自这些层形成一致体验。AetherChat 的完善方向也应围绕层间闭环，而不是继续增加互相孤立的一级面板。

## 4. 本批实际完成内容

### 4.1 安全的处理摘要

- 每个参与模型都可以产生 `analysis / tool / result / final` 四类安全摘要。
- 摘要只记录 AetherChat 可观察到的编排事实，例如上下文条数、暴露工具数、工具选择和执行状态。
- 不保存、不展示供应商隐藏推理或模型私密思维链。
- 摘要随消息持久化，历史会话恢复后仍可审阅。

### 4.2 “实际执行与交付”收据

每条具有执行事实的助手消息下方新增统一收据，集中回答：

- 本次处理是否走到最终整理阶段；
- 记录了多少条处理摘要；
- 发起了多少次工具请求；
- 核对了多少条工具结果；
- 实际交付了多少个文件；
- 如何一键进入完整 Agent Run 审计。

产品语义明确区分：

> 模型说“已经完成”不等于实际完成；只有 AetherChat 观察到的工具结果、已保存文件、确定性测试或外部状态才能作为执行凭证。

### 4.3 文件交付闭环

- 生成文件在聊天中显示名称、MIME、大小和真实本机保存位置。
- 图片、视频和音频可在聊天中按需安全预览。
- 支持用系统默认应用打开，或在文件管理器中显示所在目录。
- 其他类型进入现有工件面板，继续使用安全预览、版本历史、diff 和导出。

### 4.4 快速输入

- 桌面版启动时尝试注册全局快捷键：Linux/Windows 为 `Ctrl+Alt+Space`，macOS 为 `Cmd+Option+Space`。
- 快捷键只唤起/聚焦 AetherChat 和输入框，不自动发送，不绕过授权。
- 如果系统占用了快捷键，应用不会错误显示“快捷键可用”；仍可从“运行 → 快速输入”进入。
- 桌面快捷文件新增“快速输入”操作。

### 4.5 `aetherchat://` 深链

支持两个最小、安全的入口：

```text
aetherchat://chat?prompt=检查这份报告&mode=project
aetherchat://new?prompt=创建迁移计划&mode=project
```

- `chat` 在当前会话中聚焦输入框。
- `new` 创建新会话后聚焦输入框。
- `mode` 仅允许 `casual`、`roundtable`、`project`。
- Prompt 去除 NUL，最多 8,000 字符。
- 深链只填入草稿，不会自动提交或批准任何操作。
- 单实例启动时，第二次打开的深链会转交给已有窗口。

### 4.6 真实可核验的浏览器导航

- 浏览器插件不再把 `open_external` 意图直接记为成功。
- 后端通过 WebSocket 发出带唯一 `action_id` 的桌面动作，并最多等待 30 秒。
- Electron 对 Firefox 实际执行 `firefox --new-tab <URL>`，只在进程发出 `spawn` 事件后返回凭证；系统默认浏览器则等待 `shell.openExternal` 交接完成。
- 后端核对浏览器和启动器，只有匹配的 `execution_receipt` 才能使 Tool Run 进入 `success`。
- 用户指定 `Firefox`、`fierfox` 或“火狐”时会定向 Firefox；编排指令明确禁止先打开无关搜索页。
- 启动凭证只证明浏览器启动器已接受请求，不冒充网页内容、登录状态或页面功能已被观察验证。

2026-08-01 的安装版验收从输入框提交“帮我打开fierfox浏览器并进入youtube”，得到状态为 `completed` 的 Qwen Agent Run、带 `launcher=firefox` 和 `verification=launcher_accepted` 的 Tool Run，并在操作系统窗口与视觉层确认 `YouTube — Mozilla Firefox` 和已加载的 YouTube 首页。

## 5. 实际执行输出模型

```text
用户目标
  ↓
模型输出自然语言或结构化 tool call
  ↓
AetherChat 权限策略与用户授权
  ↓
本地工具 / MCP / ComfyUI / Git worktree / 外部连接器执行
  ↓
结构化 tool result、stdout/stderr、退出码、文件、diff 或外部状态
  ↓
模型依据真实结果继续
  ↓
最终回复 + 实际执行收据 + 可打开文件 + Run 审计
```

用户最终能看到的输出分为四类：

1. 文本输出：结论、解释、引用、表格和下一步。
2. 执行输出：工具状态、命令退出码、错误、测试结果、授权决策。
3. 交付物：文件、预览、版本、diff、本机保存位置。
4. 外部副作用：Git apply、日历/任务/数据库等连接器状态；必须由相应工具结果确认。浏览器导航还必须包含 Electron 桌面启动凭证。

## 6. 安全边界

- Quick Entry 与深链只负责导航和草稿注入，不自动发送。
- Renderer 不接收 Node.js 权限；深链由 Electron 主进程解析和白名单校验。
- 只接受 `aetherchat://chat` 与 `aetherchat://new`，不会把外部 URL 当作命令执行。
- 文件打开仍通过后端解析真实位置，并验证为普通文件而非符号链接。
- 外部导航只接受 HTTP(S) 与明确的 `default | firefox` 浏览器枚举；Renderer 不能自行伪造不匹配的启动器凭证。
- 工具副作用仍走“允许一次 / 本会话允许 / 拒绝”策略，高风险写入保持逐次确认。
- “安全摘要”不等于模型完整思维过程，也不得被宣传为 chain-of-thought。

## 7. 验证结果

| 验证项 | 结果 |
| --- | --- |
| 前端 TypeScript + Vite 生产构建 | 通过 |
| Electron 单元测试 | 13 passed |
| 后端回归 | 103 passed, 1 skipped |
| Chromium E2E | 40 passed |
| 无障碍主路径 | 既有 axe serious/critical 门禁继续通过 |
| Playwright 实际界面检查 | 交付收据、保存位置、审计跳转通过 |
| ARM64 Deb 覆盖安装 | 通过；`aetherchat://` 已注册，桌面后端 `/health` 正常 |
| 正式安装版 Qwen → Firefox → YouTube | Agent Run completed，启动凭证落库，窗口与页面验收通过 |

本次 Deb SHA-256：`15b0b92ac0d492abb00621cc83fdbc8de2da523a0ce006fe643c8512ba2e0049`

视觉验收文件：[`output/playwright/claude-parity-delivery-receipt.png`](../output/playwright/claude-parity-delivery-receipt.png)

安装版实际界面：[`output/playwright/aetherchat-installed-claude-parity.png`](../output/playwright/aetherchat-installed-claude-parity.png)

## 8. 后续优先级

### 已完成核心：本机后台 Job 与定时任务

第十七批已把现有 Run 从“可恢复”升级为“桌面窗口关闭后仍可继续”，已完成：

- schedule / manual 触发器；`file-change` 留到受限 Git 路径版本；
- 后台服务租约、心跳和崩溃恢复；
- 独立 Review Inbox；
- 定时任务的权限快照、预算上限和失败通知；
- 禁止定时任务在没有预授权策略时执行高风险副作用。

完整实现分析见 [`BACKGROUND_AUTOMATION_2026-08-01.md`](BACKGROUND_AUTOMATION_2026-08-01.md)。

### P1：Skills、Rules 与项目规范

- 读取仓库根目录到当前工作目录的分层 `AGENTS.md`；
- 支持版本化 Skill 包、适用条件、允许工具、输出契约和验收规则；
- 把项目固定说明、插件、Skill 和模型 Persona 合成为可审计 context manifest；
- 在 Run 中保存指令来源与哈希。

### P1：交付中心

- 将文件、diff、测试、授权和外部副作用统一为 Deliverable Manifest；
- 为 `uncertain` 调用增加人工处理界面；
- 支持按 Run 导出完整审计包；
- 将“运行”面板升级为待确认、失败、已完成三个收件箱。

### P2：企业治理与观测

- OpenTelemetry traces / metrics / logs；
- 组织级连接器和工具白名单；
- 数据出机边界与模型路由策略；
- 预算、保留期、审计导出和策略签名。

## 9. 官方对标依据

- [Claude Desktop 安装与桌面入口](https://support.claude.com/en/articles/10065433-install-claude-desktop)
- [Claude Desktop / Code 快速开始](https://code.claude.com/docs/en/desktop-quickstart)
- [Claude Cowork 架构概览](https://support.claude.com/en/articles/14479288-claude-cowork-architecture-overview)
- [Claude Cowork 多端使用](https://support.claude.com/en/articles/15520349-use-claude-cowork-on-web-desktop-and-mobile)
- [Claude Desktop 中的本地 MCP](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)
- [Claude Skills](https://support.claude.com/en/articles/12512176-what-are-skills)
- [Claude Plugins](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)
- [Claude Cowork Live Artifacts](https://support.claude.com/en/articles/14729249-use-live-artifacts-in-claude-cowork)
- [Claude Computer Use](https://support.claude.com/en/articles/14128542-let-claude-use-your-computer-in-cowork)
- [Claude Cowork 定时任务](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork)
- [Claude Desktop Quick Entry](https://support.claude.com/en/articles/12626668-use-quick-entry-with-claude-desktop-on-mac)
- [Claude Desktop 深链](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link)
- [Model Context Protocol 架构](https://modelcontextprotocol.io/docs/learn/architecture)

## 10. 产品定位

最终建议定位不是“另一个 Claude”，而是：

> 面向中文、Linux、本地与私有环境的多模型 Agent 控制台：模型可替换、数据可本地、过程可审计、结果可验证。

复制成熟工作流，保留 AetherChat 的开放模型、本地优先与安全审计优势，才能形成可持续差异化。
