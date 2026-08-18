# AetherChat Linux 迁移记录

> **历史报告，仅供参考。** 本文记录 Linux 移植当时的基线与验证，不代表当前 HEAD。构建与安装以 `frontend/LINUX_PACKAGING.md` 和仓库根 `README.md` 为准。

## 源码基线

源目录不包含 Git 元数据，因此无法通过提交记录计算差异。本次以文件修改时间和 Windows 0.2.0 构建产物确认基线：

- 最新有效前端源码：`frontend/src/App.tsx`，2026-07-12 23:48；
- 最新桌面能力后端：`backend/app/services/computer_use_service.py`，2026-07-12 18:06；
- 最新 Electron 主进程：`frontend/electron/main.cjs`，2026-07-12 17:44；
- Windows 0.2.0 安装包生成于 2026-07-12 23:53。

生成目录、Windows 二进制、Python 虚拟环境和依赖缓存没有作为 Linux 源码复制。工作副本位于 `/home/jimes0813/AetherChat-linux`，USB 原目录未修改。

## Linux 改动

- PyInstaller spec 按平台收集 Win32 依赖，并能在 Linux ARM64 构建本地后端。
- Electron 发布目标新增 Linux AppImage；Deb 使用原生 `dpkg-deb` 构建，以避开 electron-builder ARM64 主机上的 x86 FPM 限制。
- 后端默认数据目录遵循 XDG，Electron 打包运行时继续使用应用专属用户数据目录。
- 运行时平台传入 Renderer，标题栏和电脑面板显示 Linux 状态。
- Linux 能力接口保留文档读写和截图，关闭 Windows UI Automation、键盘和鼠标注入。
- 后端测试改为按平台验证桌面能力，前端模拟桥补齐 `model_computer_use` 契约。

## 验证结果

- TypeScript/Vite 生产构建通过；
- 后端：12 项通过，1 项 Windows 实机测试跳过；
- Chromium E2E：13 项全部通过；
- PyInstaller 后端健康检查通过；
- Electron ARM64 unpacked 应用在 Xvfb 中完成 10 秒启动烟测；
- AppImage、Deb 和内置后端均确认为 ARM64。

正式公开发布前需要替换 `frontend/package.json` 中的 `.local` 占位主页与维护邮箱，并配置正式应用图标。
