const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pet', {
  setClickThrough: v => ipcRenderer.send('set-click-through', v),
  dragStart: p => ipcRenderer.send('drag-start', p),
  dragMove: p => ipcRenderer.send('drag-move', p),
  dragEnd: () => ipcRenderer.send('drag-end'),
  report: d => ipcRenderer.send('report', d),
  onScaleChanged: cb => ipcRenderer.on('scale-changed', (e, s) => cb(s)),
  // 视线跟随：主进程全局光标轮询（鼠标不在窗口内也收得到）
  onCursor: cb => ipcRenderer.on('cursor-pos', (e, d) => cb(d)),
});
