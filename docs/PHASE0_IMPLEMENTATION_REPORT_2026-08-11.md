# AetherChat Phase 0 实施与验收记录（2026-08-11）

**实施基线**：`de2687468e7f2969bcd9b3a4392a19dfd84608bf` 之上的本地工作区改动  
**对应计划**：`AetherChat_生产级改进方案与计划_审校优化版_2026-08-10.md`  
**当前结论**：代码级 P0 基础设施已完成并通过本地门禁；公共 1.0 / RC 仍为 **No-Go**，因为远端三平台连续三次全绿与正式签名 RC 尚无证据。

## 1. 状态总览

| 门禁 | 本次结果 | 关闭状态 |
|---|---|---|
| REL-1 Windows 当前 HEAD CI | 已定位为 Windows 默认 CP1252 读取 UTF-8 中文脚本；测试现已显式使用 UTF-8，并在 ASCII 默认编码环境通过 | **待远端验证**：改动未推送，尚无同一提交三次 matrix 全绿及 Windows 安装烟测 |
| CONTRACT-1 执行策略契约 | 新增严格 `/ws/v1` 项目契约；旧入口保留 direct 兼容并持久化 actual/source/弃用事件；UI 永久显示实际策略 | **本地关闭** |
| TRUST-1 可信执行状态 | 每个副作用工具调用持久化枚举状态、证据、时间；Run 聚合可信状态；未核验/不确定不显示绿态 | **机制完成，基准待补**：尚未执行计划要求的 100 条执行声明回归集 |
| DATA-1 数据迁移与恢复 | SQLite `user_version` 顺序迁移、升级前在线备份、事务回滚、失败恢复、`quick_check`；真实用户库克隆演练与本机覆盖升级均通过 | **本地关闭** |
| Error Boundary + request_id | HTTP/WS request_id、错误响应诊断码、全局和面板错误边界、可复制诊断码 | **本地关闭** |
| 正式签名 RC | tag 构建新增签名 secret 前置门禁；现有 Authenticode 与 Ed25519 校验链保留 | **阻断**：仓库当前没有配置任何 Actions secret，无法生成正式签名 RC |

## 2. 已实施改动

### 2.1 版本化执行契约

- 新增 `/ws/v1/sessions/{session_id}`；前端默认连接 v1。
- v1 中 `mode=project` 必须显式提供 `execution_strategy=direct|planned`，否则返回带 `request_id` 的 `invalid_action`。
- 旧 `/ws/sessions/{session_id}` 保持兼容：缺字段时仍为 direct，不静默改成高成本 planned。
- 服务端统一派生并持久化：
  - `requested_execution_strategy`
  - `effective_execution_strategy`
  - `execution_strategy_source=explicit|compatibility_default`
- 兼容缺省会写入 `legacy_contract_deprecated` 事件。
- 外部客户端提交的伪造 effective/source 字段会被丢弃，实际策略只由服务端计算。
- retry 会恢复原始策略及其来源，不从 `mode` 推断。

### 2.2 副作用证据账本

`agent_tool_calls` 新增：

- `verification_status=pending|verified|unverified|failed|uncertain|not_applicable`
- `evidence_json`
- `verified_at`

当前可识别的结构化证据包括工具结果摘要、工件版本、文件 SHA-256 和桌面执行 receipt。仅“工具返回 success”但缺少可验证 receipt 的副作用会记录为 `unverified`，不会变成绿色成功。应用中断时，未确认副作用自动转为 `uncertain` 并继续阻止自动重放。

Run 列表聚合显示副作用总数、已核验数和整体状态；任一 uncertain、failed 或 unverified 都不会显示“全部已核验”。模型自然语言声明不参与可信状态计算。

### 2.3 SQLite 迁移与恢复

- 当前 schema 版本：`2`。
- `v1`：收拢历史内联补列逻辑。
- `v2`：加入副作用核验字段并迁移已有调用状态。
- 每次从旧版本升级前使用 SQLite Online Backup API 生成一致性备份。
- 每个版本使用 `BEGIN IMMEDIATE` 获取迁移写锁并独立提交；单步失败回滚。
- 初始化失败时使用升级前备份恢复原数据库。
- 高于应用支持版本的数据库拒绝启动，防止旧客户端降级写坏新 schema。
- 迁移完成运行 `PRAGMA quick_check`。

真实数据演练使用正在使用的数据库只读在线快照，所有写入仅发生在 `/tmp` 克隆中，演练后已清理：

| 指标 | v0 升级前 | v2 升级后 |
|---|---:|---:|
| 数据库大小 | 12,800,000 bytes | 克隆演练 |
| sessions | 16 | 16 |
| messages | 270 | 270 |
| tasks | 22 | 22 |
| agent_runs | 113 | 113 |
| agent_tool_calls | 132 | 132 |
| background_jobs | 0 | 0 |
| quick_check | ok | ok |
| 外键违规 | 0 | 0 |

升级前备份的逻辑快照与迁移前克隆完全一致。克隆演练阶段源数据库保持
`user_version=0`；随后在本机覆盖安装验收中，先额外创建只读一致性手工备份，
再由已安装后端对真实库执行 `v0 -> v2`。真实库迁移后仍为 16 个会话、270 条
消息、22 个任务、113 次 Agent 运行和 132 条工具调用，`quick_check=ok`、外键
违规为 0。自动迁移备份与手工备份均再次只读校验通过，并已收紧为 `0600`。

### 2.4 故障隔离与诊断链

- 所有 HTTP 请求接受或生成合法 `X-Request-ID`，响应头回传该值。
- HTTP 4xx/5xx JSON 错误体携带 `request_id`；未处理异常只返回通用 500 文案，不回显内部异常。
- WebSocket 客户端为每个动作生成 `request_id`；协议错误回传相同诊断码。
- 新增全局 Error Boundary 与主工作区/检查器面板 Error Boundary。
- 局部面板渲染失败时保留其余界面，并显示可复制的 `ui-*` 诊断码、局部重试和整页重载入口。

### 2.5 Windows 与发布门禁

远端失败运行：<https://github.com/jimes0813/aetherchat/actions/runs/30747239202>。

Windows job 的实际失败为：

```text
tests/test_release_security.py::test_linux_desktop_entry_and_icon_match_electron_package
UnicodeDecodeError: 'charmap' codec can't decode byte 0x8d
```

根因是 `Path.read_text()` 在 Windows Server 2025 使用 CP1252，而 `frontend/build-deb.sh` 含 UTF-8 中文。所有相关发布测试读写现已显式固定 `encoding="utf-8"`。该测试在 `LC_ALL=C`、`PYTHONUTF8=0`、`-X utf8=0` 下仍通过。

正式 tag 新增 `release-preflight`：若缺少下列任一 secret，三平台构建前即明确失败：

- `WINDOWS_CSC_LINK`
- `WINDOWS_CSC_KEY_PASSWORD`
- `AETHERCHAT_RELEASE_PRIVATE_KEY_B64`
- `AETHERCHAT_RELEASE_PUBLIC_KEY_B64`

2026-08-11 只读查询 `gh secret list` 返回空列表，因此本次不能伪造“正式签名 RC 已完成”的结论。

## 3. 本地验收证据

| 门禁 | 结果 |
|---|---|
| 后端完整 pytest | **126 passed, 1 skipped**；15.80 秒（最终复跑） |
| Ruff | 修改涉及的 `backend/app`、`test_basic.py`、`test_release_security.py` 全通过 |
| Mypy | **52 个源码文件，0 issue** |
| 前端生产构建 | **1514 modules**；JS 380.00 KB，gzip 114.49 KB |
| Electron 测试 | **13 / 13 passed** |
| Playwright E2E | **44 / 44 passed**；含策略/证据展示、WS request_id、面板错误隔离 |
| 发布安全测试（强制非 UTF-8 默认环境） | **4 / 4 passed** |
| Git diff whitespace | `git diff --check` 通过 |
| Linux ARM64 本地打包 | AppImage 与 Debian 包成功；`.deb` SHA-256：`a2a13f8077f0417c5309198f4a121c4d3e241af987a0af74d174e57bcf8bac9c` |
| Debian 覆盖安装 | `aetherchat 0.2.0 arm64 install ok installed`；`dpkg --verify` 无差异；安装后 `app.asar` 与后端二进制哈希和构建产物一致 |
| 包内后端隔离冒烟 | 全新库初始化为 schema v2；HTTP `X-Request-ID` 回传；`/ws/v1` 正确拒绝缺失 project 执行策略的请求 |
| 真实数据库覆盖升级 | v0 -> v2；16 sessions / 270 messages / 22 tasks / 113 runs / 132 tool calls 全部保留；两份 v0 备份均 `quick_check=ok` |
| 已安装 Electron UI | 直接连接安装后的 `file:///opt/AetherChat/resources/app.asar/dist/index.html`；基础检查全部正常；19 个工具、11 个启用插件；控制台 0 error / 0 warning；所有观测 HTTP 请求均为 200 |
| 已安装 WebSocket | Playwright 观测实际连接为 `/ws/v1/sessions/{id}`；底部状态为 connected |
| 最终普通运行状态 | `aetherchat-desktop.service` active；后端 `127.0.0.1:8000` 健康；调试端口 9222 关闭；ComfyUI 服务保持 active |

唯一测试告警为 FastAPI TestClient 的 `httpx2` 迁移提示，不影响本次断言；应在后续依赖升级批次处理。

## 4. 尚未关闭的风险与下一步

1. **不得发布 1.0/RC**：本地改动尚未 commit/push，远端尚未验证。
2. 在同一候选提交上运行 `build-test-release` 至少三次，保存三次 Linux x64、Linux ARM64、Windows x64 全绿 URL、测试摘要与产物 SHA-256。
3. 在受保护的 GitHub Environment 配置四项签名 secret，随后创建 RC tag；不得把私钥写入仓库、日志或普通 CI artifact。
4. 对签名 RC 执行 Windows 安装/覆盖升级/卸载与数据保留，以及 Ed25519 篡改拒绝、降级拒绝、断网恢复测试。
5. 建立 100 条执行声明回归集；错误绿色成功必须为 0，才能把 TRUST-1 从“机制完成”改为“门禁关闭”。
6. 后续 schema 再升级前，仍应确认数据目录有足够空间容纳数据库快照；当前实现会在备份创建失败时停止升级并保持源库不变。
7. 运行检查面板的 WebSocket 说明文字仍显示旧路径 `/ws/sessions/:id`，但实际连接已由 Playwright 证明为 `/ws/v1/sessions/{id}`；这是低风险展示偏差，应在下一批 UI 文案修正。

在上述远端与签名证据齐全前，产品状态仍是“内部 Beta 可用，公共生产发布 No-Go”。
