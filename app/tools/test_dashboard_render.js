// 完整验证：语法 + 全流程 + segRange 切换 + 多次 loadAll + 多 agent 切换
//
// 前置：本机跑着看板服务（python app/wb_usage/wb_api.py --port <PORT>）
// 运行（在 app/tools 下）：
//   NODE_PATH=<managed node workspace>/node_modules PORT=8899 node test_dashboard_render.js
//
// PORT 默认 8801。注意 8801 可能被「另一个」看板服务占用（比如旧 skill 的自启服务），
// 那种情况下请求会打到旧服务上、报 unknown api——测试时显式指定一个独占端口更安全。
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const http = require('http');

const PORT = process.env.PORT || '8801';
const ORIGIN = `http://127.0.0.1:${PORT}`;

const HTML = path.join(__dirname, '..', 'wb_usage', 'dashboard.html');
const html = fs.readFileSync(HTML, 'utf-8');
const stripped = html.replace(/<script src="chart\.umd\.min\.js[^<]*<\/script>/, '');

const dom = new JSDOM(stripped, { url: ORIGIN + '/', runScripts:'outside-only', pretendToBeVisual:true });
dom.window.Chart = class { constructor(el,cfg){this.el=el;this.cfg=cfg;} destroy(){} update(){} getDatasetMeta(){return {data:[{x:0}]};} scales={x:{getPixelForValue:()=>0}}; options={scales:{}}; };
dom.window.fetch = (url, opts) => new Promise((resolve, reject) => {
  const u = new URL(url, ORIGIN);
  const req = http.request({ method: opts?.method||'GET', hostname:u.hostname, port:u.port,
    path:u.pathname+u.search, headers:opts?.headers||{} },
    res => { let body=''; res.on('data',c=>body+=c); res.on('end',()=>resolve({
      ok: res.statusCode>=200&&res.statusCode<300, status:res.statusCode,
      json: ()=>Promise.resolve(JSON.parse(body)), text: ()=>Promise.resolve(body) })); });
  req.on('error', reject); if (opts?.body) req.write(opts.body); req.end();
});

let FAIL = [];
function check(name, cond, extra){
  console.log(`  ${cond?'PASS':'FAIL'}  ${name}${cond?'':'  '+ (extra??'')}`);
  if(!cond) FAIL.push(name);
}

const scriptRe = /<script(?:\s+[^>]*)?>([\s\S]*?)<\/script>/g;
let m, scripts = [];
while ((m = scriptRe.exec(stripped))) scripts.push(m[1]);
console.log(`[1] script 段数: ${scripts.length}`);

for (let i=0; i<scripts.length; i++) {
  try { new dom.window.Function(scripts[i]); } catch (e) {
    console.error(`[FAIL] script[${i}] SyntaxError: ${e.message}`); process.exit(1);
  }
}
console.log('[2] 语法校验: PASS');

for (let i=0; i<scripts.length; i++) {
  try { dom.window.eval(scripts[i]); } catch (e) {
    console.error(`[FAIL] script[${i}] runtime: ${e.message}`);
    console.error(e.stack.split('\n').slice(0,8).join('\n')); process.exit(1);
  }
}
console.log('[3] 全部 script eval OK');

const sleep = ms => new Promise(r => setTimeout(r, ms));
/* 轮询等待条件成立：数据库冷查询可能要好几秒（v_call 对 26 万行做 json_extract），
   固定 sleep 会误判「没渲染」。 */
async function waitFor(fn, ms=30000, step=250){
  const t0 = Date.now();
  for(;;){
    try { if (fn()) return true; } catch(e){}
    if (Date.now()-t0 >= ms) return false;
    await sleep(step);
  }
}
let kpiRow, topBody;

setTimeout(async () => {
  const doc = dom.window.document;
  const $ = id => doc.getElementById(id);
  kpiRow = $('kpiRow'); topBody = $('topBody');
  await waitFor(()=>kpiRow.children.length===5);

  console.log('\n--- 首次 loadAll（agent=全部）---');
  check('KPI 卡片数 = 5', kpiRow.children.length===5, kpiRow.children.length);
  check('会话表行数 > 0', topBody.children.length>0, topBody.children.length);
  check('多 Agent 对比卡片存在（cAgent）', !!$('cAgent'));
  check('agent 筛选组存在（segAgent）', !!$('segAgent'));
  const chips = doc.querySelectorAll('#segAgent button');
  check('agent 按钮数 >= 2（全部 + 各源）', chips.length>=2, `实际 ${chips.length}`);
  console.log(`     agent 按钮: ${[...chips].map(b=>b.textContent).join(' / ')}`);
  const rowsWithBadge = [...topBody.children].filter(tr=>/codex|workbuddy/.test(tr.innerHTML)).length;
  check('"全部"时会话行标注了来源', rowsWithBadge>0, `带标记行 ${rowsWithBadge}`);
  check('lastCollect 已更新', /最后采集|切换/.test($('lastCollect').textContent), JSON.stringify($('lastCollect').textContent));

  console.log('\n--- 切到 agent=codex ---');
  const codexBtn = doc.querySelector('#segAgent button[data-a="codex"]');
  if (codexBtn) {
    codexBtn.click();
    await waitFor(()=>{const k=kpiRow.querySelector('.k1'); return k && k.textContent.includes('—');});
    check('KPI 卡片数仍为 5', kpiRow.children.length===5, kpiRow.children.length);
    const k1 = kpiRow.querySelector('.k1');
    const k1txt = k1 ? k1.textContent : '';
    check('无积分口径时积分卡显示「—」而非 0', k1txt.includes('—'), k1txt.slice(0,60));
    check('积分卡文案说明无积分口径', /无积分口径/.test(k1txt), k1txt.slice(0,80));
    const active = doc.querySelector('#segAgent button.active');
    check('codex 按钮变为选中态', active && active.dataset.a==='codex', active && active.dataset.a);
    const mdl = $('mdRangeLabel').textContent;
    check('模型分布标题改用 Token 口径', /Token/.test(mdl), mdl);
    console.log(`     模型分布标题: ${mdl}`);
    check('Codex 会话仍能列出', topBody.children.length>0, topBody.children.length);
  } else {
    check('存在 codex 按钮（本机已采集 Codex）', false, '未找到');
  }

  console.log('\n--- 切回 agent=全部 ---');
  const allBtn = doc.querySelector('#segAgent button[data-a="all"]');
  if (allBtn) {
    allBtn.click();
    await waitFor(()=>{const k=kpiRow.querySelector('.k1'); return k && !k.textContent.includes('—');});
  }
  const k1 = kpiRow.querySelector('.k1');
  check('切回后积分卡恢复数值', k1 && !k1.textContent.includes('—'), k1 && k1.textContent.slice(0,60));

  console.log('\n--- 模拟 segRange 切到 today ---');
  const todayBtn = doc.querySelector('#segRange button[data-r="today"]');
  if (todayBtn) {
    todayBtn.click();
    await sleep(3000);
    check('区间切换后 KPI 卡片数 = 5', kpiRow.children.length===5, kpiRow.children.length);
  }

  console.log('\n--- 模拟点击"刷新"（第二次 loadAll）---');
  try {
    $('btnRefresh').click();
    await waitFor(()=>topBody.children.length>0, 20000);
    check('刷新后 KPI 卡片数 = 5', kpiRow.children.length===5, kpiRow.children.length);
    check('刷新后可重复点击不抛错', topBody.children.length>0, topBody.children.length);
  } catch (e) {
    check('刷新按钮可重复点击，不抛错', false, e.message);
  }

  /* ---------- Agent 接入面板 ---------- */
/* 走同一个 fetch 代理（含 POST body），保证打的是真实服务 */
const fetchJson = async (p) => (await dom.window.fetch(p, {})).json();
/* 注意：接口返回 {ok, data:{agents}} —— 这里要取 .data（前端 api() 会自动剥一层，
   我用的是裸 fetchJson，得自己剥） */
const REG = (await fetchJson('/api/registry')).data;

console.log('\n--- Agent 接入面板 ---');
await waitFor(()=>doc.querySelectorAll('#regList .reg-row').length>0, 20000);
const rows=doc.querySelectorAll('#regList .reg-row');
check('面板渲染出全部登记项', rows.length===REG.agents.length,
      `UI ${rows.length} vs 接口 ${REG.agents.length}`);
const uiOn=[...rows].filter(r=>!r.classList.contains('off')).length;
const apiOn=REG.agents.filter(a=>a.enabled).length;
check('启用态与接口一致', uiOn===apiOn, `UI ${uiOn} vs 接口 ${apiOn}`);
check('每行都有开关', doc.querySelectorAll('#regList input[data-reg]').length===rows.length);
check('未验证项带警示徽章',
      [...rows].some(r=>r.textContent.includes('未验证')) ===
      REG.agents.some(a=>!a.verified));
check('备注过长时以省略号结尾',
      [...doc.querySelectorAll('#regList .reg-note')]
        .filter(e=>(e.getAttribute('title')||'').length>52)
        .every(e=>e.textContent.endsWith('…')));

/* 真实开关往返：挑一个已启用的 agent 关掉再开回来，验证后端写入 + UI 同步。
   这是唯一会改配置的接口，值得端到端跑一次（跑完必须还原）。 */
const target=REG.agents.find(a=>a.enabled && a.verified);
if(target){
  const r=await fetch(ORIGIN+'/api/registry',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:target.key,enabled:false})}).then(r=>r.json());
  check(`关掉 ${target.key} 后端接受`, r.ok===true && r.data.enabled===false);
  const after=(await fetchJson('/api/registry')).data;
  check('接口回读为已关闭',
        after.agents.find(a=>a.key===target.key).enabled===false);
  const back=await fetch(ORIGIN+'/api/registry',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:target.key,enabled:true})}).then(r=>r.json());
  check('还原成功', back.ok===true && back.data.enabled===true);
  const bad=await fetch(ORIGIN+'/api/registry',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:'__not_registered__',enabled:true})}).then(r=>r.json());
  check('未登记的 key 被拒', bad.ok===false);
}

console.log('\n=== 总结 ===');
  if (FAIL.length) { console.log(`❌ 失败 ${FAIL.length} 项: ${FAIL.join(' | ')}`); process.exit(1); }
  console.log('✅ 看板 JS 流程全部正常（含多 agent 切换）');
  process.exit(0);
}, 20000);
