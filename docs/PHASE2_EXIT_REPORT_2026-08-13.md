# 第二阶段退出报告（产品体验 MVP）

**日期**：2026-08-13  
**结论**：**第二阶段 MVP 关闭**；第三阶段（平台化 / WP-8）已启动。  
**公共 1.0**：仍为 **No-Go**（与体验退出解耦，见 WP-0 / `PUBLIC_RC_CHECKLIST.md`）。

## 退出条件对照

| # | 条件 | 状态 | 证据（摘要） |
|---|---|---|---|
| 1 | 新用户可在应用内完成「选模型 → 存密钥 → 验证 → 发消息」而不手改配置文件 | **满足** | `FirstRunWizard`：Kimi / DeepSeek / 自定义 / 本机 OpenAI 兼容 / llama.cpp 托管；保存密钥 → `/models/verify`（本地再走 `/start`→`/verify`）→ 进入工作台；localStorage 可跳过 |
| 2 | 空状态有可执行示例与清晰主导航（对话 / 运行 / 文件 / 设置渐进披露） | **满足** | `ChatEmptyState` 可执行示例；`ActivityRail` 主面板 + 高级「更多/收起」；Batch 28–32 e2e/axe 基线 |
| 3 | 本地模型至少支持「选择已有 runtime → 启动 → 验证 → 聊天」一条路径 | **满足** | `LocalModelQuickPath` + `startAndVerifyLocalModel`；向导 llama 预设；`LocalModelRuntimeError` 结构化诊断 |
| 4 | 无障碍与缩放回归保持 e2e 基线 | **满足** | Batch 32：Playwright e2e 基线 + axe 主路径/向导无 serious/critical；`docs/ACCESSIBILITY_CHECKLIST.md` |
| 5 | WP-0 公共 RC 单独跟踪，不阻塞体验批次合并 | **满足** | 体验代码已合入 `main`（至 Batch 42 提交 `2d55ff6`）；RC 预检 `tag_ready=false`，阻塞于 Windows CSC |

## 第二阶段交付范围（Batch 27–42）

- **WP-6**：首次向导、空状态、主导航渐进披露、e2e/a11y
- **WP-5**：本地模型快速路径、llama 托管、运行时诊断
- **WP-7**：Skills/Rules/`AGENTS.md`、发布/回滚、dry-run、interval/webhook 工作流
- **WP-4**：知识分层检索 UI、固定问答集 Recall@K 门禁
- **WP-0（并行）**：发布证据门禁、Ed25519 GitHub secrets、public RC 预检（Windows CSC 仍缺）

## 门禁快照（关闭时）

| 项 | 结果 |
|---|---|
| 平台治理单测（Batch 43 起） | 4 passed |
| 前端 unit | 126 passed |
| 前端 `tsc` + vite build | 通过 |
| local_dev_ready | True |
| public_1.0 / tag_ready | No-Go / false（Windows CSC 缺失） |

## 明确不在第二阶段退出范围内

- 公共签名 RC 与 Windows 代码签名（WP-0 持续）
- SSO/OIDC/SCIM、多租户生产隔离（第三阶段）
- 完整可视化工作流画布（WP-7 深化）

## 移交第三阶段

见计划文档「阶段划分」与 **Batch 43**：WP-8 本地优先治理骨架（租户 / RBAC / 白名单 / 审计 + `/platform` API + 治理面板）。
