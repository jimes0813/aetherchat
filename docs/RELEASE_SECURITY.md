# AetherChat 发布安全与跨平台构建

## 支持目标

`.github/workflows/release.yml` 在干净的 GitHub 托管虚拟机上构建和测试：

- Linux x64：`ubuntu-24.04`
- Linux ARM64：`ubuntu-24.04-arm`
- Windows x64：`windows-2025`

每个平台执行后端单测、Electron 更新拒绝路径、前端生产构建和 Playwright E2E。Linux 生成 AppImage 与 Deb；Windows 生成 NSIS，并执行静默安装、同版本覆盖升级、卸载及用户数据保留烟测。

## CI Secrets

正式 `v*` tag 发布前必须配置：

- `AETHERCHAT_RELEASE_PRIVATE_KEY_B64`：Ed25519 PKCS#8 PEM 私钥的 Base64，仅用于签名更新清单。
- `AETHERCHAT_RELEASE_PUBLIC_KEY_B64`：对应 SubjectPublicKeyInfo PEM 公钥的 Base64，会嵌入正式客户端并用于发布后复验。
- `WINDOWS_CSC_LINK`：electron-builder 支持的 Windows 代码签名证书引用或 Base64 证书。
- `WINDOWS_CSC_KEY_PASSWORD`：证书密码。

私钥和证书不得写入仓库、构建产物或日志。tag 构建缺少 Windows 证书、公钥或清单私钥时会失败；普通 PR 构建可以保持无签名，但客户端更新功能因没有嵌入公钥而禁用。

### 生成本地 Ed25519 密钥对

```bash
# 打印 Base64（勿提交输出）
python scripts/release_security.py generate-keys

# 或写入私有目录（私钥文件权限 600）
python scripts/release_security.py generate-keys --output-dir ~/.aetherchat-release-keys
```

配置到 GitHub（示例）：

```bash
gh secret set AETHERCHAT_RELEASE_PRIVATE_KEY_B64 < ~/.aetherchat-release-keys/AETHERCHAT_RELEASE_PRIVATE_KEY_B64.txt
gh secret set AETHERCHAT_RELEASE_PUBLIC_KEY_B64 < ~/.aetherchat-release-keys/AETHERCHAT_RELEASE_PUBLIC_KEY_B64.txt
# Windows CSC 按证书形态设置 WINDOWS_CSC_LINK / WINDOWS_CSC_KEY_PASSWORD
```

### Public RC 预检

```bash
python scripts/release_evidence.py check --refresh-metadata
python scripts/public_rc_preflight.py
```

- `release_evidence.py`：本机 local_dev 门禁 + public_rc 缺项列表  
- `public_rc_preflight.py`：secrets 是否在本机环境/GitHub 配置、Ed25519 对是否匹配、操作步骤清单  
- 输出：`frontend/release/public-rc-preflight.json`、`docs/PUBLIC_RC_CHECKLIST.md`  

在四个 secrets 与三平台 CI 全绿、签名清单发布完成之前，`public_1.0` 保持 **No-Go**。

## 更新协议

正式客户端只读取：

`https://github.com/jimes0813/AetherChat/releases/latest/download/update-manifest.signed.json`

签名信封使用 `aetherchat-update-v1` 清单和 Ed25519。客户端依次验证：

1. 清单和软件包 URL 均为无内嵌凭据的 HTTPS，且禁止重定向。
2. Ed25519 签名正确。
3. 版本严格高于当前版本，拒绝降级或重复安装。
4. 平台和架构完全匹配。
5. 文件名安全，签名大小不超过 2 GB。
6. 流式下载的实际大小和 SHA-256 与签名清单一致。
7. Windows 安装器的系统 Authenticode 状态为 `Valid`，且签名者主题非空。

软件包先以权限 `0600` 写入随机 `.partial` 文件，校验成功后才原子重命名。任何错误都会删除临时文件，因此验证或下载失败不会改变当前安装、会话数据库、密钥引用和资料索引。

正式 Windows 安装版在启动 30 秒后自动执行第一次检查，此后每 6 小时检查。
发现并完整验证新版本后，客户端使用 electron-builder NSIS 更新参数
`--updated /S --force-run` 静默安装并重新启动。检查、下载或启动安装失败时，
当前版本继续运行并在下个周期重试；并发检查会合并为一次请求。开发模式、
非 Windows 平台、缺少嵌入公钥的构建默认禁用自动更新。紧急运维可设置
`AETHERCHAT_AUTO_UPDATE_DISABLED=1` 禁用，检查周期只能在 15 分钟到 7 天之间调整。
Windows RC 读取滚动的 `windows-ci-latest/update-manifest.signed.json`，稳定版读取
GitHub `releases/latest`；滚动清单只在安装器 Authenticode 验证成功后才由 CI
签名并发布，未签名测试包不会进入自动更新通道。

## SBOM 与构建元数据

每个平台生成 CycloneDX 1.5 JSON，包含 `package-lock.json` 的 npm 依赖和固定版本的 Python 依赖。构建元数据另外记录平台、架构、工具版本、产物大小和 SHA-256。

本机示例：

```bash
python scripts/release_security.py sbom \
  --npm-lock frontend/package-lock.json \
  --requirements backend/requirements.txt \
  --version 0.2.0 \
  --output frontend/release/AetherChat-0.2.0.cdx.json
```

## 本机安装版沙箱检查

Deb 构建脚本强制把 `chrome-sandbox` 记录为 `root:root`、模式 `4755`。安装后运行：

```bash
stat -c '%U:%G %a %n' /opt/AetherChat/chrome-sandbox
/opt/AetherChat/aetherchat-frontend
```

预期为 `root:root 4755`，启动命令不得包含 `--no-sandbox`。安装需要系统管理员权限；仅检查 Deb 归档可使用 `dpkg-deb --contents`，但不能代替实际安装启动终验。

2026-07-16 ARM64 本机终验已通过：安装文件为 `root:root 4755`，正式安装版未使用 `--no-sandbox` 即成功呈现窗口并启动打包后端；Renderer 进程显示 `NoNewPrivs: 1`、`Seccomp: 2`，`/sessions/`、`/models/`、`/tools/` 无 Bearer 令牌均返回 401。

同日重启检查发现用户侧曾再次运行带 `--no-sandbox` 的 AppImage；该进程树已终止并切换到 `/opt/AetherChat/aetherchat-frontend`。当前主进程与 Renderer 命令行均不含 `--no-sandbox`，Renderer 明确包含 `--enable-sandbox`，打包后端状态为 ready。

### F0 launch policy

Prefer the Debian install at `/opt/AetherChat` (`chrome-sandbox` root:root 4755). User AppImage/unpacked launchers must not inject `--no-sandbox` unless `AETHERCHAT_ALLOW_NO_SANDBOX=1` or an explicit `--no-sandbox` / `--aether-allow-no-sandbox` flag is provided; the launcher prints a stderr warning when degraded.

### F1 observability

- Crash dumps: Electron `crashReporter` (disable with `AETHERCHAT_CRASH_REPORTER=0`; optional upload via `AETHERCHAT_CRASH_SUBMIT_URL`).
- Backend counters in diagnostics (`AETHERCHAT_METRICS=0` to disable).

