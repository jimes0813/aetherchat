# AetherChat 优化推进记录

更新日期：2026-07-27

## 已完成：阶段 0 / 第一批 P0

- [x] 首页从“模型在线”改为可验证的“模型已配置”。
- [x] 未配置密钥的云模型不再进入“发送给全部模型”的目标集合。
- [x] 浏览器开发模式不再错误显示为 Windows。
- [x] Electron 每次启动生成随机本机 API 令牌。
- [x] HTTP Router 默认拒绝，统一要求 Bearer 令牌。
- [x] WebSocket 在握手阶段校验令牌和 Origin，令牌通过子协议头而非 URL 传输。
- [x] Renderer 令牌只保存在内存，通过受信 IPC 获取。
- [x] Demo 认证默认关闭，只允许开发者显式启用。
- [x] Linux/macOS API Key 使用系统 Keyring；JSON 只保存引用。
- [x] 旧版 Linux 明文密钥支持迁移，密钥环不可用时不再明文降级。
- [x] 沙箱使用真实会话工作区和真实 cwd。
- [x] 沙箱默认断网、限制资源、移除 capabilities、启用 no-new-privileges。
- [x] 沙箱返回真实退出码/超时，非零退出在工具审计中标记为失败。
- [x] Docker SDK 调用移到工作线程，避免阻塞 WebSocket 事件循环。
- [x] 上下文按每个模型的 token_limit/max_tokens 构建，不再固定截断到 8K。
- [x] 截断时保留系统指令与最新请求，并避免孤立 assistant/tool 消息。

## 已完成：第二批 P0（项目资料第一阶段）

- [x] 聊天输入区支持点击选择或拖放 PDF、Office、文本和常见代码文件。
- [x] 附件按会话隔离持久化，使用服务端 UUID 路径，不信任原始文件名。
- [x] 单文件限制 25 MB，并校验 PDF/Office 文件签名、文本二进制内容及 Office 解压展开大小。
- [x] PDF 按页、DOCX 按段落/表格、XLSX 按工作表/行、PPTX 按幻灯片抽取。
- [x] SQLite FTS5 项目级全文索引和中文查询回退已接入。
- [x] 新增“资料”面板，可查看、检索和删除当前会话资料。
- [x] 消息持久化附件元数据，历史对话可恢复附件标签。
- [x] 模型只接收本条消息明确附带的资料片段，并要求用文件名和位置引用来源。
- [x] 覆盖伪装 PDF、超限文件、跨会话删除、全文搜索和 PDF 页码引用测试。

## 当前验收结果

- 后端：77 项通过，1 项 Windows 平台测试跳过。
- Electron 更新验证：3 项通过（签名、降级/架构/URL 拒绝、流式大小与哈希校验及失败清理）。
- 前端生产构建：通过。
- 前端 E2E：29 项通过（新增可信 Git 仓库选择、核验证据和原生 apply 桥接，并覆盖计划编辑/确认、运行中转向提示、焦点圈定/恢复、axe、减少动画和等效 200% 缩放）。
- PyInstaller ARM64 后端：构建通过。
- 打包后端认证与工件烟测：无令牌 401，正确 Bearer 200，打包后端可生成 DOCX。
- LibreOffice 兼容性烟测：Writer / Calc / Impress 均成功打开本批生成的 DOCX / XLSX / PPTX 并转换为 PDF。
- npm audit：0 个已知漏洞；Electron 31 → 43.1.1、electron-builder 24 → 26.15.3、Vite 5 → 8.1.5。
- ARM64 AppImage SHA-256：`c1689aff36e857d08ffd48c0241c12ba169a6d8b18dd4fb2a877f52ddb1aadde`。
- ARM64 Deb SHA-256：`79af9a68a84d258e70cbf1547b14b488385d1ecebe66c4979b4471a4671efed7`。
- CycloneDX 1.5 SBOM：450 个组件，SHA-256 `a818ac4926c2bdc8560a81b32b6f8187e0dab50b6766a314f03c04d5174a77e9`。
- ARM64 构建元数据 SHA-256：`5601b203a3a0cbbcf196dd048f25e013fb782180bfb3c3de79859391dee6626b`。
- Deb 安装版沙箱终验：`/opt/AetherChat/chrome-sandbox` 为 `root:root`、模式 `4755`；不带 `--no-sandbox` 启动成功，Renderer 为 `NoNewPrivs=1`、`Seccomp=2`，受保护业务 API 无令牌返回 401。

## 下一批：核心聊天与项目知识

- [x] 聊天图片上传、缩略图和多模态模型输入。
- [x] 附件安全预览：文档只展示抽取文本，图片只展示安全重编码副本。
- [x] Markdown、代码块、表格、引用和安全链接的结构化渲染，不执行原始 HTML。
- [x] 按会话持久化的项目固定说明（项目级文件索引已完成）。
- [x] SQLite FTS5、本地哈希向量、词项覆盖与精确短语重排的离线混合检索。
- [x] 回答引用文件、页码、工作表、段落、幻灯片和原文片段。
- [x] 滚动摘要、关键决定、用户偏好和未完成任务记忆。

## 已完成：第三批 P0（对话授权与多模态）

- [x] 模型副作用操作改为对话流内独立授权卡片，而不是依赖可伪造的普通聊天文本。
- [x] 提供“允许一次 / 本会话允许 / 拒绝”决策；高风险操作只显示逐次授权。
- [x] 命令执行、工作区写入、合并决策、电脑观察/控制、本机文件写入、外部导航和本地生成分别应用风险策略。
- [x] 会话授权只存于后端内存，进程退出即清除；超时或连接中断默认拒绝。
- [x] 授权卡片仅显示脱敏参数，文件正文、密钥、口令和桌面输入内容不会直接展示。
- [x] 已在对话中批准的电脑操作通过受控上下文避免重复原生弹窗；非对话入口仍保留 Electron 原生授权。
- [x] PNG/JPEG/WebP 上传后重新编码，剥离元数据和尾随载荷，并限制像素和总视觉输入大小。

## 已完成：第四批 P1（安全预览与离线混合检索）

- [x] 每个文本片段同时写入 FTS5 与 256 维本地哈希向量，零网络、零外部 embedding 费用。
- [x] 查询融合全文排名、向量余弦相似度、词项覆盖率和精确短语命中。
- [x] 旧版资料库启动时自动补建向量索引，无需重新上传文件。
- [x] 检索结果显示归一化得分以及“全文 / 哈希相似”命中来源，不把非学习型向量误标为语义 embedding。
- [x] PDF、Office、代码和文本预览只返回抽取后的纯文本片段，不向浏览器提供原文件。
- [x] 图片预览只返回已重编码副本，并设置 `no-store`、CSP sandbox 和 `nosniff`。
- [x] 资料面板显示加载、混合检索和安全预览的阶段进度。
- [x] 预览数量、返回片段和向量候选均有上限，避免无界内存与响应体。

## 已完成：第五批 P1（可审阅的持久会话记忆）

- [x] SQLite 按会话持久化项目固定说明、滚动摘要、用户偏好、关键决定和未完成事项。
- [x] 只从用户明确表达中自动提取偏好、决定和待办；助手输出不会写入用户意图记忆。
- [x] 模型调用前注入当前会话记忆和任务板未完成任务，并限制记忆上下文总长度。
- [x] 最近对话生成本地滚动摘要，应用重启后可继续恢复，无需外部模型或网络服务。
- [x] 新增“记忆”面板，可逐项审阅、修改或清空自动提取内容及项目固定说明。
- [x] 新增带认证的记忆查询/更新 API；不存在的会话和跨会话数据保持隔离。
- [x] 覆盖用户意图提取、助手污染拒绝、数据库重启恢复、会话隔离和界面编辑 E2E。

## 总体进度

当前约 **99%**。跨平台发布代码、供应链拒绝路径、ARM64 安装版沙箱终验、核心中英文 UI、主要检查器/历史/任务文案、焦点管理、axe、缩放/减少动画、压力拒绝路径和零已知 npm 漏洞已经完成；剩余为少量次级表单/诊断文案迁移与人工屏幕阅读器终验，以及依赖真实 GitHub 的跨平台 CI 与正式签名。

## 已完成：第六批 P1（可恢复 Agent 运行状态机）

- [x] SQLite 持久化 Agent Run、模型 Step 与关键 Event，按会话隔离并保留时间线。
- [x] 记录排队、运行、等待授权、完成、失败、中断和可恢复状态，不持久化高频 token 增量。
- [x] 应用启动时把未完成运行转换为“可恢复”，不会在无人确认时自动重放副作用操作。
- [x] 重试仅选择失败、中断或未完成的模型步骤，已完成模型不会重复执行。
- [x] 内部重试不会重复写入用户消息，外部 WebSocket 消息不能伪造内部重试标志。
- [x] 新增带认证且按会话校验的运行列表、详情及事件 API。
- [x] “运行”面板显示运行状态、模型步骤、尝试次数和失败原因，并提供明确的恢复按钮。
- [x] 覆盖状态转换、事件持久化、失败重试、重启恢复和跨会话拒绝测试。

## 已完成：第七批 P1（标准 MCP 与 OAuth 连接器）

- [x] 使用官方 Python SDK 稳定版 v1，固定 `mcp>=1.27,<2`，避免尚未稳定的 v2 API 漂移。
- [x] 支持标准 stdio：参数数组直接启动子进程，不经过 shell，并使用最小化环境变量集合。
- [x] 支持标准 Streamable HTTP：初始化、会话管理、工具发现和调用均由官方 ClientSession 处理。
- [x] 远程 URL 强制 HTTPS；HTTP 仅允许 localhost/loopback，禁止 URL 内嵌凭据、代理继承和自动跳转。
- [x] 支持 Bearer、OAuth Access Token 导入，以及浏览器 OAuth 授权码/PKCE 流程。
- [x] OAuth access/refresh token 和动态客户端 secret 只保存到系统密钥环，连接配置文件只保留引用。
- [x] MCP 工具使用独立命名空间，可逐项控制是否暴露给模型；规范化名称冲突时拒绝连接。
- [x] 所有模型发起的 MCP 工具调用进入对话内授权卡片，可允许一次、本会话允许或拒绝。
- [x] 新增“连接”面板和受本机 Bearer 令牌保护的连接器管理 API。
- [x] 使用真实官方 stdio 与 Streamable HTTP 测试服务器覆盖初始化、发现、调用、权限、URL 拒绝和凭据隔离。

## 已完成：第八批 P1（交互式工件与高质量 Office 输出）

- [x] 工件列表统一返回版本、来源模型、会话、SHA-256、MIME、状态和父版本，并兼容旧工件历史。
- [x] Markdown、HTML、SVG、代码、DOCX、XLSX、PPTX 工件具有类型化安全预览；未知/危险二进制类型只允许下载原文件。
- [x] HTML/SVG 预览仅进入空权限 sandbox iframe，强制禁脚本/网络/表单/对象 CSP，并在服务端移除脚本、事件和导航/资源 URL。
- [x] Markdown 使用既有安全结构化渲染器；代码和 Office 摘要以纯文本展示，不注入主 Renderer DOM。
- [x] 提供编辑、完整版本历史、统一 diff、非破坏性版本恢复和 DOCX/XLSX/PPTX/Markdown 导出闭环。
- [x] DOCX 支持标题层级、页眉页脚、分页、表格、引用和安全重编码的 data URI 图片。
- [x] XLSX 支持 JSON 多工作表/CSV/TSV、类型写入、冻结窗格、表头样式、列宽和公式注入转义。
- [x] PPTX 使用标准宽屏母版与布局，支持分节幻灯片、文本溢出拒绝、安全图片和演讲者备注。
- [x] 工件编辑/预览限制 5 MB；Office 解包限制 50 MB、单项 20 MB、最多 10,000 项；路径穿越、跨会话访问、错误版本和未知导出格式默认拒绝。
- [x] 浏览器下载不接受任意本机目标路径；模型发起的工件写入仍由既有对话内授权策略控制，高风险本机覆盖仍只能逐次授权。
- [x] 新增后端拒绝路径/Office 打开测试和前端完整闭环 E2E，原有测试无回归。

## 进行中：第九批 P1（跨平台发布、签名更新与 SBOM）

- [x] 新增 Linux x64、Linux ARM64、Windows x64 GitHub Actions matrix；三平台均配置后端测试、Electron 拒绝路径、前端 build 和 E2E。
- [x] 新增 Windows PyInstaller + NSIS 脚本，以及静默安装、覆盖升级、卸载和用户数据保留烟测脚本。
- [x] Linux 构建继续输出 AppImage/Deb；Deb 强制 `chrome-sandbox` 为 root 所有、模式 4755，并由 CI 读取归档验证。
- [x] 移除 `aetherchat.local`，统一使用项目 GitHub 主页和维护者 noreply 邮箱。
- [x] tag 构建只从 GitHub Secrets 注入 Ed25519 私钥/公钥和 Windows 代码签名证书；缺失时发布失败，普通构建的更新功能默认禁用。
- [x] 实现 Ed25519 签名更新清单和 Electron 更新入口；Windows 正式包启动与每 6 小时自动检查、静默下载，并在 Ed25519、HTTPS、新版本、平台、架构、大小、SHA-256 与 Authenticode 全部通过后静默安装重启；Linux 保留手动签名更新。
- [x] 更新包流式写入随机 `.partial`，校验成功后原子暂存；失败清理临时文件且不修改当前安装或用户数据。
- [x] 生成 CycloneDX 1.5 SBOM、逐产物 SHA-256 和可追溯构建元数据；Python 关键依赖已固定精确版本，npm 使用 lockfile。
- [x] 覆盖签名缺失/错误、清单篡改、降级、错误架构、HTTP URL、大小/哈希不符和残留临时文件拒绝路径。
- [ ] 在恢复 Git 仓库后实际运行三平台 matrix，并使用正式 secrets 产出 Authenticode Windows 包和签名更新清单。
- [x] 使用管理员权限安装 ARM64 Deb；已核验 `/opt/AetherChat/chrome-sandbox` 为 root:root/4755，不带 `--no-sandbox` 的正式安装版窗口和打包后端启动成功，Renderer 启用 no-new-privileges/seccomp，业务 API 默认拒绝无令牌请求。

## 进行中：第十批 P1（无障碍、i18n 与发布终验）

- [x] 新增独立 `zh-CN` / `en-US` UI 资源和持久化界面语言；核心导航、聊天、Composer、授权卡片和状态栏无需重启即时切换。
- [x] 日期/时间跟随 UI locale；`html lang` 与窗口标题同步更新。
- [x] 侧栏与命令面板实现焦点圈定、Escape 关闭和关闭后焦点恢复；新增键盘跳到主要内容链接。
- [x] 授权卡片明确显示主体、工具、风险和范围；DOM/Tab 顺序为拒绝、会话允许、允许一次，默认焦点不会落在允许按钮。
- [x] 增加全局高可见焦点、AA 小字对比度修复、`prefers-reduced-motion` 和 forced-colors 规则。
- [x] 引入 axe 4.12.1；核心聊天路径无 serious/critical，键盘、窄视口/等效 200% 缩放 E2E 通过。
- [x] 升级存在公开高危公告的 Electron/Vite/打包工具链，`npm audit` 从 8 项降为 0。
- [x] 历史/任务视图以及记忆、资料、任务、工件、变更、工具、插件、连接、电脑、模型、运行、设置 12 个检查器的主标题/关键控件接入英文显示资源；错误和状态消息支持静态及前缀化动态本地化。
- [x] 英文模式 E2E 逐一打开 12 个检查器，校验英文可见标题、英文可访问名称，并用中文 UI 禁止列表阻止关键漏翻回归。
- [ ] 迁移少量次级表单说明、桌面控制细项、插件动作和运行诊断的剩余散落字符串。
- [x] 新增长会话并发写入/重启恢复、30 文档资料库检索和低磁盘无半成品压力测试；既有测试覆盖多模型中断、MCP 生命周期和后端恢复。
- [ ] 按 `docs/ACCESSIBILITY_CHECKLIST.md` 完成 Orca/NVDA 人工屏幕阅读器清单。
- [ ] 完成 Windows/x64 CI、正式签名发布和人工屏幕阅读器终验后，才能更新为 100%。

## 已完成：第十一批 P0（供应商原生工具协议）

- [x] 新增供应商无关的 `ToolCall`、`tool_call_id` 与工具错误状态，工具调用和结果不再退化为普通文本。
- [x] OpenAI-compatible 请求原生发送 assistant `tool_calls`，并以逐条 `tool` 消息和对应 `tool_call_id` 回传结果。
- [x] Anthropic 请求原生发送 `tool_use` 内容块；并行结果合并为紧邻的 user `tool_result` 内容块，并支持 `is_error`。
- [x] 工具截图在 Anthropic 结果块内传递；OpenAI 在所有原生工具结果后追加视觉观察，不打断多结果关联。
- [x] 编排器为缺失或重复的调用 ID 生成唯一 ID，每个并行调用拥有独立结果消息、工具名、错误状态和图片。
- [x] 工具结果继续执行时保持完整原生历史；上下文超限按完整用户回合裁剪，避免孤立 assistant/tool 协议片段。
- [x] 覆盖 OpenAI/Anthropic 双向协议、Anthropic 流式参数、并行结果映射、ID 修复、错误与图片内容块的离线回归测试。
- [x] 当前回归验收：后端 73 passed / 1 skipped、Electron 3 passed、前端生产构建通过、Chromium E2E 27 passed。
- [ ] 使用真实 OpenAI 与 Anthropic 云模型执行固定金丝雀场景；当前未使用付费密钥，离线协议测试不代表真实模型完成率。
- [x] 将工具调用链、供应商 tool call ID、参数/结果哈希和副作用幂等键持久化到 Agent Run checkpoint。

## 已完成：第十二批 P1（Agent Run 2.0 基础）

- [x] 新增持久化工具调用记录，保存 Run/Step、供应商、模型、原生调用 ID、脱敏参数、参数/结果哈希、状态、尝试次数和工具运行 ID。
- [x] 以运行、模型、工具轮次、调用顺序、工具名和规范化参数形成稳定幂等身份；已确认完成的副作用调用直接重放既有结果，不再次执行。
- [x] 对重启、执行中断或失败后结果不确定的副作用调用默认阻止自动重放，并标记为 `uncertain`、要求人工核查；只读调用仍可安全重试。
- [x] 每个模型在工具轮次、运行中转向和完成边界保存规范化对话 checkpoint；重试从最新 checkpoint 和下一工具轮次继续。
- [x] 活跃 Run 中的第二条业务消息不再返回 `run_in_progress`，而是持久化为 steer event，在下一安全边界注入指定模型；尚未执行的旧工具调用会被丢弃。
- [x] 中断协议支持 `run_id + step_id`，服务端按持久化步骤解析目标模型；原有全局/模型中断保持兼容。
- [x] 运行详情 API 返回工具调用、checkpoint、steer 及计数；运行面板显示三类审计摘要并提示 steer 已排队。
- [x] 模型调用次数达到上限或失败时，已有文本只记为部分输出，不再把失败步骤误标为完成。
- [x] 覆盖幂等重放、未知副作用阻断、重启恢复、checkpoint 续跑、陈旧工具调用丢弃、WebSocket steer、步骤中断和部分输出状态测试。
- [x] 本批验收：后端 73 passed / 1 skipped、Electron 3 passed、前端生产构建通过、Chromium E2E 27 passed。
- [ ] 通过插件/连接器原生幂等令牌或事务 outbox 缩小“外部副作用已发生、结果尚未落库”崩溃窗口；当前只能安全阻断自动重放，不能宣称跨系统 exactly-once。
- [ ] 支持 steer 附件、远程模型单次请求内部的即时转向，以及人工处理 `uncertain` 调用的专用界面。

## 已完成：第十三批 P1（Agent Run 2.1：Goal Spec 与 Plan DAG）

- [x] 新增持久化 `AgentGoal`、`PlanStep` 和 `Review`，记录目标、成功条件、约束、交付定义、依赖、角色、输入/输出契约、步骤输出哈希和核验结论。
- [x] 项目协作由前端显式发送 `execution_strategy=planned`；旧客户端及日常/圆桌模式继续走原有直接路径，不会被无提示地升级为多阶段调用。
- [x] 确定性 Planner 根据可用模型和 persona 生成四阶段无环图：Planner → 并行 Worker → Verifier → Synthesizer。
- [x] Worker 使用互相独立的上下文；Verifier 接收持久化候选结果并输出结构化 verdict/evidence/issues；Synthesizer 接收目标、候选和核验结果，只向主聊天交付一份最终答案。
- [x] Worker/Verifier 的原始结果只进入本机运行记录，不再向统一对话流堆叠多份中间回答。
- [x] 调度器按依赖和 phase 执行；允许 Verifier/Synthesizer 在部分 Worker 失败时降级完成，同时保留失败证据。
- [x] 重试只重置未完成 Plan Step，已完成 Worker 输出直接复用；同一模型在不同 Plan Step 的工具幂等身份和 checkpoint 已分开作用域。
- [x] Agent Run 2.1 当时尚无 worktree/隔离 workspace，Worker 与 Verifier 因而只暴露只读工具；Agent Run 2.3 已在绑定可信仓库时用隔离 worktree 替代该临时限制。
- [x] 每个阶段重新检查预算；全局中断会停止后续 phase，模型中断可定位到持久化 Plan Step。
- [x] 运行面板显示 Goal、计划完成度、四类步骤、尝试次数和最新 Review；项目模式明确提示会增加模型调用与成本。
- [x] 覆盖 DAG 持久化、角色路由、结构化 handoff、单一最终交付、计划级恢复、失败依赖降级、只读 Worker 拒绝路径、计划中断和 Plan Step 定位测试。
- [x] 本批验收：后端 73 passed / 1 skipped、Electron 3 passed、前端生产构建通过、Chromium E2E 27 passed。
- [x] 执行前计划确认/编辑与 `needs_revision` 有界修订循环已在 Agent Run 2.2 完成。
- [x] 代码任务的 repo/worktree 隔离、真实 diff/test verifier 和安全 apply 已在 Agent Run 2.3 完成；未接入仓库的 Worker 仍保持只读。

## 已完成：第十四批 P1（Agent Run 2.2：可审核计划与有界返工）

- [x] 项目模式新建 Run 后先进入持久化 `waiting_plan_confirmation`；Planner 只生成草稿，确认前不会调用 Worker、Verifier 或 Synthesizer。
- [x] 新增 `plan_status`、单调 `plan_revision`、自动返工计数、批准时间和 `agent_plan_revisions` 快照，完整保留 Planner、人工编辑与 Verifier 重规划版本。
- [x] 运行面板可编辑 Goal、交付物、成功条件、约束，以及步骤标题、说明、角色和已获准模型；保存采用期望版本检查，过期客户端不能覆盖新计划。
- [x] 计划依赖必须存在且来自更早 phase，Planner 不可被客户端改写，步骤模型只能从本 Run 原始目标集合选择。
- [x] 用户可确认并在同一 Run 上恢复执行，或拒绝计划并把未执行步骤标为 skipped；等待确认状态跨应用重启保持，不会被误判成中断恢复。
- [x] 前端保存计划后等待服务端版本回执，避免立即确认时携带旧 revision 的竞态。
- [x] Verifier 返回 `needs_revision` 或 `fail` 时，调度器按配置执行 0–2 次有界返工（默认 1 次），把上一版 Worker 候选和结构化 issues 注入返工上下文。
- [x] 返工后的 Verifier 通过才进入正常综合；在次数耗尽后仍为 `fail` 会阻断 Synthesizer，不能把未通过核验的结果包装成完成。
- [x] 未显式发送 `plan_confirmation_required` 的旧客户端仍保持 Agent Run 2.1 自动执行语义；日常和圆桌模式不受影响。
- [x] 覆盖确认前零模型调用、非法依赖、版本冲突、编辑/确认/拒绝、跨重启草稿、自动返工通过、重复核验失败阻断、WebSocket 同 Run 恢复和前端交互测试。
- [x] 本批验收：后端 73 passed / 1 skipped、Electron 3 passed、前端生产构建通过、Chromium E2E 27 passed。
- [ ] 当前返工会重跑全部 Worker，尚未根据 issue 精确选择补偿边，也不会动态增删 DAG 节点。
- [x] 代码任务的模型 Verifier 已叠加不可由模型伪造的 Git diff/命令退出码证据，并在 Agent Run 2.3 加入人工批准的安全 apply。

## 已完成：第十五批 P1（Agent Run 2.3：Git worktree 与确定性核验）

- [x] 仅允许 Electron 受信主进程通过系统目录选择器登记仓库；浏览器 Renderer 没有任意绝对路径输入，也拿不到仓库根路径。
- [x] 登记和 Run 快照要求非裸、干净且有有效 HEAD 的 Git 工作树；会拒绝自定义 Git clean/smudge/process 过滤器，Git hooks、全局配置、系统配置和 fsmonitor 在服务命令中被禁用。
- [x] 每个 Worker/attempt 从同一冻结提交创建 AetherChat 自管的 detached worktree；模型只得到不透明 workspace ID，不能选择、替换或越权访问主机路径。
- [x] 新增限定在 Worker worktree 内的列举、UTF-8 读取、原子写入、删除、状态和断网 Docker 命令工具；无 worktree 上下文时默认拒绝。
- [x] Worker 结束后直接采集 Git 状态与 binary/full-index patch，限制为 200 个文件和 2 MiB，并拒绝符号链接、子模块和 `.git` 路径。
- [x] 核验命令在只挂载对应 worktree、默认断网的 Docker 沙箱运行；持久化命令、真实退出码、超时、输出摘要/哈希、diff 哈希和变更文件。
- [x] 模型 Verifier 的 `pass` 不能覆盖全部候选的失败退出码或空 diff；没有核验命令的候选明确标为 `unverified`，不会伪装成测试通过。
- [x] 运行面板在计划确认前展示仓库快照和核验命令，运行后展示每个候选的文件、diff 大小与命令退出码。
- [x] 应用/丢弃候选只能通过 Electron 原生确认；应用前在同一锁内再次检查 session、Run 终态、补丁哈希、主仓库 HEAD 与干净状态，再执行 `git apply --check` 和 `git apply`。
- [x] apply 不会 commit、push、fetch 或访问网络；主仓库出现新提交、未提交或未跟踪改动时默认拒绝，避免覆盖用户工作。
- [x] 覆盖主仓库零污染、新文件 binary patch、跨会话/桌面令牌、失败命令覆盖模型 pass、脏树竞态、完整编排工具上下文和前端审核/apply 流程。
- [x] 本批验收：后端 77 passed / 1 skipped、Electron 3 passed、前端生产构建通过（主 JS 314.29 KB / gzip 95.97 KB）、Chromium E2E 29 passed。
- [ ] 下一步仍需读取并分层执行 `AGENTS.md`/项目规则、按 issue 精确选择返工 Worker、补丁冲突可视化，以及显式 commit/branch/PR 工作流。
- [ ] 当前核验依赖 Docker 沙箱镜像；仓库级依赖缓存、超大/多仓库改动、Git LFS/子模块和后台自动化尚未支持。

## 已完成：模型连接与本地运行时修复

- [x] 本地模型调用和连接验证前统一检查 OpenAI-compatible 端点；端点未运行时不再只返回模糊的 `Connection error`。
- [x] 新增受控 `llama.cpp` 自动启动：仅允许 HTTP 回环地址、直接参数数组且不经过 shell，并校验 `llama-server`、GGUF 文件和受保护参数。
- [x] 同一端点并发启动去重，等待 `/v1/models` 就绪；启动退出、超时和外部端点未运行均返回可操作错误。
- [x] 后端退出时只终止自身托管的模型进程，不影响用户已在外部启动的模型服务。
- [x] 云端 OpenAI-compatible、Anthropic/xAI 模型保持直接 API 连接，不触发任何本机模型进程；缺少密钥的云端模型继续默认禁用。
- [x] 模型面板支持“外部服务（仅连接）/ llama.cpp 自动启动”、GGUF 路径、可执行文件和逐行参数配置；本地模型按钮明确为“启动并验证”。
- [x] 新增运行时状态与启动 API；当前大写 `Qwen` 已绑定对应 GGUF、8080 端点和 `llama-server`。
- [x] 独立 18080 端口实际拉起 1.7B Qwen、等待就绪并完成真实 `OK` 对话，测试后端退出后托管进程同步停止；现有 35B Qwen 真实验证也返回 `OK`。
- [x] 新 Deb 已使用 `--reinstall` 覆盖系统安装；`app.asar` 与打包后端分别和 Deb 内文件 SHA-256 完全一致，正式窗口、8000 后端及对应 8080 Qwen 均在线。

## 后续阶段

- [x] 持久 Job/Step/Event Agent 状态机和重启恢复。
- [x] 标准 MCP stdio/Streamable HTTP Client 与 OAuth 连接器。
- [x] 交互式工件、安全预览和高质量 Office 输出。
- [x] OpenAI-compatible / Anthropic 供应商原生工具调用与结果往返。
- [x] Agent Run 2.0 基础：持久工具链、checkpoint、副作用幂等保护、运行中 steer 与步骤中断。
- [x] Agent Run 2.1：Goal Spec、Plan DAG、并行 Worker、Verifier、Synthesizer 与计划级恢复。
- [x] Agent Run 2.2：执行前计划编辑/确认、版本快照、乐观并发控制与 Verifier 有界返工。
- [x] Agent Run 2.3：可信 repo 接入、Worker 独立 worktree、确定性 diff/test 证据与原生确认后的安全 apply。
- [ ] x64/ARM64/Windows CI、签名更新、SBOM、无障碍和完整 i18n。

## 已完成：第十六批 P1（Claude Desktop 入口与交付闭环对标）

- [x] 为每个参与模型生成 `analysis / tool / result / final` 安全处理摘要，只记录可观察的编排事实，不持久化供应商隐藏推理。
- [x] 在助手消息中新增“实际执行与交付”收据，汇总处理摘要、工具请求、结果核对和已保存文件，并明确模型文字不是执行凭证。
- [x] 生成文件显示 MIME、大小与真实本机保存位置；图片、视频和音频可按需在聊天中预览。
- [x] 支持从交付收据一键进入 Agent Run 审计，并保留工件面板、系统默认应用和文件管理器三个查看入口。
- [x] Electron 新增全局快速输入；注册成功时才在状态栏显示快捷键，失败时仍保留菜单入口。
- [x] 新增 `aetherchat://chat` 与 `aetherchat://new` 深链、单实例转交、模式白名单、NUL 清理和 8,000 字符上限。
- [x] 快速输入与深链只聚焦/填入草稿，不自动发送，不改变既有工具和副作用授权边界。
- [x] 桌面快捷文件新增快速输入动作；electron-builder 注册 `aetherchat` protocol。
- [x] 本批验收：后端 98 passed / 1 skipped、Electron 10 passed、前端生产构建通过、Chromium E2E 38 passed、Playwright 视觉检查通过。
- [x] 下一步第一部分：durable background jobs、定时任务与 Review Inbox（见第十七批）。
- [ ] 下一步：受限文件变更触发器、Skills/Rules/`AGENTS.md` 分层指令和 OpenTelemetry。

## 已完成：第十七批 P0（持久后台 Job、定时任务与 Review Inbox）

- [x] SQLite 新增 `automation_schedules` 与 `background_jobs`，保存触发计划、不可变 Action、权限快照、预算、租约、尝试次数、执行摘要和复核状态。
- [x] 支持单次、循环和手动立即运行；同一 Schedule/计划时间唯一，离线跨周期只生成一个追赶 Job，避免恢复任务洪峰。
- [x] 新增本机调度循环、SQLite 原子租约、心跳、过期接管、最多三次恢复和正常退出回队。
- [x] 后台 Job 复用 Agent Run/Checkpoint/Tool Call 状态机；已完成 Run 不重复执行，可恢复 Run 只重试未完成步骤。
- [x] 无人值守策略默认为只读，创建时固化且不继承普通聊天的临时授权；未授权副作用同时在工具暴露层和执行层拒绝。
- [x] 每个 Job 使用独立预算上限并持久化实际消耗；完成、失败和取消结果进入 Review Inbox。
- [x] 活动栏新增“自动化”面板，可创建、暂停、启用、删除、立即运行、取消、查看审计和标记已复核。
- [x] Electron 关闭窗口后驻留系统托盘；完全退出才停止本机后端，未完成 Job 安全回队；后台结果通过本机通知和渲染器刷新事件送达。
- [x] 详细设计与边界见 `docs/BACKGROUND_AUTOMATION_2026-08-01.md`。
- [x] 本批验收：后端 101 passed / 1 skipped、Electron 10 passed、Chromium E2E 39 passed、前端生产构建、Ruff 与 Mypy 全部通过。
- [ ] 下一步：受限 Git 文件变更触发器、工具级预授权模板、Review Inbox 分类筛选、Skills/Rules/`AGENTS.md` 和 OpenTelemetry。

每一批必须同时满足：代码实现、拒绝路径测试、生产构建、E2E 回归和文档更新。

## 工程规范：后端静态检查接入（ruff + mypy）

配置：`backend/pyproject.toml`；工具依赖：`backend/requirements-dev.txt`（`ruff==0.14.4` / `mypy==1.13.0`，与运行时依赖同样固定版本）。CI 在 `linux-x64` 目标上执行（避免跨矩阵重复）。

- [x] **ruff = 硬门禁**。规则集 `E,F,W,I,UP,B,C4,SIM,PIE`，`line-length=120`。CI `ruff check app` 绿灯（exit 0）。
- [x] **mypy = 硬门禁**。115 个存量类型问题已清零；CI 不再使用 `continue-on-error`。
- [x] **自动修可自动修项**。`ruff check --fix`（仅安全修复）清除 189 处：import 排序(`I001`)、typing 现代化(`UP006`/`UP045`)、`datetime.UTC`(`UP017`)、内置 `TimeoutError`(`UP041`)、弃用 import(`UP035`)、行尾空白等。改动 39/42 个 `app` 文件，**行为等价**：`pytest` 77 passed / 1 skipped，与接入前基线一致。
- [x] **B023 人工核查完成**。6 处“循环变量被闭包捕获”经核查**全部为误报**：闭包均在同一迭代内同步消费、不逃逸（orchestrator `remember_tool_result` 5 处 + comfyui `internal_link` 1 处）。已在源码逐行 `# noqa: B023` 并注明原因；**B023 保持强制启用**，未来其它闭包命中会被拦下。
- [x] **清零非格式类 ruff 债务**。已处理 `SIM105`、`B905`、`B007`、`SIM117`、`B008`、`B904`、`PIE810`、`SIM102`、`SIM103`、`SIM108`、`SIM118`、`UP022`；配置中只剩 `E501`。
- [ ] **收敛 E501 行长**。当前 67 处，等待一次独立、可审阅的格式化改动。
- [x] **清零 mypy 存量并转硬门禁**。新增明确包边界，修复 Optional、平台 API、第三方返回值和容器类型；48 个源码文件 0 issue，并启用 `check_untyped_defs` 与 `warn_return_any`。
- [ ] **补全所有函数签名**。完成后启用 `disallow_untyped_defs`，进一步收紧类型边界。
