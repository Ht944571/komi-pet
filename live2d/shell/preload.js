const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pet', {
  setClickThrough: v => ipcRenderer.send('set-click-through', v),
  dragStart: p => ipcRenderer.send('drag-start', p),
  dragMove: p => ipcRenderer.send('drag-move', p),
  dragEnd: () => ipcRenderer.send('drag-end'),
  report: d => ipcRenderer.send('report', d),
  onScaleChanged: cb => ipcRenderer.on('scale-changed', (e, s) => cb(s)),
});
