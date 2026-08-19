# AetherChat 测评改进任务计划

**依据**：`Desktop/AetherChat_专业测评报告_2026-08-19.md`（综合 7.4 / 10）  
**日期**：2026-08-19  
**原则**：先卫生与门禁，再体验，再 1.0 证据链。不加新功能。

## 本轮执行范围

P0–P7 已合入。本轮继续拆执行环：`run_provider` 与工具调度离开 `stream_exec`。Windows CSC 仍阻塞。

## P0 — 一周内（本轮落地）

| ID | 事项 | 状态 | 验收 |
|---|---|---|---|
| P0-1 | 首次向导去掉本机绝对路径与 Uncensored 默认模型 | **完成** | 源码与测试不含开发机路径、不含 Uncensored |
| P0-2 | 侧栏/历史默认隐藏诊断会话 | **完成** | 标题匹配冒烟/schema/DAG/Error: 默认不显示，可展开 |
| P0-3 | 生成物卡片：人类文件名；元数据 JSON 不进主卡片；不展示内部绝对路径 | **完成** | `.metadata.json` 收到技术细节；主文案为文件名 +「已保存到本机」 |
| P0-4 | 「预算 $10」改为会话额度；关于页写清版本/通道/Chromium 沙箱 | **完成** | 顶栏「会话额度」；帮助→关于含沙箱状态；状态栏降级提示 |
| P0-5 | 删除死文件 `frontend/App.tsx`；App.tsx <800；DesktopControl 拆出文档面板并重开书面预算 | **完成** | App 798/800；DesktopControl 600/620 |
| P0-6 | 收编未提交的 `backend/aetherchat-backend.spec` | **完成** | 纳入本轮提交 |
| P0-7 | compose 去掉 docker.sock；demo auth 默认关 | **完成** | 默认 `AETHERCHAT_ENABLE_DEMO_AUTH=0`，无 docker.sock |
| P0-8 | ComfyUI 工作流根目录不再写死开发机路径 | **完成** | 仅 env + `Path.home()` 探测 |

**不做本轮**：强推 origin 的 12 个历史提交（需维护者确认远程策略）。本轮改动单独提交。

## P1 — 一个月（本轮落地）

| ID | 事项 | 状态 |
|---|---|---|
| P1-1 | 流式气泡：streams 离开全量 zustand，selector + memo | **完成** |
| P1-2 | 消息列表虚拟化（content-visibility + 只渲染最近 80 条）；历史默认 100 条 | **完成** |
| P1-3 | steer 写走 `to_thread`；消息列表异步化；插件调用从 run 抽出 | **完成**（`stream_user_message` 约 120 行；`stream_exec.py` 166 行；`stream_provider.py` 仍约 722） |
| P1-4 | 设置页收成 4 组 | **完成** |
| P1-5 | 自绘对话框替换 `window.confirm` | **完成** |
| P1-6 | 对话授权不得替代高风险原生 Computer Use | **完成** |
| P1-7 | HTTP Bearer 由 Electron 主进程注入；渲染进程不再缓存真实 token | **完成**（WS 按 getter 取 token，构造后立即清空局部变量；桌面仍为 `intercept`） |
| P1-8 | 生产关 `/docs`；`/health` 精简 | **完成** |
| P1-9 | 拆 `test_basic.py`（fixtures 入 conftest，插件/Comfy/桌面测抽出） | **完成**（续拆 automation / plan / tool_loop；各文件均 <1500） |

## P2 — 一季度（1.0 资格）

| ID | 事项 | 状态 |
|---|---|---|
| P2-1 | Windows CSC 配置到 GitHub Secrets | **阻塞**（本机无 Authenticode 证书；CI 在 secret 存在时才会强制签名） |
| P2-2 | 三平台同一 `v*` tag | **部分完成**（`v0.2.0-rc.7` Linux x64+arm64；Windows 仍缺 CSC） |
| P2-3 | 已发布 `update-manifest.signed.json` | **完成（Linux RC）**（prerelease，不走 `/releases/latest`） |
| P2-4 | Windows 安装/升级/卸载烟测记录 Authenticode | **完成**（无 CSC 时记录状态；`REQUIRE_CODE_SIGNING=1` 时失败） |
| P2-5 | 用户安装降级模式明示 | **完成**（通道探测、首次警告、工作台横幅、关于页、用户启动器注释） |

## P3 — 测评打磨（本轮落地）

| ID | 事项 | 状态 |
|---|---|---|
| P3-1 | 上翻历史时新消息不强制滚到底 | **完成** |
| P3-2 | 首次向导、语音浮层焦点陷阱 | **完成** |

## P4 — 计划 P2 余项（本轮落地）

| ID | 事项 | 状态 |
|---|---|---|
| P4-1 | 前端 manualChunks + `sourcemap: hidden` | **完成** |
| P4-2 | 治理页标为高级实验，不作为默认设置入口 | **完成** |
| P4-3 | 收束顶栏：去掉模式回显与重复「新建默认」 | **完成** |
| P4-4 | GTK「运行」菜单并入帮助，去掉与顶栏重复的打开工作台 | **完成** |

## P5 — P1 余项收口（本轮落地）

| ID | 事项 | 状态 |
|---|---|---|
| P5-1 | 再拆 `test_basic.py` 为 ws / orchestrator / agent | **完成** |
| P5-2 | `stream_user_message` 执行环抽到 `orchestrator/stream_exec.py` | **完成** |

## P6 — P1 余项收口（本轮落地）

| ID | 事项 | 状态 |
|---|---|---|
| P6-1 | WebSocket 连接 token 改为 getter，握手后清空局部 auth | **完成** |
| P6-2 | 计划批处理从 `stream_exec.py` 抽到 `orchestrator/stream_plan.py` | **完成**（`stream_plan.py` 仍约 563 行） |

## P7 — 测试文件行数收口（本轮落地）

| ID | 事项 | 状态 |
|---|---|---|
| P7-1 | 自动化测抽到 `tests/test_basic_automation.py` | **完成**（`test_basic.py` 约 1434 行） |
| P7-2 | 计划 DAG 抽到 `tests/test_basic_plan.py`，工具环抽到 `tests/test_basic_tool_loop.py` | **完成**（orchestrator 约 913，plan 约 947，tool_loop 约 856） |

## P8 — 执行环再拆（本轮落地）

| ID | 事项 | 状态 |
|---|---|---|
| P8-1 | `run_provider` 抽到 `orchestrator/stream_provider.py` | **完成**（`stream_exec.py` 166 行） |
| P8-2 | 工具调度抽到 `stream_tools.py`，授权后调用抽到 `stream_invoke.py` | **完成**（tools 354，invoke 230；`stream_provider.py` 仍约 722，未到 ≤400） |

## 版本建议

继续称 **0.2.0 本地 RC**。P0 合入后不打 1.0。
