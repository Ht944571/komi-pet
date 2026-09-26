// -*- coding: utf-8 -*-
// 古见同学 Live2D 桌宠 · Electron 主进程
// 结构：透明置顶窗（逐像素穿透）+ 本地静态服务（127.0.0.1，模型走 fetch 必须 HTTP）+ 托盘
const { app, BrowserWindow, Tray, Menu, ipcMain, screen, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');

const PORT = 8799;
const SMOKE = process.argv.includes('--smoke');
const NOTRANS = process.argv.includes('--no-transparency');   // 沙箱验证用：关透明
const POS_FILE = path.join(__dirname, '.pet-pos.json');
const BASE_W = 420, BASE_H = 640;

let win = null, tray = null, server = null, drag = null, quitting = false;
const state = { clickThrough: false, scale: 1, visible: true };

function loadPos() { try { return JSON.parse(fs.readFileSync(POS_FILE, 'utf8')); } catch { return null; } }
function savePos(p) { try { fs.writeFileSync(POS_FILE, JSON.stringify(p)); } catch { } }

// ---- 本地静态服务：WebGL/fetch 在 file:// 下会被 Chromium 拦，所以走 127.0.0.1 ----
function serve() {
  const root = __dirname;
  const mime = {
    '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
    '.json': 'application/json', '.png': 'image/png', '.moc3': 'application/octet-stream',
  };
  return http.createServer((req, res) => {
    let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (p === '/') p = '/renderer/index.html';
    const fp = path.join(root, p);
    if (!fp.startsWith(root)) { res.writeHead(403); return res.end(); }
    fs.readFile(fp, (e, b) => {
      if (e) { res.writeHead(404); return res.end('not found'); }
      res.writeHead(200, { 'Content-Type': mime[path.extname(fp).toLowerCase()] || 'application/octet-stream' });
      res.end(b);
    });
  }).listen(PORT, '127.0.0.1');
}

function createWindow() {
  const wa = screen.getPrimaryDisplay().workAreaSize;
  const W = Math.round(BASE_W * state.scale), H = Math.round(BASE_H * state.scale);
  const pos = loadPos();
  win = new BrowserWindow({
    width: W, height: H,
    x: pos?.x ?? wa.width - W - 12,
    y: pos?.y ?? wa.height - H - 4,
    transparent: !NOTRANS, frame: false, resizable: false, hasShadow: false,
    alwaysOnTop: true, skipTaskbar: true, show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false,
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  // 默认整窗穿透；鼠标移到模型上时由渲染进程收回（forward 让我们仍能收到 mousemove）
  win.setIgnoreMouseEvents(true, { forward: true });
  win.loadURL(`http://127.0.0.1:${PORT}/renderer/index.html`);
  win.webContents.on('did-finish-load', () => console.log('[main] did-finish-load'));
  win.webContents.on('did-fail-load', (e, code, desc, url) =>
    console.log('[main] did-fail-load', code, desc, url));
  win.webContents.on('preload-error', (e, p, err) =>
    console.log('[main] preload-error', p, String(err)));
  win.webContents.on('console-message', (e, level, msg) =>
    console.log('[renderer]', msg));
  win.once('ready-to-show', () => { if (state.visible) win.show(); });
  win.on('closed', () => { win = null; });
}

function createTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, 'icon.png'));
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  tray.setToolTip('古见同学桌宠');
  tray.setContextMenu(buildMenu());
}
function buildMenu() {
  return Menu.buildFromTemplate([
    { label: state.visible ? '隐藏桌宠' : '显示桌宠', click: () => { state.visible = !state.visible; state.visible ? win?.show() : win?.hide(); tray.setContextMenu(buildMenu()); } },
    { type: 'separator' },
    { label: '穿透模式（鼠标穿过角色）', type: 'checkbox', checked: state.clickThrough, click: () => { state.clickThrough = !state.clickThrough; applyClickThrough(); } },
    { type: 'separator' },
    { label: '大小', submenu: Menu.buildFromTemplate([
      { label: '小 (0.75x)', click: () => setScale(0.75) },
      { label: '中 (1x)', click: () => setScale(1) },
      { label: '大 (1.25x)', click: () => setScale(1.25) },
    ]) },
    { type: 'separator' },
    { label: '退出古见同学', click: () => { quitting = true; app.quit(); } },
  ]);
}
function applyClickThrough() {
  win?.setIgnoreMouseEvents(state.clickThrough, { forward: true });
  tray?.setContextMenu(buildMenu());
}
function setScale(s) {
  state.scale = s;
  if (!win) return;
  const [x, y] = win.getPosition();
  win.setBounds({ x, y, width: Math.round(BASE_W * s), height: Math.round(BASE_H * s) });
  win.webContents.send('scale-changed', s);
}

// ---- IPC ----
ipcMain.on('set-click-through', (e, v) => { state.clickThrough = !!v; applyClickThrough(); });
ipcMain.on('drag-start', (e, p) => { drag = { sx: p.screenX, sy: p.screenY, pos: win?.getPosition() }; });
ipcMain.on('drag-move', (e, p) => {
  if (!drag || !win) return;
  const x = drag.pos[0] + (p.screenX - drag.sx);
  const y = drag.pos[1] + (p.screenY - drag.sy);
  const wa = screen.getPrimaryDisplay().workAreaSize;
  const W = Math.round(BASE_W * state.scale), H = Math.round(BASE_H * state.scale);
  win.setPosition(Math.max(0, Math.min(x, wa.width - W)), Math.max(0, Math.min(y, wa.height - H)));
});
ipcMain.on('drag-end', () => { if (win) savePos(win.getPosition()); drag = null; });
ipcMain.on('report', (e, d) => {
  if (SMOKE) { console.log('SMOKE:' + JSON.stringify(d)); setTimeout(() => { quitting = true; app.quit(); }, 400); }
});

// ---- 冒烟模式：无头验证整条链路（窗口/WebGL/l2d/模型加载），超时即报错 ----
let smokeWatch = null;
app.whenReady().then(() => {
  app.commandLine.appendSwitch('enable-transparent-visuals');
  app.setName('古见同学桌宠');
  server = serve();
  createWindow();
  createTray();
  if (SMOKE) {
    // 冒烟模式：等渲染进程在模型加载完成后回报（见 pet.js 的 report）。
    // ⚠️ 不要在单进程模式用 executeJavaScript 探查——它会挂起且永远不 resolve。
    smokeWatch = setTimeout(() => {
      console.log('SMOKE:' + JSON.stringify({ ok: false, error: 'timeout: renderer 12s 内未报告' }));
      quitting = true; app.quit();
    }, 12000);
  }
});
app.on('window-all-closed', () => { if (!SMOKE && !quitting) { /* 不退出：托盘常驻 */ } else app.quit(); });
app.on('before-quit', () => { clearTimeout(smokeWatch); if (server) server.close(); });
