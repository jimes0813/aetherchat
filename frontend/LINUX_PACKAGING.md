# AetherChat Linux 打包说明

Linux 版本使用 Electron + electron-builder，并把当前架构上由 PyInstaller 生成的 FastAPI 后端一并打包。构建产物与构建主机架构一致，不能跨架构复用 Windows 后端或 Electron 文件。

## 构建

在项目根目录安装 Python 依赖和 PyInstaller：

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m pip install pyinstaller
cd frontend
npm ci
./build-linux.sh
```

脚本优先使用项目根目录的 `.venv-linux`。也可用 `AETHERCHAT_PYTHON=/path/to/python ./build-linux.sh` 指定已安装依赖的 Python。

Deb 与 Electron 元数据使用项目 GitHub 地址和维护者 noreply 邮箱，不再包含 `aetherchat.local` 占位信息。

脚本支持 Linux x64 和 ARM64，使用 electron-builder 生成 AppImage，并用系统原生 `dpkg-deb` 生成 Deb。文件位于 `frontend/release/`。AppImage 可直接运行，Deb 可通过系统软件中心或 `apt install ./文件名.deb` 安装。

Deb 构建会强制记录 `chrome-sandbox` 为 root 所有、模式 4755；CI 会读取归档目录验证该元数据。桌面菜单、快捷操作和 `aetherchat://` 协议统一经 `/usr/bin/aetherchat` 启动；启动器会在执行前再次校验 `root:root 4755`，不满足条件时拒绝降级启动并提示重新安装 Deb。签名更新、SBOM、CI secrets 和安装版沙箱终验见 `docs/RELEASE_SECURITY.md`。

## Linux 能力

- 支持聊天、模型配置、插件、任务、工件、文档读写以及 Electron 屏幕截图。
- 版本化工件支持隔离 HTML/SVG 预览、diff/恢复，以及 DOCX/XLSX/PPTX 导出；Office 输出依赖已经包含在 PyInstaller 后端中。
- 聊天库、工件和知识库由 `AETHERCHAT_DATA_DIR` 决定。打包桌面若已在 `userData/backend-data`（Linux 上通常是 `~/.config/aetherchat-frontend/backend-data`）找到 `aetherchat.sqlite3`，继续使用该目录，避免把现有会话拆到别处；否则与独立后端一样使用 `$XDG_DATA_HOME/aetherchat` 或 `~/.local/share/aetherchat`。帮助 → 关于与运行面板会显示实际路径。Electron 自己的缓存仍在 `userData`。
- Linux X11 会通过系统 `libX11`/`libXtst` 提供完整 `computer_20251124` 截图与键鼠动作，包括移动、单/双/三击、右/中键、拖拽、滚动、文本、组合键、按键保持、等待和区域放大；每次动作后自动截图复核，并可在 App 内立即接管和撤销会话授权。Windows 继续使用 UI Automation 与 Win32 输入。Wayland 会保持 fail-closed，除非桌面会话提供兼容的 X11/XTest 环境。
- 模型密钥通过 Secret Service 系统密钥环保存；`vault.json` 只保留引用。首次启动会把旧版明文条目迁移到密钥环。若桌面环境没有可用的 Secret Service，应用会拒绝持久化新密钥，而不是回退为明文。
- 安装包注册 `aetherchat://` 协议，并在桌面菜单提供“快速输入”操作。可用 `xdg-open 'aetherchat://chat?prompt=检查报告&mode=project'` 聚焦并填入当前会话，或使用 `aetherchat://new` 新建会话；深链不会自动发送。
- 点击窗口关闭按钮后，安装版会隐藏到系统托盘，让本机后端、单次/循环定时任务和 Review Inbox 继续工作。托盘菜单提供重新打开、快速输入和“退出 AetherChat”；只有明确退出才会停止后端，未完成 Job 会回队并在下次启动恢复。若桌面环境不支持托盘图标，应用保持传统的关闭即退出回退行为。
- 后台自动化使用 SQLite 租约和只读权限快照，不依赖 Redis；完整设计见 `docs/BACKGROUND_AUTOMATION_2026-08-01.md`。

## 验证

```bash
scripts/quality_gate.sh
```

首次在干净环境验证时使用 `AETHERCHAT_QUALITY_VENV=/tmp/aetherchat-quality-venv scripts/quality_gate.sh --bootstrap`；它会重建 Python 虚拟环境、执行 `npm ci`，然后运行 diff、全仓 Ruff、Mypy、完整后端、Electron、前端单元、构建和 Playwright 门禁。

安装包运行后，在“运行”面板检查后端 API、WebSocket 和桌面能力状态。Wayland 下截图是否可用取决于桌面环境的屏幕共享授权。

2026-07-16 ARM64 验收还使用打包后端生成三种 Office 文件，并由 LibreOffice Writer、Calc 和 Impress 成功无界面打开并转换为 PDF。浏览器跨源下载需要后端暴露 `Content-Disposition`，当前 CORS 配置已经包含该响应头。
