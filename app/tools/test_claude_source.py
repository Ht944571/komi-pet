# -*- coding: utf-8 -*-
r"""Claude Code 数据源测试（2026-09-29 接入）

验证点：
  A. assistant 用量行：五分量映射、总输入口径（input+cache_read+cache_creation）、
     时间戳 ISO8601、requestId 回退
  B. 全缓存命中（input_tokens=0）不丢行（视图 prompt_tokens>0 的规避——总输入口径）
  C. API 报错行 / 空用量行跳过
  D. user 行：str 与 text 段数组两种形态；tool_result / isMeta 跳过
  E. summary 行 → ai-title 元数据
  F. 注册与门控：SOURCES 里有 claude-code；本机未装时 available()=False
  G. 触发链端到端：ODS 近窗并入不分 agent——claude-code 的 usage 行进 ODS
     即被 _active_sessions 数为活跃（桌宠写字态判据）

用法：python tools/test_claude_source.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "wb_usage"))

import wb_hover_core as CORE                          # noqa: E402
from agents import SOURCES, BY_KEY, get_source        # noqa: E402
from agents.claude_code import ClaudeCodeSource       # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def row_of(line, line_no=1, path="p.jsonl"):
    src = ClaudeCodeSource()
    rows = src.parse_lines(path, [(line_no, line)])
    return rows[0] if rows else None


def assistant_line(usage, **over):
    d = {"type": "assistant",
         "sessionId": "sess-aaa", "cwd": "D:/proj", "uuid": "u1",
         "requestId": "req_001", "timestamp": "2026-09-29T00:30:00.000Z",
         "message": {"id": "msg_01", "model": "claude-sonnet-4-6",
                     "role": "assistant",
                     "usage": {"input_tokens": 4, "output_tokens": 301,
                               "cache_creation_input_tokens": 2215,
                               "cache_read_input_tokens": 12345}}}
    d["message"]["usage"].update(usage)
    d.update(over)
    return json.dumps(d, ensure_ascii=False)


NOW_MS = int(time.time() * 1000)

print("\n[A] assistant 用量行映射")
r = row_of(assistant_line({}))
u = json.loads(r["raw_usage_json"])
check("A1: prompt_tokens=总输入(4+2215+12345)",
      u["prompt_tokens"] == 4 + 2215 + 12345, f"got {u.get('prompt_tokens')}")
check("A2: 缓存两分量正确",
      u["prompt_cache_hit_tokens"] == 12345
      and u["prompt_cache_write_tokens"] == 2215, f"got {u}")
check("A3: total_tokens=总输入+输出",
      u["total_tokens"] == 4 + 2215 + 12345 + 301, f"got {u.get('total_tokens')}")
check("A4: ISO8601 时间戳解析为毫秒（UTC 语义）",
      r["ts_ms"] == int(datetime(2026, 9, 29, 0, 30, tzinfo=timezone.utc)
                        .timestamp() * 1000), f"got {r['ts_ms']}")
check("A5: session/cwd/model/requestId",
      r["session_id"] == "sess-aaa" and r["cwd"] == "D:/proj"
      and r["model"] == "claude-sonnet-4-6" and r["request_id"] == "req_001")

print("\n[B] 全缓存命中不丢行（input=0）")
r = row_of(assistant_line({"input_tokens": 0, "output_tokens": 50}))
u = json.loads(r["raw_usage_json"])
check("B1: 行保留且 prompt_tokens=缓存和",
      r is not None and u["prompt_tokens"] == 2215 + 12345, f"got {u}")

print("\n[C] 非真实调用跳过")
check("C1: API 报错行跳过",
      row_of(assistant_line({}, isApiErrorMessage=True)) is None)
check("C2: 全零用量行跳过",
      row_of(assistant_line({"input_tokens": 0, "output_tokens": 0,
                             "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 0})) is None)

print("\n[D] user 行")
check("D1: 字符串 content 提取",
      row_of(json.dumps({"type": "user", "sessionId": "s1", "cwd": "D:/p",
                         "timestamp": "2026-09-29T00:30:01Z",
                         "message": {"role": "user", "content": "帮我修 bug"}}))
      ["user_prompt"] == "帮我修 bug")
check("D2: text 段数组提取",
      row_of(json.dumps({"type": "user", "sessionId": "s1",
                         "message": {"role": "user", "content": [
                             {"type": "text", "text": "第一段"},
                             {"type": "text", "text": "第二段"}]}}))
      ["user_prompt"] == "第一段\n第二段")
check("D3: 纯 tool_result 行跳过",
      row_of(json.dumps({"type": "user", "sessionId": "s1",
                         "message": {"role": "user", "content": [
                             {"type": "tool_result", "content": "ok"}]}})) is None)
check("D4: isMeta 系统注入跳过",
      row_of(json.dumps({"type": "user", "isMeta": True, "sessionId": "s1",
                         "message": {"role": "user", "content": "注入"}})) is None)

print("\n[E] summary 标题行")
r = row_of(json.dumps({"type": "summary", "summary": "修菜单美术",
                       "leafUuid": "sess-aaa"}), line_no=9)
check("E1: ai-title 事件 + 标题 + 会话指向",
      r is not None and r["event_type"] == "ai-title"
      and r["ai_title"] == "修菜单美术" and r["session_id"] == "sess-aaa")

print("\n[F] 注册与门控")
check("F1: SOURCES 已注册 claude-code",
      "claude-code" in BY_KEY and get_source("claude-code") is not None)
src = ClaudeCodeSource()
check("F2: 本机未装时 available()=False（目录不存在）",
      src.available() == os.path.isdir(src.root()))

print("\n[G] 触发链端到端：claude-code 数据进 ODS → 活跃判定数到")
fd, ods = tempfile.mkstemp(suffix=".db")
os.close(fd)
conn = sqlite3.connect(ods)
conn.execute("""CREATE TABLE ods_jsonl_event(
    id INTEGER PRIMARY KEY, file_path TEXT, line_no INTEGER, session_id TEXT,
    event_type TEXT, role TEXT, ts_ms INTEGER, project TEXT, model TEXT,
    request_id TEXT, cwd TEXT, cost_usd REAL,
    raw_usage_json TEXT, user_prompt TEXT, ai_title TEXT, agent TEXT)""")
conn.execute("INSERT INTO ods_jsonl_event (session_id, event_type, ts_ms, raw_usage_json, agent) "
             "VALUES ('sess-aaa', 'usage', ?, '{\"prompt_tokens\":14649,\"total_tokens\":14950}', 'claude-code')",
             (NOW_MS - 2_000,))
conn.commit()
conn.row_factory = sqlite3.Row
fd2, wb = tempfile.mkstemp(suffix=".db")
os.close(fd2)
conn2 = sqlite3.connect(wb)
conn2.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT, status TEXT, last_activity_at INTEGER)")
conn2.commit()
conn2.close()
active = CORE._active_sessions(conn, wb, CORE.ACTIVE_WINDOW_SEC)
conn.close()
os.unlink(ods)
os.unlink(wb)
check("G1: 宿主库无记录也能凭 ODS usage 行判活跃（写字态触发链）",
      len(active) == 1 and active[0]["session_id"] == "sess-aaa",
      f"got {[(d.get('session_id')) for d in active]}")

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
