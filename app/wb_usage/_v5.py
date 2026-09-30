# -*- coding: utf-8 -*-
"""验证「多 Agent 用量对比」是否随口径变 + 顺手查 today_credit。

上一版诊断写 `window.CHARTS` 读到 undefined —— 因为 `const CHARTS` 属于全局
**词法**环境，不会挂到 window 上。脚本内直接写 `CHARTS` 就能访问。
"""
import io
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DBG = os.path.join(HERE, "_dbg_dash.html")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CHAIN = """
<script>
(function(){
  function bars(){
    try{
      var ch=CHARTS['cAgent'];                 /* 直接引用，别用 window.CHARTS */
      if(!ch) return '无图表';
      var ds=ch.data.datasets[0].data;
      var lb=ch.data.labels;
      return lb.map(function(l,i){return l+':'+ds[i]+'万';}).join(' , ');
    }catch(e){ return 'err:'+e.message; }
  }
  function kpi(){
    var row=document.getElementById('kpiRow');
    var lab=row&&row.querySelector('.kpi .lab');
    var val=row&&row.querySelector('.kpi .val');
    var sub=row&&row.querySelector('.kpi .delta');
    return (lab?lab.textContent:'?')+'='+(val?val.textContent:'?')
      +' ('+(sub?sub.textContent:'?')+')';
  }
  var lines=[];
  setTimeout(function(){
    lines.push('【今天】'+kpi());
    lines.push('   对比图: '+bars());
    document.getElementById('rsTrigger').click();
    setTimeout(function(){
      var b=document.querySelector('[data-r="yesterday"]'); if(b) b.click();
      setTimeout(function(){
        lines.push('【昨天】'+kpi());
        lines.push('   对比图: '+bars());
        document.getElementById('rsTrigger').click();
        setTimeout(function(){
          var c=document.querySelector('[data-r="all"]'); if(c) c.click();
          setTimeout(function(){
            lines.push('【全部】'+kpi());
            lines.push('   对比图: '+bars());
            var d=document.createElement('div');
            d.style.cssText='position:fixed;left:8px;bottom:8px;z-index:9999;background:#fff;'
              +'border:2px solid #B4364F;padding:8px 12px;font:11px monospace;line-height:1.8;max-width:1480px';
            d.textContent=lines.join('\\n');
            document.body.appendChild(d);
          }, 3000);
        }, 500);
      }, 3000);
    }, 500);
  }, 1800);
})();
</script>
"""


class Proxy(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/"):
            try:
                with OP.open("http://127.0.0.1:8801" + self.path, timeout=90) as r:
                    body = r.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(str(e).encode())
        else:
            super().do_GET()

    def log_message(self, *a):
        pass


def main():
    shutil.copyfile(os.path.join(HERE, "dashboard.html"), DBG)
    s = io.open(DBG, encoding="utf-8").read()
    io.open(DBG, "w", encoding="utf-8", newline="\n").write(
        s.replace("</body>", CHAIN + "\n</body>"))
    os.chdir(HERE)
    srv = ThreadingHTTPServer(("127.0.0.1", 8803), Proxy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1)
    out = r"D:\workbuddy\_v5.png"
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--virtual-time-budget=35000", "--window-size=1500,900",
                    "--screenshot=" + out, "http://127.0.0.1:8803/_dbg_dash.html"],
                   capture_output=True, timeout=300)
    srv.shutdown()
    try:
        os.remove(DBG)
    except OSError:
        pass
    print("saved", out)


main()
