# AetherChat P0→P1 修复与验收报告

**日期**：2026-08-13  
**范围**：桌面入口、完成语义、API/E2E 契约、质量门禁、KPI 证据、工件恢复、i18n/无障碍、前端模块边界  
**结论**：本轮 P0、P1 修复和本机安装验收完成；本机 `local_dev` 发布证据 11/11 通过。公共 1.0 仍为 No-Go，未用本机或离线代理证据替代 Windows 签名、三平台候选构建和真实运营样本。

## 修复结果

| 优先级 | 修复项 | 结果 |
|---|---|---|
| P0 | 完成语义 | 用 `completed_verified`、`completed_with_warnings`、`uncertain` 区分已核验、带告警和证据不足；聚合计数与前端展示同步，禁止把未知副作用显示为绿色成功 |
| P0 | API/E2E 契约 | 对数组响应进行显式归一化，补齐 `ErrorBoundary` 和真实 WebSocket readiness mock；失败响应不再把错误对象当数组渲染 |
| P0 | 桌面安全入口 | Deb 提供 `/usr/bin/aetherchat`；启动前强制校验 `/opt/AetherChat/chrome-sandbox` 为 root:root 4755；任何分支都不追加 `--no-sandbox`；桌面、菜单和 `aetherchat://` 协议统一走安全入口 |
| P0 | 质量与差异门禁 | 新增统一 `quality_gate.sh`、diff hygiene、i18n 与模块边界门禁，并接入 CI；Ruff、Mypy、后端、Electron、Vitest、构建和 Playwright 统一执行 |
| P0 | KPI 证据 | 新增机器可读 KPI 快照；100 条可信声明、60 次确定性代理、RAG、真实 Run telemetry 与发布门禁分开记录，样本不足保持 `insufficient_evidence` |
| P1 | 旧工件恢复 | 当前选择指向已删除工件时自动清除；历史缺失文本工件可预览并一键恢复；二进制或证据不足的工件不伪装为可恢复 |
| P1 | i18n 与无障碍 | 生产 TSX 中文裸文本/属性/表达式与缺失英文映射进入静态门禁；设置页和主工作区完成中英双语；960 等效宽度、200% 缩放、强制颜色、高对比度焦点和 axe 回归通过 |
| P1 | 模块边界 | 抽离 `DesktopControlPanel`（227 行）与 `PluginCenter`（468 行）；`InspectorPanel` 由 2,308 降至 1,619 行；`App.tsx` 785 行；以 CI 行数预算防止回涨 |

## 自动化验收

| 门禁 | 结果 |
|---|---|
| 后端 pytest | 432 passed，1 skipped；1 个既有 `httpx2` 迁移告警 |
| Ruff | 全量通过 |
| Mypy | 76 个源码文件，0 issue |
| Electron | 22/22 passed |
| 前端单元/组件 | 158/158 passed |
| Playwright | 57/57 passed |
| 前端生产构建 | 1545 modules；JS 502.90 KB，gzip 153.36 KB；成功，保留 >500 KB 分包告警 |
| npm audit | 0 vulnerability |
| 发布证据 | `local_dev` 11/11 pass；`public_1.0` No-Go |

Playwright 覆盖 720×450 CSS viewport（1440×900、deviceScaleFactor=2 的 200% 等效场景）、设置页无横向溢出、强制颜色模式、键盘焦点、选中态和 axe serious/critical 检查。Orca/NVDA 人工读屏签收仍是后续运营验收，不被自动化结果替代。

## ARM64 成品与安装终验

| 工件 | 大小 | SHA-256 |
|---|---:|---|
| `AetherChat-0.2.0-linux-arm64.AppImage` | 204,396,343 bytes | `10de4d6b2a97215b8f889f1bb480df5b009307f30130833b574505648da11c6b` |
| `AetherChat-0.2.0-linux-arm64.deb` | 160,053,198 bytes | `077b850267a6d3f315fa7c6c527fb23c7b9870737540a4435e2b64b8a968577f` |
| 安装版 `app.asar` | 与本次 release 构建一致 | `92e2d2474aa1cc90736351a6ad7c9a293f102a7e96b129e72939ad5f9b96df4f` |

安装终验结果：

- `/opt/AetherChat/chrome-sandbox` 为 `root:root 4755`；安全启动器和主程序为 `root:root 755`；
- 主进程、打包后端和 Renderer 均来自 `/opt/AetherChat`；未发现精确参数 `--no-sandbox`，Renderer 明确包含 `--enable-sandbox`；
- `/health/ready` 返回 200、`ready=true`、数据库 schema 4；
- `aetherchat://chat?prompt=...` 由既有实例接收，验证协议入口和 single-instance 链路；
- 安装前在线备份为 `aetherchat-pre-batch51-install-20260813T1320.sqlite3`，SHA-256 `22fb679753fed7877650d8d419930d41888136b1492ac67d5cca879d6824689d`，完整性 `ok`；
- 覆盖安装后主库 `quick_check=ok`，17 sessions、352 messages、22 tasks、152 agent runs 保留。

## KPI 与发布判定

- 通过：100 条版本化执行声明错误绿色成功数 0；离线 RAG Hit@3=1.0、MRR=1.0。
- 证据不足：真实 RC 受控任务闭环率、新用户首次任务 p90、恢复成功率、严重问题诊断定位率。自动 E2E 与 60 次确定性组件代理不等同于这些运营 KPI。
- 公共 1.0 No-Go：Windows CSC、同一候选三平台签名 CI、已发布签名更新清单、Windows 安装/覆盖升级/卸载证据仍未齐全。本机 `/opt` 已终验，但公共证据账本在接入可访问的终验工件前继续保守列为缺项。

机器可读证据位于 `docs/evidence/p0-p1-2026-08-13/`，其中 `SHA256SUMS.json` 固定各 JSON 快照哈希。
