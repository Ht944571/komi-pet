# -*- coding: utf-8 -*-
r"""Agent 接入 T1/T2/T3 专项测试（目标任务文档 §6）

覆盖：
  A. T3「只记计数不存原文」开关：关 → 跳过原文归档、消费层照常、cost_usd 入库
  B. T1 L2 日志活动度：log_activity_age 三值语义 + 缓存
  C. T1 三级降级：detect_detail 按最强可用层归级（hook > 日志 > 进程/端口）
  D. T1 登记册健康检查：路径存在/全缺/无声明 三值 + 缓存
  E. T2 ccusage 适配器：字段映射 / 幂等 / 登记 key / 无 Node 零影响 / 成本入库

用法：python tools/test_agent_access.py
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "wb_usage"))

import wb_collect as C                        # noqa: E402
import wb_agent_registry as REG               # noqa: E402
import wb_agent_presence as P                 # noqa: E402
import agents.ccusage as CC                   # noqa: E402
from agents.base import AgentSource           # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


class _FakeSrc:
    key = "workbuddy"

    def project_of(self, cwd):
        return "proj" if cwd else ""


NOW = time.time()
DAY = 86400.0

# ===== A. T3 raw 归档开关 + cost_usd =====
print("\n[A] T3：只记计数不存原文（开关）+ cost_usd 入库")
tmpdb = os.path.join(tempfile.gettempdir(), "komi-t3-raw.db")
for suf in ("", "-wal", "-shm"):
    if os.path.exists(tmpdb + suf):
        os.remove(tmpdb + suf)
conn = C.get_conn(tmpdb)
check("A0: 默认开启原文归档", C.raw_archive_enabled(conn) is True)
rows = [dict(AgentSource.base_row(7, id="r7", event_type="assistant", role="ai",
                                    session_id="s", ts_ms=int(NOW * 1000) - int(DAY * 1000 * 3),
                                    cwd="d:/proj", model="m", raw_usage_json='{"total_tokens":10}'),
             _raw={"note": "should-not persist when off"},
             cost_usd=0.42)]
n = C.insert_rows(conn, "t3://x", _FakeSrc(), rows)
check("A1: 开启时事件+原文都落",
      conn.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0] == 1
      and conn.execute("SELECT COUNT(*) FROM ods_jsonl_raw").fetchone()[0] == 1)
C.meta_set(conn, "raw_archive", "0")
conn.commit()
check("A2: 开关读回 off", C.raw_archive_enabled(conn) is False)
rows2 = [dict(AgentSource.base_row(8, id="r8", event_type="assistant", role="ai",
                                     session_id="s", ts_ms=int(NOW * 1000) - int(DAY * 1000),
                                     cwd="d:/proj", model="m",
                                     raw_usage_json='{"total_tokens":5}'),
              _raw={"note": "skip me"}),
         dict(AgentSource.base_row(9, id="r9", event_type="assistant", role="ai",
                                     session_id="s", ts_ms=int(NOW * 1000),
                                     cwd="d:/proj", model="m",
                                     raw_usage_json='{"total_tokens":3}'),
              cost_usd=1.25)]
n = C.insert_rows(conn, "t3://y", _FakeSrc(), rows2)
check("A3: 关闭后事件照常落（2 行）", n == 2
      and conn.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0] == 3)
check("A4: 关闭后原文归档零增长（T3 验收 ②：只少原文展示）",
      conn.execute("SELECT COUNT(*) FROM ods_jsonl_raw").fetchone()[0] == 1)
check("A5: cost_usd 入库（T2 schema 扩展）",
      conn.execute("SELECT cost_usd FROM ods_jsonl_event WHERE id='r9'").fetchone()[0] == 1.25)
C.meta_set(conn, "raw_archive", "1")
conn.close()
for suf in ("", "-wal", "-shm"):
    if os.path.exists(tmpdb + suf):
        os.remove(tmpdb + suf)

# ===== B. T1 L2 日志活动度 =====
print("\n[B] T1：log_activity_age 三值语义")
tmpdir = tempfile.mkdtemp(prefix="komi-t1-")
fresh = os.path.join(tmpdir, "fresh.jsonl")
open(fresh, "w").write("{}")
old = os.path.join(tmpdir, "old.jsonl")
open(old, "w").write("{}")
os.utime(old, (NOW - 2 * DAY, NOW - 2 * DAY))
miss = os.path.join(tmpdir, "no-such-*.jsonl")
check("B1: 刚写入的日志 → age 很小",
      P.log_activity_age([fresh], now=NOW) is not None
      and P.log_activity_age([fresh], now=NOW) < 60)
check("B2: 两天前的日志 → age ≈ 2 天",
      abs(P.log_activity_age([old], now=NOW) - 2 * DAY) < 5)
check("B3: 无匹配文件 → None（无法判定，不装懂）",
      P.log_activity_age([miss], now=NOW) is None)
check("B4: 多模式取最近",
      P.log_activity_age([old, fresh], now=NOW) < 60)
check("B5: 空声明 → None", P.log_activity_age([], now=NOW) is None)

# ===== C. T1 三级降级 =====
print("\n[C] T1：detect_detail 按最强可用层归级")
# L1 hook：新鲜心跳文件
hook = os.path.join(tmpdir, "hook.heartbeat")
open(hook, "w").write("1")
os.utime(hook, (NOW - 10, NOW - 10))
# L3 port：本机起一个监听 socket（backlog 给足：后续多次探活 connect 都要能进队列）
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(50)
port = srv.getsockname()[1]
for _ in range(20):                 # 等 listen 真正就绪（Windows 上立即 connect 可能被拒）
    if P._port_open(port):
        break
    time.sleep(0.1)
stale_log = os.path.join(tmpdir, "stale.jsonl")
open(stale_log, "w").write("{}")
os.utime(stale_log, (NOW - 2 * DAY, NOW - 2 * DAY))

specs = {
    "a_hook": {"procs": [], "ports": [], "hook_files": [hook], "log_paths": []},
    "b_log": {"procs": [], "ports": [], "hook_files": [], "log_paths": [fresh]},
    "c_port": {"procs": [], "ports": [port], "hook_files": [], "log_paths": []},
    "d_stale": {"procs": [], "ports": [], "hook_files": [],
                "log_paths": [stale_log]},
    "e_none": {"procs": [], "ports": [], "hook_files": [], "log_paths": []},
}
d = P.detect_detail(specs, now=NOW)
check("C1: 新鲜 hook → level 1（最强信号）",
      d.get("a_hook", {}).get("level") == P.LEVEL_HOOK)
check("C2: 日志新鲜（无探针）→ level 2（CLI 型 agent 的补洞路径）",
      d.get("b_log", {}).get("level") == P.LEVEL_LOG)
check("C3: 端口在听 → level 3", d.get("c_port", {}).get("level") == P.LEVEL_PROC)
check("C4: 日志陈旧且无探针 → 不活跃（不误判）", "d_stale" not in d)
check("C5: 什么都没有 → 不活跃", "e_none" not in d)
check("C6: L2 附带 age（忙碌时长语义）",
      isinstance(d.get("b_log", {}).get("age"), float))
check("C7: hook 新鲜 + 端口在跑 → 归级 1（最强可用层，不叠加）",
      P.detect_detail({**specs, "a_hook": {**specs["a_hook"], "ports": [port]}},
                      now=NOW).get("a_hook", {}).get("level") == P.LEVEL_HOOK)
# 旧式显式 probes → 只做 L3（向后兼容）
old_style = P.detect_once({"c_port": {"procs": [], "ports": [port]}})
check("C8: detect_once 旧式调用仍只做 L3", old_style == {"c_port"})
srv.close()

# ===== D. T1 登记册健康检查 =====
print("\n[D] T1：登记册 health 三值")
reg_tmp = os.path.join(tmpdir, "_agents.json")
json.dump({"agents": {
    "ok_agent": {"label": "A", "config_paths": [fresh]},
    "gone_agent": {"label": "B", "config_paths": [os.path.join(tmpdir, "nope.json")]},
    "nopaths_agent": {"label": "C", "config_paths": [], "log_paths": []},
}}, open(reg_tmp, "w", encoding="utf-8"))
saved_reg, saved_cache = REG.REGISTRY, REG._cache
REG.REGISTRY, REG._cache = reg_tmp, None
try:
    h = REG.health(force=True)
    check("D1: 路径在 → ok", h["ok_agent"]["health"] == "ok")
    check("D2: 路径全缺 → unavailable（面板标「不可用」的依据）",
          h["gone_agent"]["health"] == "unavailable")
    check("D3: 无路径声明 → unknown（不冤枉）",
          h["nopaths_agent"]["health"] == "unknown")
    check("D4: 附带命中计数", h["ok_agent"]["paths_found"] == 1
          and h["gone_agent"]["paths_found"] == 0)
finally:
    REG.REGISTRY, REG._cache = saved_reg, saved_cache

# ===== E. T2 ccusage 适配器 =====
print("\n[E] T2：ccusage 覆盖面补充源")
entry = {"date": "2026-09-24", "inputTokens": 100, "outputTokens": 40,
         "cacheCreationTokens": 10, "cacheReadTokens": 50,
         "reasoningOutputTokens": 6, "totalTokens": 200,
         "costUSD": 0.5, "models": ["gpt-x"]}
row = CC._row_of("claude", "claude-code", entry)
check("E1: prompt = input+cacheRead+cacheCreation（任务文档映射）",
      row["raw_usage_json"] and json.loads(row["raw_usage_json"])["prompt_tokens"] == 160)
u = json.loads(row["raw_usage_json"])
check("E2: completion/thinking/total 映射", u["completion_tokens"] == 40
      and u["completion_thinking_tokens"] == 6 and u["total_tokens"] == 200)
check("E3: cost_usd 随行", row["cost_usd"] == 0.5)
check("E4: line_no 稳定（幂等）",
      row["line_no"] == CC._stable_line_no("ccusage:claude:2026-09-24"))
import time as _t
_noon = _t.mktime((2026, 9, 24, 12, 0, 0, 0, 0, -1)) * 1000   # 固定日期正午（本地）
check("E5: 日期 → 当日中午 ts（确定性比较）",
      abs(row["ts_ms"] - _noon) < 60 * 1000)

cdir = os.path.join(tmpdir, "ccusage-cache")
os.makedirs(cdir, exist_ok=True)
json.dump({"fetched_at": NOW, "daily": [entry]},
          open(os.path.join(cdir, "claude.json"), "w"))
saved = (CC.CACHE_DIR, CC._node_available, CC._source_enabled, CC._refresher)
CC.CACHE_DIR = cdir
CC._node_available = lambda: True
CC._source_enabled = lambda k: True
CC._refresher = type("_R", (), {"ensure_started": lambda self: None})()
try:
    src = CC.CcusageSource()
    tdb = os.path.join(tempfile.gettempdir(), "komi-t2-cc.db")
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(tdb + suf):
            os.remove(tdb + suf)
    tconn = C.get_conn(tdb)
    got = []

    def fake_insert(path, src_obj, rows):
        got.append((path, rows))
        return C.insert_rows(tconn, path, src_obj, rows)

    n = src.collect(tconn, tdb, fake_insert)
    check("E6: 缓存 → 规范行落库（agent=登记 key）",
          n == 1 and got[0][0] == "ccusage://claude/daily"
          and tconn.execute("SELECT agent FROM ods_jsonl_event").fetchone()[0] == "claude-code")
    check("E7: 幂等（重采不重复）", src.collect(tconn, tdb, fake_insert) == 0
          and tconn.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0] == 1)
    check("E8: cost_usd 落库（成本维度）",
          tconn.execute("SELECT cost_usd FROM ods_jsonl_event").fetchone()[0] == 0.5)
    CC._node_available = lambda: False
    check("E9: 无 Node → collect 返回 0（主链路零影响）",
          src.collect(tconn, tdb, fake_insert) == 0)
    check("E10: 无 Node → available()=False（整源跳过）", src.available() is False)
    tconn.close()
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(tdb + suf):
            os.remove(tdb + suf)
finally:
    CC.CACHE_DIR, CC._node_available, CC._source_enabled, CC._refresher = saved

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
