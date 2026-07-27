const { app, BrowserWindow, Menu, dialog, ipcMain, shell, desktopCapturer, screen } = require('electron');
const { spawn } = require('child_process');
const { randomBytes, randomUUID, timingSafeEqual } = require('crypto');
const path = require('path');
const fs = require('fs');
const http = require('http');
const net = require('net');
const { stageUpdate } = require('./update-verifier.cjs');
const { launchWindowsInstaller } = require('./update-launcher.cjs');

const isDev = !app.isPackaged;
let backendProcess = null;
let backendPort = Number(process.env.AETHERCHAT_PORT || 8000);
let backendStatus = 'starting';
const desktopBridgeToken = process.env.AETHERCHAT_DESKTOP_TOKEN || randomBytes(32).toString('hex');
const apiAccessToken = process.env.AETHERCHAT_API_TOKEN || randomBytes(32).toString('hex');
const computerConsentToken = randomBytes(32).toString('hex');
const desktopFileHandles = new Map();
const desktopHandleOwners = new Set();
const MAX_DESKTOP_FILE_HANDLES = 256;
let computerConsentServer = null;
let computerConsentUrl = '';
let computerUseSessionApproved = false;
let computerConsentQueue = Promise.resolve();

function updatePublicKey() {
  if (process.env.AETHERCHAT_UPDATE_PUBLIC_KEY_B64) {
    return Buffer.from(process.env.AETHERCHAT_UPDATE_PUBLIC_KEY_B64, 'base64').toString('utf8');
  }
  const publicKeyPath = path.join(__dirname, 'update-public-key.pem');
  return fs.existsSync(publicKeyPath) ? fs.readFileSync(publicKeyPath, 'utf8') : '';
}

async function checkForSignedUpdate() {
  const owner = BrowserWindow.getAllWindows().find((window) => !window.isDestroyed());
  const manifestUrl = String(
    process.env.AETHERCHAT_UPDATE_MANIFEST_URL
      || 'https://github.com/jimes0813/AetherChat/releases/latest/download/update-manifest.signed.json',
  ).trim();
  const publicKey = updatePublicKey();
  if (!manifestUrl || !publicKey) {
    await dialog.showMessageBox(owner, {
      type: 'info', title: 'AetherChat 更新', message: '此构建未配置正式更新通道',
      detail: '为避免接受无签名软件包，缺少 HTTPS 清单地址或 Ed25519 公钥时更新功能保持禁用。',
      buttons: ['确定'],
    });
    return;
  }
  const consent = await dialog.showMessageBox(owner, {
    type: 'question', title: '检查签名更新', message: '允许连接正式更新服务器吗？',
    detail: 'AetherChat 只会下载已签名清单；软件包还必须通过版本、平台、架构、大小和 SHA-256 校验。',
    buttons: ['允许本次检查', '取消'], defaultId: 1, cancelId: 1, noLink: true,
  });
  if (consent.response !== 0) return;
  try {
    const staged = await stageUpdate({
      manifestUrl,
      publicKey,
      currentVersion: app.getVersion(),
      platform: process.platform === 'win32' ? 'windows' : process.platform,
      arch: process.arch,
      downloadDir: path.join(app.getPath('userData'), 'updates'),
    });
    if (staged.path.endsWith('.AppImage')) fs.chmodSync(staged.path, 0o700);
    const install = await dialog.showMessageBox(owner, {
      type: 'info', title: '签名更新已验证',
      message: `AetherChat ${staged.manifest.version} 已安全暂存`,
      detail: `SHA-256：${staged.sha256}\n\n当前版本和用户数据尚未修改。启动安装程序后如果安装失败，仍可继续使用当前版本。`,
      buttons: ['启动安装程序', '稍后'], defaultId: 1, cancelId: 1, noLink: true,
    });
    if (install.response === 0) {
      if (process.platform === 'win32') {
        await launchWindowsInstaller(staged.path);
        app.quit();
      } else {
        const error = await shell.openPath(staged.path);
        if (error) throw new Error(error);
      }
    }
  } catch (error) {
    await dialog.showMessageBox(owner, {
      type: 'error', title: '更新已拒绝', message: '更新验证或暂存失败',
      detail: `${String(error?.message || error)}\n\n当前安装和用户数据未被修改。`, buttons: ['确定'],
    });
  }
}

const documentFilters = [{
  name: 'AetherChat 支持的文档',
  extensions: ['txt', 'md', 'json', 'csv', 'tsv', 'docx', 'xlsx', 'pptx', 'pdf'],
}];

function cleanupDesktopFileHandles(webContentsId) {
  for (const [handle, fileHandle] of desktopFileHandles) {
    if (fileHandle.webContentsId === webContentsId) desktopFileHandles.delete(handle);
  }
}

function rememberDesktopFileHandle(event, filePath) {
  const webContentsId = event.sender.id;
  if (!desktopHandleOwners.has(webContentsId)) {
    desktopHandleOwners.add(webContentsId);
    event.sender.once('destroyed', () => {
      cleanupDesktopFileHandles(webContentsId);
      desktopHandleOwners.delete(webContentsId);
    });
  }
  while (desktopFileHandles.size >= MAX_DESKTOP_FILE_HANDLES) {
    const oldestHandle = desktopFileHandles.keys().next().value;
    if (!oldestHandle) break;
    desktopFileHandles.delete(oldestHandle);
  }
  const handle = randomUUID();
  desktopFileHandles.set(handle, { path: filePath, webContentsId });
  return handle;
}

function rendererSafeDocumentSaveResult(saved, destination) {
  const backupPath = typeof saved?.backup_path === 'string' && saved.backup_path
    ? saved.backup_path
    : null;
  const result = {
    saved: saved?.saved === true,
    displayName: path.basename(destination),
    sizeBytes: Number(saved?.size_bytes || 0),
    savedAt: typeof saved?.saved_at === 'string' ? saved.saved_at : '',
    backupCreated: Boolean(backupPath),
  };
  if (backupPath) result.backupDisplayName = path.basename(backupPath);
  return result;
}

function assertTrustedDesktopSender(event) {
  const senderUrl = event.senderFrame?.url || event.sender.getURL();
  const mainFrameUrl = event.sender.getURL();
  const isMainFrame = !event.senderFrame || event.senderFrame === event.sender.mainFrame;
  const trustedFile = senderUrl.startsWith('file:') && senderUrl === mainFrameUrl;
  const trustedDev = isDev
    && process.env.VITE_DEV_SERVER_URL
    && senderUrl.startsWith(process.env.VITE_DEV_SERVER_URL);
  if (!isMainFrame || (!trustedFile && !trustedDev)) {
    throw new Error('Untrusted renderer cannot use desktop capabilities');
  }
}

async function confirmDesktopAction(event, message, detail) {
  const owner = BrowserWindow.fromWebContents(event.sender);
  const result = await dialog.showMessageBox(owner || undefined, {
    type: 'warning',
    title: 'AetherChat 本地电脑权限',
    message,
    detail,
    buttons: ['允许本次操作', '取消'],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
  });
  return result.response === 0;
}

function secureTokenMatches(candidate) {
  const actual = Buffer.from(String(candidate || ''), 'utf8');
  const expected = Buffer.from(computerConsentToken, 'utf8');
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

async function confirmModelComputerUse(payload) {
  const risk = String(payload?.risk || 'computer_control');
  const alwaysConfirm = new Set(['file_write', 'app_launch']).has(risk);
  if (computerUseSessionApproved && !alwaysConfirm) return { approved: true, scope: 'session' };

  const owner = BrowserWindow.getAllWindows().find((window) => !window.isDestroyed());
  const action = String(payload?.action || '电脑操作').slice(0, 80);
  const detail = String(payload?.detail || '').slice(0, 2000);
  const buttons = alwaysConfirm
    ? ['允许本次操作', '取消']
    : ['允许本次操作', '允许本次会话', '拒绝'];
  const cancelId = buttons.length - 1;
  const result = await dialog.showMessageBox(owner || undefined, {
    type: 'warning',
    title: 'AetherChat 模型电脑控制',
    message: `模型请求执行：${action}`,
    detail: `${detail}\n\n“允许本次会话”会授权模型观察屏幕并操作键鼠，直到退出 AetherChat。所有操作都会记录在工具运行日志中。`,
    buttons,
    defaultId: cancelId,
    cancelId,
    noLink: true,
  });
  if (!alwaysConfirm && result.response === 1) {
    computerUseSessionApproved = true;
    return { approved: true, scope: 'session' };
  }
  return { approved: result.response === 0, scope: result.response === 0 ? 'once' : 'denied' };
}

function startComputerConsentServer() {
  if (computerConsentServer && computerConsentUrl) return Promise.resolve(computerConsentUrl);
  return new Promise((resolve, reject) => {
    const server = http.createServer((request, response) => {
      const send = (statusCode, payload) => {
        const body = Buffer.from(JSON.stringify(payload), 'utf8');
        response.writeHead(statusCode, {
          'Content-Type': 'application/json; charset=utf-8',
          'Content-Length': body.length,
          'Cache-Control': 'no-store',
        });
        response.end(body);
      };
      if (request.method !== 'POST' || request.url !== '/consent') {
        send(404, { approved: false, error: 'not_found' });
        return;
      }
      if (!secureTokenMatches(request.headers['x-aether-consent-token'])) {
        send(403, { approved: false, error: 'forbidden' });
        return;
      }
      const chunks = [];
      let size = 0;
      request.on('data', (chunk) => {
        size += chunk.length;
        if (size > 64 * 1024) {
          request.destroy();
          return;
        }
        chunks.push(chunk);
      });
      request.on('end', async () => {
        if (size > 64 * 1024) return;
        let payload;
        try {
          payload = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
        } catch {
          send(400, { approved: false, error: 'invalid_json' });
          return;
        }
        computerConsentQueue = computerConsentQueue
          .catch(() => undefined)
          .then(() => confirmModelComputerUse(payload));
        try {
          send(200, await computerConsentQueue);
        } catch (error) {
          send(500, { approved: false, error: String(error?.message || error) });
        }
      });
    });
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') {
        server.close();
        reject(new Error('Unable to bind the model consent broker'));
        return;
      }
      computerConsentServer = server;
      computerConsentUrl = `http://127.0.0.1:${address.port}/consent`;
      resolve(computerConsentUrl);
    });
  });
}

function stopComputerConsentServer() {
  computerUseSessionApproved = false;
  computerConsentUrl = '';
  if (computerConsentServer) computerConsentServer.close();
  computerConsentServer = null;
}

function backendExecutablePath() {
  const executable = process.platform === 'win32' ? 'aetherchat-backend.exe' : 'aetherchat-backend';
  if (app.isPackaged) return path.join(process.resourcesPath, 'backend', executable);
  return path.join(__dirname, '../../backend/dist/aetherchat-backend', executable);
}

function startBackend(port) {
  const exePath = backendExecutablePath();
  if (!fs.existsSync(exePath)) return false;
  if (backendProcess && !backendProcess.killed) return true;

  const dataDir = path.join(app.getPath('userData'), 'backend-data');
  fs.mkdirSync(dataDir, { recursive: true });
  const noProxyEntries = (process.env.NO_PROXY || process.env.no_proxy || '')
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
  for (const localHost of ['localhost', '127.0.0.1', '::1']) {
    if (!noProxyEntries.includes(localHost)) noProxyEntries.push(localHost);
  }
  const noProxy = noProxyEntries.join(',');
  backendProcess = spawn(exePath, [], {
    cwd: path.dirname(exePath),
    windowsHide: true,
    env: {
      ...process.env,
      AETHERCHAT_DATA_DIR: dataDir,
      AETHERCHAT_PORT: String(port),
      AETHERCHAT_DESKTOP_TOKEN: desktopBridgeToken,
      AETHERCHAT_API_TOKEN: apiAccessToken,
      AETHERCHAT_COMPUTER_CONSENT_URL: computerConsentUrl,
      AETHERCHAT_COMPUTER_CONSENT_TOKEN: computerConsentToken,
      NO_PROXY: noProxy,
      no_proxy: noProxy,
    },
    stdio: 'ignore',
  });
  backendProcess.on('error', (error) => {
    backendStatus = 'unavailable';
    console.error('AetherChat backend failed to start:', error);
  });
  backendProcess.on('exit', () => {
    backendProcess = null;
  });
  return true;
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill();
  }
  backendProcess = null;
}

function probeBackend(port, timeoutMs = 1000) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/health`, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        try {
          const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          resolve(Boolean(res.statusCode === 200 && payload.status === 'ok' && payload.service === 'aetherchat'));
        } catch {
          resolve(false);
        }
      });
    });
    req.on('error', () => resolve(false));
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function backendRequest(endpoint, { method = 'GET', body } = {}) {
  return new Promise((resolve, reject) => {
    const encoded = body === undefined ? null : Buffer.from(JSON.stringify(body), 'utf8');
    const request = http.request({
      hostname: '127.0.0.1',
      port: backendPort,
      path: endpoint,
      method,
      headers: {
        'X-Aether-Desktop-Token': desktopBridgeToken,
        'Authorization': `Bearer ${apiAccessToken}`,
        ...(encoded ? {
          'Content-Type': 'application/json',
          'Content-Length': encoded.length,
        } : {}),
      },
      timeout: 15000,
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        let payload;
        try {
          payload = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
        } catch {
          reject(new Error(`Desktop service returned invalid JSON (HTTP ${response.statusCode})`));
          return;
        }
        if ((response.statusCode || 500) >= 400) {
          reject(new Error(payload.detail || `Desktop service failed (HTTP ${response.statusCode})`));
          return;
        }
        resolve(payload);
      });
    });
    request.on('error', reject);
    request.on('timeout', () => request.destroy(new Error('Desktop service timed out')));
    if (encoded) request.write(encoded);
    request.end();
  });
}

async function probeDesktopBackend(port) {
  const previousPort = backendPort;
  backendPort = port;
  try {
    const result = await backendRequest('/desktop/status');
    return result?.available === true;
  } catch {
    return false;
  } finally {
    backendPort = previousPort;
  }
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => {
      server.close(() => resolve(true));
    });
  });
}

async function selectBackendPort(preferredPort = 8000) {
  if (await probeBackend(preferredPort) && await probeDesktopBackend(preferredPort)) {
    return { port: preferredPort, reuse: true };
  }
  if (await isPortFree(preferredPort)) return { port: preferredPort, reuse: false };
  for (let port = preferredPort + 1; port <= preferredPort + 20; port += 1) {
    if (await isPortFree(port)) return { port, reuse: false };
  }
  throw new Error('No available local port for the AetherChat backend');
}

function waitForBackend(port, timeoutMs = 15000) {
  const startedAt = Date.now();

  return new Promise((resolve) => {
    const check = async () => {
      if (await probeBackend(port)) {
        resolve(true);
        return;
      }
      retry();
    };

    const retry = () => {
      if (Date.now() - startedAt >= timeoutMs) {
        resolve(false);
        return;
      }
      setTimeout(check, 300);
    };

    check();
  });
}

function createWindow() {
  const existing = BrowserWindow.getAllWindows()[0];
  if (existing) {
    if (existing.isMinimized()) existing.restore();
    existing.focus();
    return existing;
  }
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1180,
    minHeight: 760,
    title: 'AetherChat',
    backgroundColor: '#f5efe7',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [
        `--aether-api-base=http://127.0.0.1:${backendPort}`,
        `--aether-backend-status=${backendStatus}`,
        `--aether-app-version=${app.getVersion()}`,
        `--aether-platform=${process.platform}`,
      ],
    },
  });

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL);
    win.webContents.openDevTools({ mode: 'detach' });
  } else {
    win.loadFile(path.join(__dirname, '../dist/index.html'));
  }

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    const currentUrl = win.webContents.getURL();
    if (url === currentUrl) return;
    event.preventDefault();
    try {
      const target = new URL(url);
      if (/^https?:$/.test(target.protocol)) shell.openExternal(url);
    } catch {
      // Invalid and non-HTTP navigation is denied.
    }
  });
  win.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  return win;
}

const menu = Menu.buildFromTemplate([
  { label: '文件', submenu: [{ role: 'quit', label: '退出' }] },
  { label: '编辑', submenu: [{ role: 'undo', label: '撤销' }, { role: 'redo', label: '重做' }, { type: 'separator' }, { role: 'copy', label: '复制' }, { role: 'paste', label: '粘贴' }] },
  { label: '视图', submenu: [{ role: 'reload', label: '刷新' }, ...(isDev ? [{ role: 'toggleDevTools', label: '开发者工具' }] : []), { role: 'togglefullscreen', label: '全屏' }] },
  { label: '运行', submenu: [{ label: '打开工作台', click: () => createWindow() }] },
  {
    label: '帮助',
    submenu: [{ label: '检查签名更新', click: () => checkForSignedUpdate() }, { type: 'separator' }, {
      label: '关于 AetherChat',
      click: () => dialog.showMessageBox({
        type: 'info',
        title: '关于 AetherChat',
        message: `AetherChat ${app.getVersion()}`,
        detail: '多模型中文协作桌面工作台\n前端与本机后端一体运行',
        buttons: ['确定'],
      }),
    }],
  },
]);

ipcMain.on('aether-window:minimize', (event) => {
  BrowserWindow.fromWebContents(event.sender)?.minimize();
});

ipcMain.on('aether-window:toggle-maximize', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return;
  if (win.isMaximized()) {
    win.unmaximize();
  } else {
    win.maximize();
  }
});

ipcMain.on('aether-window:close', (event) => {
  BrowserWindow.fromWebContents(event.sender)?.close();
});

ipcMain.handle('aether-external:open', async (_event, value) => {
  assertTrustedDesktopSender(_event);
  const url = new URL(String(value || ''));
  if (!/^https?:$/.test(url.protocol)) throw new Error('Only HTTP(S) links are allowed');
  await shell.openExternal(url.toString());
  return true;
});

ipcMain.handle('aether-runtime:api-token', async (event) => {
  assertTrustedDesktopSender(event);
  return apiAccessToken;
});

ipcMain.handle('aether-desktop:status', async (event) => {
  assertTrustedDesktopSender(event);
  return backendRequest('/desktop/status');
});

ipcMain.handle('aether-desktop:select-document', async (event) => {
  assertTrustedDesktopSender(event);
  const result = await dialog.showOpenDialog({
    title: '选择要在 AetherChat 中编辑的文档',
    properties: ['openFile'],
    filters: documentFilters,
  });
  if (result.canceled || !result.filePaths[0]) return null;
  const selectedPath = path.resolve(result.filePaths[0]);
  const document = await backendRequest('/desktop/document/read', {
    method: 'POST',
    body: { path: selectedPath },
  });
  const handle = rememberDesktopFileHandle(event, selectedPath);
  return { handle, displayName: path.basename(selectedPath), document };
});

ipcMain.handle('aether-workspace:select-repository', async (event, sessionId) => {
  assertTrustedDesktopSender(event);
  const normalizedSessionId = String(sessionId || '');
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(normalizedSessionId)) {
    throw new Error('A valid session ID is required');
  }
  const result = await dialog.showOpenDialog({
    title: '选择干净的 Git 仓库作为 Agent 工作区',
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return backendRequest('/workspaces/repositories/register', {
    method: 'POST',
    body: {
      session_id: normalizedSessionId,
      path: path.resolve(result.filePaths[0]),
    },
  });
});

ipcMain.handle('aether-workspace:decide-candidate', async (event, value) => {
  assertTrustedDesktopSender(event);
  const workspaceId = String(value?.workspaceId || '');
  const sessionId = String(value?.sessionId || '');
  const decision = String(value?.decision || '');
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  if (!uuidPattern.test(workspaceId) || !uuidPattern.test(sessionId)) {
    throw new Error('A valid workspace and session ID are required');
  }
  if (!['apply', 'reject'].includes(decision)) {
    throw new Error('Unsupported workspace decision');
  }
  const owner = BrowserWindow.fromWebContents(event.sender);
  const changedFiles = Math.max(0, Number(value?.changedFiles || 0));
  const verification = String(value?.verificationStatus || 'unknown').slice(0, 80);
  const confirmation = await dialog.showMessageBox(owner || undefined, {
    type: decision === 'apply' ? 'warning' : 'question',
    title: decision === 'apply' ? '应用 Agent Git 候选' : '丢弃 Agent Git 候选',
    message: decision === 'apply'
      ? `允许把 ${changedFiles} 个文件的候选变更应用到所选仓库吗？`
      : '确定丢弃这个隔离 worktree 候选吗？',
    detail: decision === 'apply'
      ? `确定性核验：${verification}\n\nAetherChat 会再次校验仓库 HEAD、干净状态和补丁哈希；只应用工作区变更，不会 commit、push 或访问网络。`
      : '丢弃后会清理 AetherChat 管理的 worktree；用户主仓库不会被修改。',
    buttons: decision === 'apply' ? ['应用变更', '取消'] : ['丢弃候选', '取消'],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
  });
  if (confirmation.response !== 0) return null;
  return backendRequest(`/workspaces/candidates/${workspaceId}/decision`, {
    method: 'POST',
    body: {
      session_id: sessionId,
      decision,
    },
  });
});

ipcMain.handle('aether-desktop:save-document', async (_event, value) => {
  assertTrustedDesktopSender(_event);
  const handle = String(value?.handle || '');
  const fileHandle = desktopFileHandles.get(handle);
  if (!fileHandle || fileHandle.webContentsId !== _event.sender.id) throw new Error('Document handle expired; select the file again');
  const selectedPath = fileHandle.path;
  if (!await confirmDesktopAction(_event, '允许写入所选文档吗？', `AetherChat 将更新 ${path.basename(selectedPath)}，并先创建备份。`)) {
    throw new Error('Document write was cancelled');
  }
  const saved = await backendRequest('/desktop/document/write', {
    method: 'POST',
    body: {
      path: selectedPath,
      source_path: selectedPath,
      data: value?.data || {},
      create_backup: true,
    },
  });
  return rendererSafeDocumentSaveResult(saved, selectedPath);
});

ipcMain.handle('aether-desktop:save-document-as', async (_event, value) => {
  assertTrustedDesktopSender(_event);
  const handle = String(value?.handle || '');
  const fileHandle = desktopFileHandles.get(handle);
  if (!fileHandle || fileHandle.webContentsId !== _event.sender.id) throw new Error('Document handle expired; select the file again');
  const selectedPath = fileHandle.path;
  const extension = path.extname(selectedPath).replace(/^\./, '');
  const result = await dialog.showSaveDialog({
    title: '将编辑后的文档另存为',
    defaultPath: path.join(path.dirname(selectedPath), `${path.basename(selectedPath, path.extname(selectedPath))}-edited.${extension}`),
    filters: [{ name: `${extension.toUpperCase()} 文档`, extensions: [extension] }],
  });
  if (result.canceled || !result.filePath) return null;
  const destination = path.resolve(result.filePath);
  const saved = await backendRequest('/desktop/document/write', {
    method: 'POST',
    body: {
      path: destination,
      source_path: selectedPath,
      data: value?.data || {},
      create_backup: true,
    },
  });
  desktopFileHandles.delete(handle);
  const nextHandle = rememberDesktopFileHandle(_event, destination);
  return {
    ...rendererSafeDocumentSaveResult(saved, destination),
    handle: nextHandle,
  };
});

ipcMain.handle('aether-desktop:capture-screen', async (event) => {
  assertTrustedDesktopSender(event);
  if (!await confirmDesktopAction(event, '允许截取主屏幕吗？', '截图仅保留在本机预览中，除非你随后点击保存。')) {
    throw new Error('Screen capture was cancelled');
  }
  const primary = screen.getPrimaryDisplay();
  const scaleFactor = primary.scaleFactor || 1;
  const sources = await desktopCapturer.getSources({
    types: ['screen'],
    thumbnailSize: {
      width: Math.max(1, Math.round(primary.size.width * scaleFactor)),
      height: Math.max(1, Math.round(primary.size.height * scaleFactor)),
    },
  });
  const source = sources.find((item) => String(item.display_id) === String(primary.id));
  if (!source) throw new Error(`Primary display ${primary.id} was not returned by the screen capture service`);
  if (source.thumbnail.isEmpty()) throw new Error('Primary screen capture returned no image');
  const size = source.thumbnail.getSize();
  return {
    dataUrl: source.thumbnail.toDataURL(),
    width: size.width,
    height: size.height,
    capturedAt: new Date().toISOString(),
  };
});

ipcMain.handle('aether-desktop:save-screenshot', async (_event, dataUrl) => {
  assertTrustedDesktopSender(_event);
  const match = /^data:image\/png;base64,([A-Za-z0-9+/=]+)$/.exec(String(dataUrl || ''));
  if (!match) throw new Error('Invalid PNG screenshot payload');
  const image = Buffer.from(match[1], 'base64');
  if (!image.length || image.length > 50 * 1024 * 1024) throw new Error('Screenshot payload is empty or too large');
  const outputDirectory = path.join(app.getPath('pictures'), 'Screenshots');
  fs.mkdirSync(outputDirectory, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const outputPath = path.join(outputDirectory, `AetherChat-${stamp}.png`);
  fs.writeFileSync(outputPath, image, { flag: 'wx' });
  return {
    saved: true,
    displayName: path.basename(outputPath),
    locationLabel: '图片/Screenshots',
    sizeBytes: image.length,
  };
});

ipcMain.handle('aether-desktop:mouse', async (_event, value) => {
  assertTrustedDesktopSender(_event);
  const action = String(value?.action || 'get');
  if (!['get', 'move', 'click'].includes(action)) throw new Error('Unsupported mouse action');
  if (action !== 'get') {
    const detail = action === 'move'
      ? `移动到 (${value?.x}, ${value?.y})。`
      : `在 (${value?.x}, ${value?.y}) 执行 ${value?.button || 'left'} 单击。`;
    if (!await confirmDesktopAction(_event, action === 'move' ? '允许移动鼠标吗？' : '允许执行鼠标点击吗？', detail)) {
      throw new Error('Mouse action was cancelled');
    }
  }
  return backendRequest('/desktop/mouse', {
    method: 'POST',
    body: {
      action,
      x: value?.x,
      y: value?.y,
      button: value?.button || 'left',
    },
  });
});

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) app.quit();

app.on('second-instance', () => {
  createWindow();
});

app.whenReady().then(async () => {
  if (!hasSingleInstanceLock) return;
  try {
    await startComputerConsentServer();
    const selected = await selectBackendPort(backendPort);
    backendPort = selected.port;
    if (selected.reuse) {
      backendStatus = 'ready';
    } else if (startBackend(backendPort)) {
      backendStatus = await waitForBackend(backendPort) ? 'ready' : 'unavailable';
    } else {
      backendStatus = 'unavailable';
    }
  } catch (error) {
    backendStatus = 'unavailable';
    console.error('Unable to prepare the AetherChat backend:', error);
  }
  Menu.setApplicationMenu(menu);
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  desktopFileHandles.clear();
  desktopHandleOwners.clear();
  stopComputerConsentServer();
  stopBackend();
});
