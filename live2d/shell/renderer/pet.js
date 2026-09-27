// 古见同学 Live2D 桌宠 · 渲染逻辑
// 依赖：l2d（IIFE 全局 L2D）、preload 暴露的 window.pet
const canvas = document.getElementById('cv');
const bubble = document.getElementById('bubble');
const qs = new URLSearchParams(location.search);
// 默认加载「古见同学」新模型（15 部件版，绑骨后导出）。
// 仍可用 ?model=<相对路径> 覆盖，方便临时换回 samples 或旧版最小模型。
const MODEL = qs.get('model') ||
  '../../model/古见同学/拆层_古见同学_对齐裁切.model3.json';

function report(d) { try { window.pet.report(d); } catch { } }
// 渲染进程的报错要能浮到主进程（否则单进程/无头模式下完全不可见）
window.addEventListener('error', e =>
  report({ ok: false, error: 'renderer: ' + e.message + ' @' + e.filename + ':' + e.lineno }));
window.addEventListener('unhandledrejection', e =>
  report({ ok: false, error: 'renderer: unhandledrejection ' + String(e.reason) }));

let ready = false, dragOn = false, over = null;
let lastGaze = 0, bubbleT = null, pokeT = null;
let l2d = null;

function fit() {
  canvas.width = innerWidth;
  canvas.height = innerHeight;
  try { l2d && l2d.resize(); } catch { }
}
addEventListener('resize', fit);

function hitAt(cx, cy) {
  for (const b of l2d.getHitAreaBounds())
    if (cx >= b.x && cx <= b.x + b.w && cy >= b.y && cy <= b.y + b.h) return b.name;
  return null;
}
// 视线判定范围：与 Python 2D 版统一的**固定 443×465 物理像素**（中心=窗口中心）。
// 窗口默认整窗穿透、鼠标基本不在窗口里 → 判定必须基于**全局光标**（主进程按 ~15fps
// 轮询 screen.getCursorScreenPoint 后推送），不能只靠窗口自己的 mousemove。
// ⚠️ 主进程的 screen/bounds 都是 DIP，所以下面要按 devicePixelRatio 把物理像素折回 DIP，
//    否则高 DPI 屏上判定框会随缩放倍数变小（150% 屏只有 2/3 大）。
const GAZE_BOX_W = 443, GAZE_BOX_H = 465;

function applyGaze(dx, dy) {
  const now = performance.now();
  if (now - lastGaze < 66) return;                 // 15fps 节流，与桌宠气质一致
  lastGaze = now;
  l2d.setParams({
    ParamAngleX: dx * 30, ParamAngleY: -dy * 20,
    ParamEyeBallX: dx, ParamEyeBallY: -dy,
  });
}
function gazeFromCursor(d) {
  if (!ready || dragOn || !d || !d.win || !d.cur) return;
  const dpr = window.devicePixelRatio || 1;            // 物理像素 → DIP
  const cx = d.win.x + d.win.width / 2;
  const cy = d.win.y + d.win.height / 2;
  const hw = GAZE_BOX_W / 2 / dpr, hh = GAZE_BOX_H / 2 / dpr;
  const ox = d.cur.x - cx, oy = d.cur.y - cy;
  if (Math.abs(ox) > hw || Math.abs(oy) > hh) {    // 出判定范围 → 视线回正
    applyGaze(0, 0);
    return;
  }
  applyGaze(Math.max(-1, Math.min(1, ox / hw)), Math.max(-1, Math.min(1, oy / hh)));
}
const LINES = {
  head: ['……唔。被摸头的话，稍微、有点开心。', '在、在听。不要突然伸手。'],
  body: ['（戳）……干嘛啦。', '别戳那里。'],
};
function say(t) {
  bubble.textContent = t;
  bubble.classList.add('on');
  clearTimeout(bubbleT);
  bubbleT = setTimeout(() => bubble.classList.remove('on'), 3200);
}
function onTap(area) {
  const arr = LINES[area] || ['……（看了你一眼）'];
  say(arr[Math.floor(Math.random() * arr.length)]);
  l2d.setParams({ ParamMouthOpenY: 0.8, ParamAngleZ: area === 'head' ? -6 : 6 });
  clearTimeout(pokeT);
  pokeT = setTimeout(() => l2d.setParams({ ParamMouthOpenY: 0, ParamAngleZ: 0 }), 700);
}

function boot() {
  fit();
  l2d = L2D.init(canvas);

  l2d.on('loaded', () => {
    ready = true;
    fit();
    const areas = l2d.getHitAreaBounds().map(b => b.name);
    console.log('model-loaded params=' + l2d.getParams().length +
                ' hitAreas=' + areas.join(','));
    report({ ok: true, model: MODEL, params: l2d.getParams().length, hitAreas: areas });
  });
  l2d.on('tap', area => { if (ready) onTap(area); });

  l2d.load({ path: MODEL, logLevel: 'warn' })
    .catch(e => report({ ok: false, error: 'load: ' + String(e) }));

  addEventListener('mousemove', e => {
    const r = canvas.getBoundingClientRect();
    const cx = (e.clientX - r.left) / r.width;
    const cy = (e.clientY - r.top) / r.height;
    const area = ready ? hitAt(cx, cy) : null;
    const hit = !!area || dragOn;
    if (hit !== over) { over = hit; window.pet.setClickThrough(!hit); }  // 状态变化才调
    if (dragOn) { window.pet.dragMove({ screenX: e.screenX, screenY: e.screenY }); return; }
    // 注：视线不再挂在这里 —— 鼠标不在窗口内时收不到 mousemove，
    //     改由主进程全局轮询推送（见 gazeFromCursor）。
  });
  addEventListener('mousedown', e => {
    if (!ready) return;
    const r = canvas.getBoundingClientRect();
    if (hitAt((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height)) {
      dragOn = true;
      window.pet.dragStart({ screenX: e.screenX, screenY: e.screenY });
    }
  });
  addEventListener('mouseup', () => { if (dragOn) { dragOn = false; window.pet.dragEnd(); } });
  addEventListener('contextmenu', e => e.preventDefault());   // 右键归托盘
  window.pet.onScaleChanged(() => fit());
  window.pet.onCursor(d => gazeFromCursor(d));     // 全局光标 → 视线跟随
}

if (typeof L2D === 'undefined') {
  report({ ok: false, error: 'L2D 全局缺失：l2d/dist/index.min.js 未正确加载' });
} else {
  try { boot(); }
  catch (e) { report({ ok: false, error: 'boot: ' + (e && e.stack || e) }); }
}
