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

# ===== F. 库型源：opencode（SQLite）=====
# 2026-09-30：看板上「opencode 无用量数据」→ 它的用量在 ~/.local/share/opencode/opencode.db
# 的 session_message（assistant 消息 data.tokens）。夹具库照实机 schema 造。
print("\n[F] 库型源：opencode（SQLite）")
import sqlite3 as _sq                          # noqa: E402
from agents.opencode import OpenCodeSource as _OC   # noqa: E402

_ocdb = os.path.join(tmpdir, "opencode-fixture.db")
if os.path.exists(_ocdb):
    os.remove(_ocdb)
_c = _sq.connect(_ocdb)
_c.executescript("""
CREATE TABLE project(id TEXT PRIMARY KEY, worktree TEXT);
CREATE TABLE session_v2(id TEXT PRIMARY KEY, project_id TEXT, title TEXT,
                        model TEXT, directory TEXT, time_created INTEGER);
CREATE TABLE session_message(id TEXT PRIMARY KEY, session_id TEXT, type TEXT,
                             seq INTEGER, time_created INTEGER, data TEXT);
""")
_c.execute("INSERT INTO project VALUES('p1','C:/work/demo-proj')")
_c.execute("INSERT INTO session_v2 VALUES('ses_1','p1','问个项目问题',"
           "'{\"id\":\"m-x\",\"providerID\":\"prov\"}',NULL,1790700000000)")
_msgs = [
    ("m_u1", "user", 1, {"text": "帮我写个函数", "time": {"created": 1790700001000}}),
    ("m_a1", "assistant", 2, {"time": {"created": 1790700002000},
                              "model": {"id": "m-x", "providerID": "prov"},
                              "cost": 0,
                              "tokens": {"input": 1000, "output": 50, "reasoning": 10,
                                         "cache": {"read": 800, "write": 20}}}),
    ("m_idle", "idle", 3, {"time": {"created": 1790700003000}, "outcome": "succeeded"}),
    ("m_u2", "user", 4, {"text": "再改一下", "time": {"created": 1790700004000}}),
    ("m_a2", "assistant", 5, {"time": {"created": 1790700005000},
                              "model": {"id": "m-x", "providerID": "prov"},
                              "tokens": {"input": 2000, "output": 60, "reasoning": 0,
                                         "cache": {"read": 0, "write": 0}}}),
]
for _mid, _t, _seq, _d in _msgs:
    _c.execute("INSERT INTO session_message VALUES(?,?,?,?,?,?)",
               (_mid, "ses_1", _t, _seq, _d["time"]["created"],
                json.dumps(_d, ensure_ascii=False)))
_c.commit()
_c.close()

oc = _OC()                                     # 夹具库替换掉真实路径
oc.db_path = lambda: _ocdb
oc.root = lambda: tmpdir
check("F1: available() 认数据库文件", oc.available() is True)
check("F2: change_hint() 是两张表的行号指纹", isinstance(oc.change_hint(), str)
      and oc.change_hint().startswith("5:"), oc.change_hint())

_tdb = os.path.join(tmpdir, "komi-oc-test.db")
for _suf in ("", "-wal", "-shm"):
    if os.path.exists(_tdb + _suf):
        os.remove(_tdb + _suf)
_tconn = C.get_conn(_tdb)
_got = []


def _fake_insert(conn, path, src_obj, rows):        # 库型源的 insert_rows 是 4 参
    _got.append((path, rows))
    return C.insert_rows(conn, path, src_obj, rows)


n_oc = oc.collect(_tconn, _tdb, _fake_insert)
u1 = json.loads(_tconn.execute(
    "SELECT raw_usage_json FROM ods_jsonl_event WHERE id='opencode:m_a1'").fetchone()[0])
check("F3: assistant 的 tokens → 规范用量",
      u1["prompt_tokens"] == 1000 and u1["completion_tokens"] == 50
      and u1["total_tokens"] == 1060 and u1["prompt_cache_hit_tokens"] == 800
      and u1["prompt_cache_write_tokens"] == 20
      and u1["completion_thinking_tokens"] == 10, json.dumps(u1))
check("F4: 轮次按「最近一条用户提问」聚合（两条 assistant 分属两轮）",
      _tconn.execute("SELECT COUNT(DISTINCT request_id) FROM ods_jsonl_event "
                     "WHERE event_type='usage'").fetchone()[0] == 2)
check("F5: 用户提问单独成行（气泡/时间线要用）",
      _tconn.execute("SELECT user_prompt FROM ods_jsonl_event "
                     "WHERE event_type='message' AND role='user'").fetchone()[0] == "帮我写个函数")
check("F6: cwd 取自 project.worktree（项目名跟着派生）",
      _tconn.execute("SELECT cwd, project FROM ods_jsonl_event "
                     "WHERE id='opencode:m_a1'").fetchone()[:] == ("C:/work/demo-proj", "demo-proj"))
check("F7: agent 列 = 登记 key", _tconn.execute(
    "SELECT DISTINCT agent FROM ods_jsonl_event").fetchone()[0] == "opencode")
check("F8: 幂等（重采不重复）",
      oc.collect(_tconn, _tdb, _fake_insert) == 0
      and _tconn.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0] == 4)
_tconn.close()
for _suf in ("", "-wal", "-shm"):
    if os.path.exists(_tdb + _suf):
        os.remove(_tdb + _suf)

# ===== G. 整文件 JSON 源：qwen-code =====
# 2026-09-30：本机 ~/.qwen 有目录结构但没有会话文件 → 用**合成样本**（Gemini CLI 系格式）
# 验证解析逻辑；认不出来的文件必须**不出行**（宁缺勿错）。
print("\n[G] 整文件 JSON 源：qwen-code（合成样本）")
from agents.qwen_code import QwenCodeSource as _QW      # noqa: E402

_qroot = os.path.join(tmpdir, "qwen-home")
_cdir = os.path.join(_qroot, "projects", "c--work-demo-proj", "chats")
os.makedirs(_cdir, exist_ok=True)
json.dump({"schema_version": 1, "pid": 1, "session_id": "sess-q1",
           "work_dir": "C:/work/demo-proj", "started_at": 1790700000.0},
          open(os.path.join(_cdir, "sess-q1.runtime.json"), "w"))
json.dump({"sessionId": "sess-q1", "messages": [
    {"type": "user", "timestamp": "2026-09-30T10:00:00Z",
     "content": [{"text": "帮我看下这个报错"}]},
    {"type": "gemini", "model": "qwen3-coder",
     "timestamp": "2026-09-30T10:00:05Z",
     "tokens": {"input": 500, "output": 80, "cached": 300, "thoughts": 20, "total": 600}},
    {"type": "gemini", "model": "qwen3-coder", "timestamp": "2026-09-30T10:00:09Z",
     "tokens": {"input": 0, "output": 0}},                       # 全 0 → 不出行
]}, open(os.path.join(_cdir, "sess-q1.json"), "w"), ensure_ascii=False)
# 一个"认不出来"的会话（老版本/别的格式）：必须是零行
json.dump({"sessionId": "sess-q2", "messages": [{"type": "gemini", "foo": "bar"}]},
          open(os.path.join(_cdir, "sess-q2.json"), "w"))

qw = _QW()
qw.root = lambda: _qroot
check("G1: available() 认 chats 目录", qw.available() is True)
check("G2: change_hint() 跟随文件数与 mtime",
      (qw.change_hint() or "").startswith("2:"), qw.change_hint())
_tdb2 = os.path.join(tmpdir, "komi-qw-test.db")
for _suf in ("", "-wal", "-shm"):
    if os.path.exists(_tdb2 + _suf):
        os.remove(_tdb2 + _suf)
_tconn2 = C.get_conn(_tdb2)
def _ins2(conn, path, src_obj, rows):
    return C.insert_rows(conn, path, src_obj, rows)


n_qw = qw.collect(_tconn2, _tdb2, _ins2)
_qrow = _tconn2.execute("SELECT raw_usage_json, cwd, model, request_id FROM ods_jsonl_event "
                        "WHERE event_type='usage'").fetchone()
_qu = json.loads(_qrow[0]) if _qrow else {}
check("G3: tokens 块映射（input/output/cached/thoughts）",
      n_qw == 2 and _qu.get("prompt_tokens") == 500 and _qu.get("completion_tokens") == 80
      and _qu.get("total_tokens") == 600 and _qu.get("prompt_cache_hit_tokens") == 300
      and _qu.get("completion_thinking_tokens") == 20, f"n={n_qw} u={_qu}")
check("G4: cwd 取自同目录 runtime.json 的 work_dir（项目名派生）",
      _qrow and _qrow[1] == "C:/work/demo-proj"
      and _tconn2.execute("SELECT project FROM ods_jsonl_event WHERE event_type='usage'"
                          ).fetchone()[0] == "demo-proj", _qrow and _qrow[1])
check("G5: 全 0 / 认不出的消息不出行（宁缺勿错）",
      _tconn2.execute("SELECT COUNT(*) FROM ods_jsonl_event WHERE session_id='sess-q2'"
                      ).fetchone()[0] == 0)
check("G6: 幂等（重采不重复）",
      qw.collect(_tconn2, _tdb2, _ins2) == 0
      and _tconn2.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0] == 2)
_tconn2.close()
for _suf in ("", "-wal", "-shm"):
    if os.path.exists(_tdb2 + _suf):
        os.remove(_tdb2 + _suf)

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
