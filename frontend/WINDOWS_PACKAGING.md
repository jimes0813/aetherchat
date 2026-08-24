# AetherChat Windows 打包说明

项目使用 Electron + electron-builder 构建 Windows 桌面客户端，并把 PyInstaller 生成的 FastAPI 后端作为本地资源一并打入 NSIS 安装包。

## 构建环境

在 Windows x64 主机上准备：

- Python 3，且 `py -3` 可用；
- Node.js 与 npm；
- 后端依赖和 PyInstaller；
- 可正常加载 PyWin32 的同一个 Python 环境。

首次构建可在项目根目录执行：

```powershell
py -3 -m pip install -r backend\requirements.txt
py -3 -m pip install pyinstaller
cd frontend
npm ci
```

请用同一个 `py -3` 解释器安装依赖并运行 PyInstaller，否则构建时可能找不到 PyWin32 或 Office/PDF 库。

## 生成 EXE 安装包

在 `frontend` 目录双击或从 PowerShell/CMD 运行：

```bat
build-windows.bat
```

脚本会依次：

1. 使用 `backend/aetherchat-backend.spec` 生成本地后端。
2. 把 `backend/dist/aetherchat-backend` 同步到 `frontend/backend`。
3. 构建 React/Vite 前端。
4. 使用 electron-builder 生成 x64 NSIS 安装器。

当前版本的预期产物为：

```text
frontend/release/AetherChat-Setup-0.2.1-rc.4.exe
```

## 公开发布与代码签名

正式 Windows 发布遵循仓库根目录的
[`CODE_SIGNING_POLICY.md`](../CODE_SIGNING_POLICY.md)。项目获 SignPath
Foundation 开源计划批准后，官方安装包只能从公开仓库的 GitHub Actions
可信构建提交签名；SignPath 私钥保存在其 HSM 中，不会以 PFX 文件下载到
开发者电脑。

未获批准前生成的安装包是未签名测试构建，Windows 可能显示“未知发布者”或
SmartScreen 提示。自签证书只适合受控测试环境，不得作为公开可信签名发布。

SignPath 批准项目后，在 GitHub 仓库中配置以下内容：

- Actions Secret：`SIGNPATH_API_TOKEN`；
- Actions Variables：`SIGNPATH_ORGANIZATION_ID`、`SIGNPATH_PROJECT_SLUG`、
  `SIGNPATH_SIGNING_POLICY_SLUG`，以及需要选择非默认配置时使用的可选
  `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG`。

这些值以 SignPath 项目的 CI integration 页面生成的信息为准。API Token 只能
写入 GitHub Secret，不得粘贴到 Issue、日志或源码。不要同时配置 SignPath 和
传统的 `WINDOWS_CSC_LINK`。`publish-windows-installer` 工作流会先完成测试和
未签名安装检查，再将 GitHub artifact 提交给 SignPath；返回产物只有通过
`Get-AuthenticodeSignature` 验证后才会作为已签名安装包发布。未配置 SignPath
时，该工作流仍会生成明确标注为未签名的测试安装包。

## 自动更新

打包工作流会把 `AETHERCHAT_RELEASE_PUBLIC_KEY_B64` 对应的 Ed25519 公钥嵌入
Windows 客户端。正式安装版启动 30 秒后检查签名更新，并每 6 小时重试；新包
只有通过签名清单、版本、平台/架构、大小、SHA-256 和系统 Authenticode
校验后，才使用
`--updated /S --force-run` 静默覆盖安装并自动重启。开发版或缺少公钥的构建
不会联网检查。设置 `AETHERCHAT_AUTO_UPDATE_DISABLED=1` 可作为紧急停用开关。
RC 版本读取 `windows-ci-latest` 的滚动签名清单，稳定版读取 GitHub Latest；
CI 只为 Authenticode 已验证的安装器生成和发布该清单。

## 手动构建命令

```powershell
cd backend
py -3 -m PyInstaller --noconfirm --clean aetherchat-backend.spec

cd ..\frontend
Remove-Item backend -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item ..\backend\dist\aetherchat-backend backend -Recurse
npm ci
npm run dist:win
```

## 本地能力的打包要点

- `backend/aetherchat-backend.spec` 收集 `python-docx` (`docx`)、`openpyxl`、`python-pptx` (`pptx`)、`pypdf`、`reportlab` 和 Pillow (`PIL`) 的代码/数据。
- 鼠标服务使用 PyWin32。Spec 显式包含 `win32api`、`win32con`、`pywintypes` 和已有的 `win32crypt`，避免动态导入在打包后丢失。
- EXE 和 COLLECT 的 UPX 均关闭。PyWin32 和文档库包含原生扩展，不压缩这些二进制可降低加载失败和杀毒软件误报的风险。
- PDF 追加中文备注使用 ReportLab 的 CID 字体 `STSong-Light`。发布前应对实际生成的中文 PDF 进行渲染检查。
- 截图由 Electron `desktopCapturer` 完成，不依赖 Python 截图模块。

不要从历史的 `backend/dist`、`frontend/backend` 或 `frontend/release` 目录推断新功能已进入安装包；只有完整重新构建后的产物才包含当前源码。

## 运行时结构

- Electron 加载本地 `dist/index.html`，启动随包附带的 FastAPI 后端，并在健康检查通过后显示工作台。
- 若首选端口（默认 8000）被占用，应用会在后续端口中选择可用端口。只有当已存在后端通过健康检查且能验证当前桌面令牌时才会复用。
- 桌面受保护端点仅绑定到本场景中的本机后端，并要求 Electron 主进程持有的随机令牌。令牌不暴露给 Renderer。
- 模型密钥存放在用户数据目录，Windows 上使用当前用户的 DPAPI 加密。
- Windows 安装器使用 NSIS，支持选择安装目录、创建桌面快捷方式和开始菜单快捷方式。

受支持的文档类型、PDF 限制、截图/鼠标行为和安全边界详见 [DESKTOP_CAPABILITIES.md](../docs/DESKTOP_CAPABILITIES.md)。

## 发布前冒烟检查

建议在一个普通（非管理员）Windows 账户中安装并检查：

1. “电脑”面板显示受保护服务已连接，支持的格式列表完整。
2. 用临时副本分别读取、修改和重新打开 TXT/MD、DOCX、XLSX、PPTX 和 PDF，核对内容及备份文件。
3. 对中文 PDF 追加备注，用独立 PDF 阅读器检查页数、字体和元数据。
4. 验证截图前的确认对话框、应用内预览，以及保存到 `Pictures\Screenshots` 的 PNG。
5. 只在无破坏性区域测试读取鼠标位置、移动和单击，并核对每次改变状态前的确认。
6. 直接请求本地 `/desktop/*` 端点但不带正确令牌，应返回 HTTP 403。

不要使用唯一份真实文档做冒烟测试。

## 开发运行

`npm run electron:dev` 会先构建静态前端，再启动 Electron 与本地后端。若要连接 Vite 热更新服务，先运行 `npm run dev`，再在启动 Electron 时设置：

```powershell
$env:VITE_DEV_SERVER_URL = 'http://127.0.0.1:5173'
npm run electron:dev
```
