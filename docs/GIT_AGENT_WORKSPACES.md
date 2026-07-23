# Git Agent 工作区

更新日期：2026-07-19  
实现阶段：Agent Run 2.3

## 目标

Git Agent 工作区让项目模式中的代码 Worker 能修改真实仓库，同时把模型执行、主工作树写入和 Git 发布动作分开：

1. 用户在 Electron 桌面端选择一个干净的 Git 仓库；
2. Planner 冻结仓库 HEAD、分支和最多四条核验命令；
3. 每个 Worker/attempt 从冻结提交创建独立 detached worktree；
4. Worker 只通过服务端绑定的工作区工具读写和运行命令；
5. 服务端采集真实 diff，并在断网 Docker 沙箱执行核验命令；
6. 用户审阅证据后，通过 Electron 原生确认应用或丢弃候选；
7. apply 只把补丁写到主工作树，后续 commit、branch、push 和 PR 由用户自行处理。

浏览器预览不能登记本机仓库，也不能应用或丢弃本机候选。未给 Run 绑定仓库时，Worker/Verifier 保持原有只读策略。

## 信任边界

| 边界 | 当前保证 |
| --- | --- |
| 仓库选择 | 绝对路径只能由受信 Electron 主进程的系统目录选择器提交；登记和决策同时要求常规 API Bearer 与临时 Desktop capability token |
| Renderer / 模型 | 公开对象只含仓库显示名、分支和提交标识；不返回根路径。模型只得到服务端绑定的不透明 workspace ID |
| Git 仓库 | 仅接受非裸、有有效 HEAD、无暂存/未提交/未跟踪改动的工作树；拒绝本地 `clean`、`smudge`、`process`、`required` 自定义过滤器 |
| Git 进程 | 使用参数数组而非主机 shell，并禁用 hooks、fsmonitor、系统/全局 Git 配置、终端提示和外部 diff |
| Worker 文件访问 | 相对 POSIX 路径限定在所属 worktree；拒绝路径穿越、反斜杠、`.git`、符号链接逃逸和非普通文件 |
| Worker 命令 | 命令只在 Docker 容器内执行；仅挂载对应 worktree，默认 `network=none`，容器根文件系统只读，丢弃全部 capabilities，并启用资源限制和 `no-new-privileges` |
| 候选补丁 | 从冻结提交生成 binary/full-index patch；最多 200 个变更文件和 2 MiB，拒绝符号链接与子模块 |
| 主工作树写入 | 只能由 Electron 原生确认触发；服务端在同一锁内重查 session、Run 终态、补丁哈希、HEAD 和干净状态，再依次执行 `git apply --check` 与 `git apply` |

仓库根路径会保存在本机 SQLite 中，供后端创建和清理 worktree 使用，但不会通过仓库、Run 或候选的公开 API 返回。

## Worker 工具

绑定仓库的 Worker 只会获得以下工作区工具：

- `workspace_list_files`
- `workspace_read_file`
- `workspace_write_file`
- `workspace_delete_file`
- `workspace_run_command`
- `workspace_status`

工具调用的 `run_id`、`plan_step_id` 和 `workspace_id` 来自编排器的服务端执行上下文，不接受模型自行替换。没有匹配上下文时调用默认失败。

单个读写文件上限为 2 MiB，只支持 UTF-8 普通文本。文件列举最多返回 2,000 项。Worker 交互命令的超时范围为 1–300 秒；命令文本会由容器内的 `bash -lc` 解释，但不会拼接到主机命令行。

## 核验语义

仓库登记时会保守推断 `pytest`、npm test/lint、Cargo 或 Go 命令；用户可在计划确认前修改，Run 最多冻结四条。核验在 Worker 结束后执行，每条上限 120 秒，输出最多持久化 20,000 字符并另存 SHA-256。

| 状态 | 含义 |
| --- | --- |
| `passed` | 候选有非空 diff，且至少配置一条核验命令，全部退出码为 0 |
| `failed` | 候选生成失败，或至少一条核验命令退出码非 0 |
| `unverified` | 候选有非空 diff，但没有配置核验命令 |
| `no_changes` | Worker 没有生成可应用的 diff |

模型 Verifier 会看到结构化的文件数、diff 哈希、命令、退出码和输出哈希。如果所有候选均为 `failed`/`no_changes`，模型声称的 `pass` 会被确定性规则覆盖为 `fail`；只有未核验候选时，模型 `pass` 会降为 `unknown`。用户仍可明确选择应用一个有 diff 的失败或未核验候选，原生确认框会显示其核验状态。

核验命令可能修改 worktree（例如格式化器）；最终补丁在核验命令之后采集，因此会包含这些修改。

## 安全 apply 与清理

候选只有在所属 Run 进入 `completed`、`failed` 或 `interrupted` 后才能应用。apply 的原子检查范围包含：

- 候选仍为 `pending`，防止重复应用；
- 候选属于当前 session 和已登记仓库；
- 持久化补丁的 SHA-256 与实际 BLOB 一致；
- 主仓库 HEAD 仍等于计划冻结提交；
- 主仓库仍无暂存、未提交或未跟踪改动；
- `git apply --check --binary` 成功。

通过后只执行 `git apply --binary`。AetherChat 不会自动 commit、创建/切换 branch、push、fetch、打开 PR 或访问网络。成功应用或用户丢弃后，只清理 AetherChat 自己的数据目录下对应 worktree；等待用户决定的候选会保留，以支持重启后继续审阅。

apply 后主工作树按设计变为有未提交改动。若用户在计划确认后修改主仓库、移动 HEAD，或补丁无法干净应用，系统会拒绝覆盖并要求重新运行或人工合并。

## 运行前提

- 主机已安装 Git；
- Docker daemon 可用；
- 已构建或安装 `AETHERCHAT_SANDBOX_IMAGE` 指定的镜像，默认是 `aether-sandbox:latest`；
- 项目的测试依赖必须已经包含在镜像或 worktree 中。核验容器默认断网，不会现场下载依赖。

## 当前限制与下一步

- 尚未读取、合并或分层执行仓库中的 `AGENTS.md`、Rules 或 Skills；
- 不支持裸仓库、脏工作树、Git LFS、子模块、符号链接变更、超过 200 个文件或 2 MiB 的补丁；
- 不支持多仓库任务、依赖缓存、冲突可视化、交互式 patch 选择和候选合并；
- 不自动创建 commit/branch/PR，也没有远程仓库网络操作；
- 返工仍会重跑全部 Worker，不能根据 Verifier issue 精确选择补偿节点；
- 未完成后台 job daemon；运行仍依赖当前后端生命周期，等待决策的 worktree 可能持续占用磁盘；
- Docker 隔离降低了模型命令直接影响主机的范围，但不能替代主机 Docker daemon、本机用户权限和镜像供应链的安全维护。

下一阶段应优先补齐 `AGENTS.md`/Rules 的作用域与优先级、按 issue 精确返工、依赖缓存与核验模板、冲突审阅，以及显式 commit/branch/PR 流程。
