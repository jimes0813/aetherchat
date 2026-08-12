const { contextBridge, ipcRenderer } = require('electron');

function argumentValue(name, fallback = '') {
  const prefix = `--${name}=`;
  const argument = process.argv.find((item) => item.startsWith(prefix));
  return argument ? argument.slice(prefix.length) : fallback;
}

contextBridge.exposeInMainWorld('aetherWindow', {
  minimize: () => ipcRenderer.send('aether-window:minimize'),
  toggleMaximize: () => ipcRenderer.send('aether-window:toggle-maximize'),
  close: () => ipcRenderer.send('aether-window:close'),
});

contextBridge.exposeInMainWorld('aetherRuntime', {
  apiBase: argumentValue('aether-api-base', 'http://127.0.0.1:8000'),
  backendStatus: argumentValue('aether-backend-status', 'starting'),
  backendErrorCode: argumentValue('aether-backend-error-code'),
  backendRequestId: argumentValue('aether-backend-request-id'),
  appVersion: argumentValue('aether-app-version'),
  platform: argumentValue('aether-platform', process.platform),
  quickEntryShortcut: argumentValue('aether-quick-entry-shortcut'),
  getApiToken: () => ipcRenderer.invoke('aether-runtime:api-token'),
  openExternal: (url, options = {}) => ipcRenderer.invoke(
    'aether-external:open',
    { url, ...options },
  ),
  openArtifact: (sessionId, path, action = 'open') => ipcRenderer.invoke(
    'aether-artifact:open',
    { sessionId, path, action },
  ),
  onLaunchIntent: (callback) => {
    if (typeof callback !== 'function') return () => undefined;
    const listener = (_event, intent) => callback(intent);
    ipcRenderer.on('aether-intent:compose', listener);
    void ipcRenderer.invoke('aether-intent:ready');
    return () => ipcRenderer.removeListener('aether-intent:compose', listener);
  },
  onBackgroundChange: (callback) => {
    if (typeof callback !== 'function') return () => undefined;
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('aether-background:changed', listener);
    return () => ipcRenderer.removeListener('aether-background:changed', listener);
  },
});

contextBridge.exposeInMainWorld('aetherDesktop', {
  status: () => ipcRenderer.invoke('aether-desktop:status'),
  selectDocument: () => ipcRenderer.invoke('aether-desktop:select-document'),
  saveDocument: (handle, data) => ipcRenderer.invoke('aether-desktop:save-document', { handle, data }),
  saveDocumentAs: (handle, data) => ipcRenderer.invoke('aether-desktop:save-document-as', { handle, data }),
  captureScreen: () => ipcRenderer.invoke('aether-desktop:capture-screen'),
  saveScreenshot: (dataUrl) => ipcRenderer.invoke('aether-desktop:save-screenshot', dataUrl),
  mouse: (action, options = {}) => ipcRenderer.invoke('aether-desktop:mouse', { action, ...options }),
});

contextBridge.exposeInMainWorld('aetherWorkspace', {
  selectRepository: (sessionId) => ipcRenderer.invoke('aether-workspace:select-repository', sessionId),
  decideCandidate: (workspaceId, sessionId, decision, summary = {}) => ipcRenderer.invoke(
    'aether-workspace:decide-candidate',
    { workspaceId, sessionId, decision, ...summary },
  ),
});
