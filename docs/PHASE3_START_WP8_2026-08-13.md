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
- 将 authorize 硬接入所有 tool_executor 路径（后续可挂 policy gate）
- 审计导出与合规报表

## 下一步建议（Batch 44+）

1. 在工具调用与模型选择路径上可选接入 `authorize`（feature flag）
2. 审计导出（JSON/CSV）与保留策略
3. 项目/工作区作用域（tenant → project）
4. 客户触发时再做 SSO 与团队空间
