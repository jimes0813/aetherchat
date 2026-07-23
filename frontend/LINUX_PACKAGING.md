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

Deb 构建会强制记录 `chrome-sandbox` 为 root 所有、模式 4755；CI 会读取归档目录验证该元数据。签名更新、SBOM、CI secrets 和安装版沙箱终验见 `docs/RELEASE_SECURITY.md`。

## Linux 能力

- 支持聊天、模型配置、插件、任务、工件、文档读写以及 Electron 屏幕截图。
- 版本化工件支持隔离 HTML/SVG 预览、diff/恢复，以及 DOCX/XLSX/PPTX 导出；Office 输出依赖已经包含在 PyInstaller 后端中。
- 本地数据由 Electron 放在用户应用数据目录；独立后端默认使用 `$XDG_DATA_HOME/aetherchat` 或 `~/.local/share/aetherchat`。
- Windows UI Automation、Win32 键鼠注入和启动 `.exe` 在 Linux 上关闭，能力状态接口会如实返回 `false`。
- 模型密钥通过 Secret Service 系统密钥环保存；`vault.json` 只保留引用。首次启动会把旧版明文条目迁移到密钥环。若桌面环境没有可用的 Secret Service，应用会拒绝持久化新密钥，而不是回退为明文。

## 验证

```bash
cd frontend
npm run build
npm run test:e2e
```

安装包运行后，在“运行”面板检查后端 API、WebSocket 和桌面能力状态。Wayland 下截图是否可用取决于桌面环境的屏幕共享授权。

2026-07-16 ARM64 验收还使用打包后端生成三种 Office 文件，并由 LibreOffice Writer、Calc 和 Impress 成功无界面打开并转换为 PDF。浏览器跨源下载需要后端暴露 `Content-Disposition`，当前 CORS 配置已经包含该响应头。
