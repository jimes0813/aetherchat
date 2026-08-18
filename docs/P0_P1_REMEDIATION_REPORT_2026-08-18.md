# AetherChat 2026-08-18 修改建议执行报告

**日期**：2026-08-18  
**依据**：`/home/jimes0813/Desktop/AetherChat-修改建议-2026-08-18.md`  
**范围**：P0 收编与卫生、P1 架构文档/门禁、P2 可落地重构、P3 本地安全与文档对齐  
**结论**：P0 与 P1 已完成；P2 完成巨型模块拆分、写路径 `to_thread` 与陈旧依赖淘汰；P3 完成限流/未认证告警/文档终态对齐。公共 1.0 仍为 No-Go（Windows CSC 与三平台签名 CI 需要密钥，无法在本机伪造）。

## 修复结果

| 优先级 | 修复项 | 结果 |
|---|---|---|
| P0-1 | 提交 141 个未提交文件 | 拆为 7 个逻辑提交（voice / desktop+references / 前端抽离 / backend 接线 / workbench 集成 / tests / quality+docs） |
| P0-2 | mypy `voice.py` engine 类型 | `SpeakRequest.engine` 使用 `TtsEngine` Literal，synthesize 调用处类型安全 |
| P0-3 | 删除入库 `.bak` | 已 `git rm` `frontend/src/App.tsx.broken-20260707-2230.bak`，`.gitignore` 增加 `*.bak` |
| P0-4 | LICENSE | 根目录 MIT，`frontend/package.json` `license: MIT` |
| P1-1 | 死依赖/死服务 + README | 移除 sqlalchemy/asyncpg/alembic、空 `backend/alembic/`、compose 中的 postgres/minio；README 改为 SQLite 桌面架构 |
| P1-2 | pytest 运行路径 | 根 `pytest.ini` 增加 `pythonpath = .` |
| P1-3 | 报告-门禁对账 | `docs/evidence/quality_gate_claims.json` + `scripts/check_gate_claims.py`，接入 `quality_gate.sh` |
| P1-4 | 后端模块边界 | `scripts/check_backend_module_boundaries.sh`，默认 2000 行，`orchestrator/run.py` 暂 2100 |
| P2-1 | 拆分巨型模块 | `chat_database.py` → `services/db/*`；`orchestrator.py` → `services/orchestrator/*`；对外 API 保持不变 |
| P2-2 | SQLite 异步化 | WAL/`busy_timeout` 原本已开启；新增 `ChatDatabase.run_in_thread`，流式写消息走 `asyncio.to_thread` |
| P2-3 | 淘汰 python-jose / passlib | 代码零引用，已从 `requirements.txt` 删除 |
| P3-1 | Windows 签名 + 三平台 CI | **未做**。需要 `WINDOWS_CSC_LINK` / `WINDOWS_CSC_KEY_PASSWORD`；见 `docs/PUBLIC_RC_CHECKLIST.md` |
| P3-2 | 限流/认证逃生口 | Redis 滑动窗口优先，`/voice/` 整段豁免取消；未认证启动打 `WARNING`；compose 仅绑定 `127.0.0.1` |
| P3-3 | 文档终态 | README 去掉 PR0–PR13 web 原型叙事；Claude 对标与 Linux 移植报告标注历史 |

## 自动化验收

| 门禁 | 结果 |
|---|---|
| Mypy | 97 个源码文件，0 issue |
| Electron | 24/24 passed |
| 后端 pytest | 465 passed（仓库根与 `cd backend && pytest ../tests` 结果一致） |
| Ruff | 全量通过 |
| 后端模块边界 | 全部服务模块通过（`orchestrator/run.py` 2078/2100） |
| docker compose | `docker compose config` 解析成功，仅 Redis + 可选 backend |

claims 快照见 `docs/evidence/quality_gate_claims.json`。故意改坏该快照中的数字会使 `scripts/check_gate_claims.py` 失败。

## 拆分后的关键路径

```
backend/app/services/chat_database.py   # 门面：ChatDatabase + 常量再导出
backend/app/services/db/
  connection.py / schema.py / sessions.py / messages.py
  tasks.py / agent_runs.py / event_journal.py / workspaces.py / automation.py
backend/app/services/orchestrator/
  __init__.py / run.py / plan.py / tools.py / context.py / receipts.py
```

## 仍开放的 1.0 阻塞项

- GitHub secrets 缺少 Windows CSC，无法用同一 tag 跑通 linux-x64 / linux-arm64 / windows-x64 签名构建
- 未发布 `update-manifest.signed.json` 的生产升级路径证据
- Windows 安装/覆盖升级/卸载烟测仍依赖 Windows runner

*生成时间：2026-08-18*
