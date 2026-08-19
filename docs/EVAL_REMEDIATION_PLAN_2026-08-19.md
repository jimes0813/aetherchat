# AetherChat 测评改进任务计划

**依据**：`Desktop/AetherChat_专业测评报告_2026-08-19.md`（综合 7.4 / 10）  
**日期**：2026-08-19  
**原则**：先卫生与门禁，再体验，再 1.0 证据链。不加新功能。

## 本轮执行范围

本轮完成 **P0 全部**，并留下 P1/P2 清单供后续批次。

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

## P1 — 一个月（下一批）

| ID | 事项 |
|---|---|
| P1-1 | 流式气泡：streams 离开全量 zustand，selector + memo |
| P1-2 | 消息列表虚拟化；历史默认 100 条 |
| P1-3 | 剩余 SQLite 写走 `to_thread`；拆 `stream_user_message` |
| P1-4 | 设置页收成 4 组 |
| P1-5 | 自绘对话框替换 `window.confirm`；i18n 稳定 key |
| P1-6 | 对话授权不得替代原生 Computer Use broker |
| P1-7 | Bearer 留在主进程 |
| P1-8 | 生产关 `/docs`；`/health` 精简 |
| P1-9 | 拆 `test_basic.py` |

## P2 — 一季度（1.0 资格）

Windows CSC、三平台同一 tag、已发布 `update-manifest.signed.json`、Windows 安装烟测、用户安装降级模式明示。

## 版本建议

继续称 **0.2.0 本地 RC**。P0 合入后不打 1.0。
