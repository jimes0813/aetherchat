# 第三阶段启动说明（WP-8 治理骨架）

**日期**：2026-08-13  
**批次**：Batch 43  
**状态**：骨架可用；非生产多租户

## 目标

在桌面本地优先模式下建立**可扩展**的租户 / 身份 / 策略 / 审计基础，后续按客户需求接入 SSO、团队空间与导出。

## 已交付

| 层 | 内容 |
|---|---|
| 服务 | `backend/app/services/platform_governance.py`：默认租户 `local`、owner 主体、RBAC、策略补丁、authorize、审计 JSONL |
| API | `GET/POST /platform/tenants`、`PATCH .../policy`、`GET/POST /platform/principals`、`GET /platform/roles`、`POST /platform/authorize`、`GET /platform/audit`、`GET /platform/status` |
| UI | 活动栏高级区「治理」→ `PlatformGovernancePanel`：策略、白名单强制切换、工具授权试算、主体与审计列表 |
| 测试 | `tests/test_platform_governance.py`（4） |

## 刻意不做（本批）

- 真实 OIDC/SAML/SCIM
- 跨设备同步与中心化 secret 托管
- ~~将 authorize 硬接入所有 tool_executor 路径~~ → **Batch 44 已接入**
- ~~审计导出与合规报表~~ → **Batch 44 已做 JSON/JSONL 导出**

## Batch 44（运行时门禁）

| 项 | 内容 |
|---|---|
| 开关 | `AETHERCHAT_PLATFORM_POLICY_GATE` 默认开启；设 `0`/`false` 跳过 |
| 工具路径 | `tool_registry.invoke_async`、plugin/MCP `invoke_tool`；编排器捕获 `PermissionError` |
| 模型路径 | `orchestrator` 在选中模型后 `filter_model_names` |
| 行为 | 仅在 RBAC 拒绝或白名单强制开启时拦截；默认策略不挡日常使用 |
| 审计 | 拒绝始终记入；运行时允许不记（避免刷屏）；API 试算仍记 allow |
| 导出 | `GET /platform/audit/export?format=json|jsonl`；治理面板「导出 JSON」 |

## Batch 45（项目作用域 + 审计保留）

| 项 | 内容 |
|---|---|
| 项目 | `projects` 表；默认项目 `default`；`POST/GET /platform/projects`；项目策略覆盖租户 |
| 生效策略 | `effective_policy(tenant, project)` 合并后用于 authorize |
| 审计 | `project_id` 字段；`format=csv` 导出；`POST /platform/audit/prune` |
| 保留 | `audit_retention_days`（默认 90）+ `audit_max_events`（默认 10000）；append 时 best-effort 裁剪 |
| schema | `schema_version=2`，旧 store 自动 migrate |
| 单测 | 12 passed |

## 下一步建议（Batch 46+）

1. 会话/Run 绑定 `project_id` 并传入 runtime gate
2. 客户触发时再做 SSO 与团队空间
3. Windows CSC 配齐后签公共 RC
