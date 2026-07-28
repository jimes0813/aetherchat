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
  appVersion: argumentValue('aether-app-version'),
  platform: argumentValue('aether-platform', process.platform),
  getApiToken: () => ipcRenderer.invoke('aether-runtime:api-token'),
  openExternal: (url) => ipcRenderer.invoke('aether-external:open', url),
  openArtifact: (sessionId, path, action = 'open') => ipcRenderer.invoke(
    'aether-artifact:open',
    { sessionId, path, action },
  ),
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
