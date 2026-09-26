// 古见同学 Live2D 桌宠 · 渲染逻辑
// 依赖：l2d（IIFE 全局 L2D）、preload 暴露的 window.pet
const canvas = document.getElementById('cv');
const bubble = document.getElementById('bubble');
const qs = new URLSearchParams(location.search);
const MODEL = qs.get('model') || '../models/Hiyori/Hiyori.model3.json';

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
function gaze(cx, cy) {
  const now = performance.now();
  if (now - lastGaze < 66) return;                 // 15fps 节流，与桌宠气质一致
  lastGaze = now;
  const dx = Math.max(-1, Math.min(1, (cx - 0.5) * 2));
  const dy = Math.max(-1, Math.min(1, (cy - 0.42) * 2));
  l2d.setParams({
    ParamAngleX: dx * 30, ParamAngleY: -dy * 20,
    ParamEyeBallX: dx, ParamEyeBallY: -dy,
  });
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
    if (ready && !dragOn) gaze(cx, cy);
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
}

if (typeof L2D === 'undefined') {
  report({ ok: false, error: 'L2D 全局缺失：l2d/dist/index.min.js 未正确加载' });
} else {
  try { boot(); }
  catch (e) { report({ ok: false, error: 'boot: ' + (e && e.stack || e) }); }
}
