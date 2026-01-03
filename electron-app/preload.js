const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  onResetUI: (callback) => ipcRenderer.on('reset-ui', (_event, value) => callback(value)),
  onOpenSettings: (callback) => ipcRenderer.on('open-settings', (_event, value) => callback(value)),
  onUpdateStatus: (callback) => ipcRenderer.on('update-status', (_event, value) => callback(value)),
  hideWindow: () => ipcRenderer.send('hide-window')
});

window.addEventListener('DOMContentLoaded', () => {
  console.log('OpenReLife Electron App loaded');
});
