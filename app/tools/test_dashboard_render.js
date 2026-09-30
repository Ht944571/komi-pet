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
/* 记录每次 new 出来的图表实例（含最新一版 cfg）——单日/多日口径要检查轴标签与数据集 */
const SEEN = [];
dom.window.Chart = class { constructor(el,cfg){this.el=el;this.cfg=cfg;SEEN.push(this);} destroy(){} update(){} getDatasetMeta(){return {data:[{x:0}]};} scales={x:{getPixelForValue:()=>0}}; options={scales:{}}; };
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
  /* 2026-09-29 清理三条失效断言（留着只会一直假红）：
     ① "会话行标注了来源"——会话行来源徽章功能从未上线（dashboard 无此 DOM）；
     ② "lastCollect 已更新"——切换耗时播报已按开发者信息有意移除（只剩清空）；
     ③ "Codex 会话仍能列出"——与空数据场景冲突（今天无 Codex 会话时空表是正确行为），
        KPI/图表断言已覆盖切换正确性。 */

  console.log('\n--- 切到 agent=codex ---');
  const codexBtn = doc.querySelector('#segAgent button[data-a="codex"]');
  if (codexBtn) {
    codexBtn.click();
    /* 2026-09-28 起无积分口径的第一张卡是「计费方式」说明卡（不再是写「—」的积分卡）；
       该卡 cls 是 k2，按「第一张 KPI 卡」定位而非 .k1 类 */
    await waitFor(()=>{const k=kpiRow.children[0]; return k && k.textContent.includes('计费方式');});
    check('KPI 卡片数仍为 5', kpiRow.children.length===5, kpiRow.children.length);
    const k1 = kpiRow.children[0];
    const k1txt = k1 ? k1.textContent : '';
    check('无积分口径时第一张卡为「计费方式」', k1txt.includes('计费方式') && k1txt.includes('Token'), k1txt.slice(0,60));
    check('计费方式卡说明按 Token 计费', /按 Token 计费/.test(k1txt), k1txt.slice(0,80));
    const active = doc.querySelector('#segAgent button.active');
    check('codex 按钮变为选中态', active && active.dataset.a==='codex', active && active.dataset.a);
    const mdl = $('mdRangeLabel').textContent;
    check('模型分布标题改用 Token 口径', /Token/.test(mdl), mdl);
    console.log(`     模型分布标题: ${mdl}`);
  } else {
    check('存在 codex 按钮（本机已采集 Codex）', false, '未找到');
  }

  console.log('\n--- 切回 agent=全部 ---');
  const allBtn = doc.querySelector('#segAgent button[data-a="all"]');
  if (allBtn) {
    allBtn.click();
    /* 2026-09-29 起全部=混合口径按 Token：第一张卡是「Token / 轮次」均耗卡 */
    await waitFor(()=>{const k=kpiRow.querySelector('.k1'); return k && k.textContent.includes('Token / 轮次');});
  }
  const k1 = kpiRow.querySelector('.k1');
  check('切回全部后第一张卡为「Token / 轮次」', k1 && k1.textContent.includes('Token / 轮次'), k1 && k1.textContent.slice(0,60));

  console.log('\n--- 模拟切换时间区间（下拉里的预设按钮）---');
  /* 区间选择器 = 「下拉 + 双月日历」；⚠️ 列表是**打开时才渲染**的（openRangeSel 里
     buildRangeList），所以必须先点触发器再点条目 —— 直接找 #rsList 会是空的。 */
  const pickRange = async (k) => {
    const trig = $('rsTrigger');
    if (trig) trig.click();
    await sleep(150);
    const b = doc.querySelector(`#rsList button[data-r="${k}"]`);
    if (!b) {
      console.log('     ⚠️ 找不到区间按钮', k, '现有:',
                  [...doc.querySelectorAll('#rsList button')].map(x=>x.dataset.r).join(',') || '(空)');
      return false;
    }
    b.click();
    await sleep(1200);
    return true;
  };
  const okToday = await pickRange('today');
  check('能切到「今天」（下拉可用）', okToday);
  if (okToday) {
    check('区间切换后 KPI 卡片数 = 5', kpiRow.children.length===5, kpiRow.children.length);
  }

  /* ---------- 单日口径：日趋势 / 命中率趋势按小时铺 00:00–24:00 ---------- */
  /* 需求（2026-09-29）：区间是一天（今天/昨天/自定义里的一天）时，横轴要按小时铺满
     00:00–24:00；多日仍按天（MM-DD）。这里用真接口跑：既验前端标签/数据集，
     也顺带验后端 /api/daily?gran=hour。 */
  console.log('\n--- 单日口径：横轴按小时 00:00–24:00 ---');
  const latestChart = id => [...SEEN].reverse().find(c=>c.el && c.el.id===id);
  await waitFor(()=>{
    const t = latestChart('cTrend');
    return t && t.cfg.data.labels[0] === '00:00';
  }, 30000);
  const t1 = latestChart('cTrend'), h1 = latestChart('cHit');
  const labs1 = t1 ? t1.cfg.data.labels : [];
  check('单日：日趋势横轴 = 00:00 … 24:00（25 个刻度）',
        labs1[0]==='00:00' && labs1[labs1.length-1]==='24:00' && labs1.length===25,
        `got ${labs1.length}: ${labs1.slice(0,3).join(',')} … ${labs1.slice(-2).join(',')}`);
  check('单日：刻度是整点且连续（00,01,…）',
        labs1.slice(0,4).join('|')==='00:00|01:00|02:00|03:00', labs1.slice(0,4).join('|'));
  const tok1 = t1 && t1.cfg.data.datasets.find(x=>/Tokens/.test(x.label));
  check('单日：24:00 那个刻度没有数据（null，不是 0 —— 否则末尾像暴跌）',
        !!tok1 && tok1.data[tok1.data.length-1] === null,
        tok1 && JSON.stringify(tok1.data.slice(-3)));
  check('单日：不再画「日均」参考线（单日日均恒等于当日）',
        !!t1 && !t1.cfg.data.datasets.some(x=>/日均/.test(x.label)),
        t1 && t1.cfg.data.datasets.map(x=>x.label).join(','));
  const hitLabs = h1 ? h1.cfg.data.labels : [];
  check('单日：命中率趋势横轴同样是 00:00 … 24:00',
        hitLabs[0]==='00:00' && hitLabs[hitLabs.length-1]==='24:00' && hitLabs.length===25,
        `got ${hitLabs.length}`);
  check('单日：命中率趋势也不画平均线',
        !!h1 && !h1.cfg.data.datasets.some(x=>/平均/.test(x.label)),
        h1 && h1.cfg.data.datasets.map(x=>x.label).join(','));

  console.log('\n--- 多日口径：横轴回到「天」（MM-DD）---');
  const ok7 = await pickRange('7');
  check('多日口径：能切到「近 7 天」', ok7);
  if (ok7) {
    await waitFor(()=>{
      const t = latestChart('cTrend');
      return t && /^\d\d-\d\d$/.test(t.cfg.data.labels[0]||'');
    }, 30000);
    const t2 = latestChart('cTrend');
    check('多日：横轴是 MM-DD 而不是小时',
          !!t2 && /^\d\d-\d\d$/.test(t2.cfg.data.labels[0]),
          t2 && t2.cfg.data.labels.slice(0,3).join(','));
    check('多日：日均参考线回来了',
          !!t2 && t2.cfg.data.datasets.some(x=>/日均/.test(x.label)),
          t2 && t2.cfg.data.datasets.map(x=>x.label).join(','));
  }
  await pickRange('today');

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
