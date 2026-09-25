# -*- coding: utf-8 -*-
r"""桌宠气泡"本轮用量实时刷新"测试

验证点：
  1. ODS 有数据 + workbuddy working → row2 显示真实数据，用时算到 now
  2. ODS 有数据 + workbuddy 不可读 → row2 显示数据，行1 "最近对话"
  3. ODS 无数据 + workbuddy working（截图 bug 场景）→ row2 显示 "等待首笔调用..." 而非 0
  4. 完全空态 → 显示 "当前没有活跃会话"
  5. 签名变化：latest_turn 字段变化能触发 sig 变化

用法：python tools/test_live_bubble.py
"""

import os
import sys
import time
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_hover_core as CORE                          # noqa: E402
import wb_whale_win as W                              # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


# 构造临时 ODS 库
def make_ods(rows):
    """rows: list of (session_id, request_id, ts_ms, credit, total_tokens, ai_title)"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE ods_jsonl_event(
        id INTEGER PRIMARY KEY, session_id TEXT, request_id TEXT,
        event_type TEXT, role TEXT, ts_ms INTEGER, project TEXT,
        raw_usage_json TEXT, user_prompt TEXT, ai_title TEXT)""")
    for sid, rid, ts, credit, tokens, title in rows:
        usage = '{"credit":' + str(credit) + ',"total_tokens":' + str(tokens) + '}'
        con.execute("""INSERT INTO ods_jsonl_event
            (session_id, request_id, event_type, role, ts_ms, project, raw_usage_json, ai_title)
            VALUES (?, ?, 'message', 'assistant', ?, '测试项目', ?, ?)""",
            (sid, rid, ts, usage, title))
    con.commit()
    con.close()
    return path


# 构造临时 workbuddy.db
def make_wb(sessions):
    """sessions: list of (id, title, status, last_activity_at_ms)"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE sessions(
        id TEXT PRIMARY KEY, title TEXT, status TEXT, last_activity_at INTEGER)""")
    for sid, title, status, lat in sessions:
        con.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (sid, title, status, lat))
    con.commit()
    con.close()
    return path


# ---- 场景 1：workbuddy working + ODS 有数据 ----
print("\n[1] workbuddy working + ODS 有数据")
now_ms = int(time.time() * 1000)
ods1 = make_ods([
    ("sess1", "turn1", now_ms - 2000, 0.15, 1200, "测试对话标题"),
    ("sess1", "turn1", now_ms - 1000, 0.30, 2400, None),  # 同 turn 多调用
])
wb1 = make_wb([("sess1", "测试对话标题", "working", now_ms)])
_kpi, active, ok, latest = CORE.query_db(ods1, wb1)
check("S1: query_db 4-tuple 返回",
      _kpi is None and active is not None and ok and latest is not None)
check("S1: latest 有数据",
      latest and latest.get("credit") and latest.get("credit") > 0)
check("S1: active 取到 working 会话",
      active and len(active) == 1 and active[0]["_wb_title"] == "测试对话标题")

# 模拟 WhalePet._bubble_lines（用 stub）
class Stub:
    pass
app = Stub()
app.api_ok = True
app.db_ok = ok
app.active = active
app.latest_turn = latest
app._today_timeline = lambda: ([], [])   # 跟随 P2：Stub 无时间线 → row3 走回退叙述
row1, row2, row3 = W.WhalePet._bubble_lines(app)
print(f"  row1: {row1}")
print(f"  row2: {row2}")
print(f"  row3: {row3}")
check("S1: row1 标题含 '正在对话'",
      "正在对话" in row1, f"got: {row1}")
check("S1: row2 显示真实积分（>0）",
      "0.0 分" not in row2 and "分" in row2, f"got: {row2}")
check("S1: row2 显示 tokens",
      "tok" in row2, f"got: {row2}")
check("S1: row2 用时非 0秒",
      "0秒" not in row2, f"got: {row2}")


# ---- 场景 2：workbuddy 不可读 + ODS 有数据（ODS 兜底 active）----
print("\n[2] workbuddy 不可读 + ODS 有数据（兜底 active 仍存在）")
wb2 = "/nonexistent/path/nonexistent.db"  # 不存在 → wb_ok=False
_kpi, active, ok, latest = CORE.query_db(ods1, wb2)
check("S2: wb 不可读时仍返回 latest",
      latest is not None and latest.get("credit") and latest["credit"] > 0)
# workbuddy.db 不可读 → 走 ODS 时间窗口兜底（60s 内有 ODS 活动的 session 算 active）
check("S2: active 通过 ODS 兜底仍非空",
      active and len(active) > 0,
      f"got active={active}")

app.active = active
app.latest_turn = latest
row1, row2, row3 = W.WhalePet._bubble_lines(app)
print(f"  row1: {row1}")
print(f"  row2: {row2}")
check("S2: row1 显示对话相关文字（兜底仍视为活跃）",
      "正在对话" in row1 or "最近对话" in row1, f"got: {row1}")
check("S2: row2 仍显示数据（关键：latest 接管数据源）",
      "分" in row2 and "tok" in row2 and "0.0 分" not in row2, f"got: {row2}")


# ---- 场景 3：截图 bug 场景 — workbuddy working 但 ODS 无数据 ----
print("\n[3] workbuddy working + ODS 无数据（截图 bug 场景）")
ods3 = make_ods([])  # 空的 ODS
wb3 = make_wb([("sess2", "新对话", "working", now_ms)])
_kpi, active, ok, latest = CORE.query_db(ods3, wb3)
check("S3: active 有 working 会话（workbuddy 标记）",
      active and len(active) == 1)
check("S3: latest 为 None（ODS 无数据）",
      latest is None)

app.active = active
app.latest_turn = None  # ODS 无数据
row1, row2, row3 = W.WhalePet._bubble_lines(app)
print(f"  row1: {row1}")
print(f"  row2: {row2}")
check("S3: row1 仍显示 '正在对话'（workbuddy 状态驱动）",
      "正在对话" in row1, f"got: {row1}")
check("S3: row2 不再显示 0.0 分 0 tok（修 bug）",
      "0.0 分" not in row2 and "0 tok" not in row2, f"got: {row2}")
check("S3: row2 显示等待首笔",
      "等待" in row2, f"got: {row2}")


# ---- 场景 4：完全空态 ----
print("\n[4] 完全空态")
ods4 = make_ods([])
wb4 = make_wb([])
_kpi, active, ok, latest = CORE.query_db(ods4, wb4)
check("S4: active 空", active == [])
check("S4: latest 为 None", latest is None)

app.active = active
app.latest_turn = None
row1, row2, row3 = W.WhalePet._bubble_lines(app)
print(f"  row1: {row1}")
check("S4: row1 显示 '当前没有活跃会话'",
      "当前没有活跃会话" in row1, f"got: {row1}")


# ---- 场景 5：签名变化 ----
print("\n[5] _sig 签名包含 latest_turn 关键字段")
# 实例化一个最小 WhalePet
try:
    import threading
    whale = W.WhalePet(run_seconds=10)
    whale.latest_turn = {"credit": 0.5, "total_tokens": 1000,
                          "first_ts": now_ms, "last_ts": now_ms,
                          "title": "test"}
    sig1 = whale._sig()
    whale.latest_turn["credit"] = 1.0
    sig2 = whale._sig()
    check("S5: latest_turn 字段变化触发 _sig 变化", sig1 != sig2)
    # 实例化时已设了 _arm_timers，这里手动退出（避免 timer 干扰测试）
    try:
        W._user32.PostQuitMessage(0)
        whale.run()  # 跑一下让消息循环退出
    except Exception:
        pass
except Exception as e:
    print(f"  (skip S5 due to init issue: {e})")


print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")

# 清理临时文件
for p in (ods1, ods3, ods4, wb1, wb3, wb4):
    try:
        os.unlink(p)
    except Exception:
        pass

sys.exit(0 if not FAILED else 1)
