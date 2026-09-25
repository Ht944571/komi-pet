# -*- coding: utf-8 -*-
r"""数仓维护（P0）+ 日志轮转（P1）专项测试

验证点：
  A. raw_retention：TTL 按主表 ts_ms 联结删除；消费层 ods_jsonl_event 不动；
     ts_ms=0 的行不删；孤儿上报不删除；分批删除计数正确
  B. vacuum_db：删除后文件真正缩小（auto_vacuum=0 库必须 VACUUM）
  C. rotate_if_large：超限轮转保留一代、小文件不动、多次轮转链正确

用法：python tools/test_dw_maintain.py
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "wb_usage"))

import wb_collect as C                        # noqa: E402
from wb_hover_core import rotate_if_large     # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


NOW_MS = int(time.time() * 1000)
DAY = 86400 * 1000


def make_db(path):
    conn = C.get_conn(path)
    rows = [
        # (line_no, ts_ms, raw_size)  —— raw_json 为 size 个 'x'，便于验证 freed
        (1, NOW_MS - 100 * DAY, 1000),   # 100 天前 → TTL 内删
        (2, NOW_MS - 100 * DAY, 2000),
        (3, NOW_MS - 1 * DAY, 3000),     # 昨天 → 保留
        (4, 0, 4000),                    # 无有效时间 → 保留
        (5, NOW_MS - 100 * DAY, 5000),   # 100 天前，raw_json=NULL → 删（不计 freed）
    ]
    for line_no, ts, size in rows:
        conn.execute(
            """INSERT OR IGNORE INTO ods_jsonl_event
               (id, file_path, line_no, event_type, role, session_id, ts_ms,
                cwd, model, request_id, raw_usage_json, user_prompt, ai_title,
                project, agent)
               VALUES ('id', 'f.jsonl', ?, 'assistant', 'ai', 'sess', ?,
                       '', '', '', '{}', NULL, NULL, 'proj', 'workbuddy')""",
            (line_no, ts))
        raw = "x" * size if size != 5000 else None
        conn.execute(
            "INSERT OR IGNORE INTO ods_jsonl_raw(file_path, line_no, raw_json)"
            " VALUES ('f.jsonl', ?, ?)", (line_no, raw))
    # 孤儿：raw 有、event 无
    conn.execute(
        "INSERT OR IGNORE INTO ods_jsonl_raw(file_path, line_no, raw_json)"
        " VALUES ('f.jsonl', 99, 'yyyy')")
    conn.commit()
    return conn


print("\n[A] raw_retention：TTL 删旧 / 事件层与零时间行 / 孤儿都不动")
tmp = os.path.join(tempfile.gettempdir(), "komi-test-dw.db")
if os.path.exists(tmp):
    os.remove(tmp)
conn = make_db(tmp)
ev_before = conn.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0]
deleted, freed, orphans = C.raw_retention(conn, ttl_days=30)
check("A1: 只删 3 行超龄原文（含 NULL raw 行，不计 freed）",
      deleted == 3, f"deleted={deleted}")
check("A2: freed ≈ 3000 字节（NULL 行不计）",
      freed == 3000, f"freed={freed}")
check("A3: 消费层 ods_jsonl_event 一行不动",
      conn.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0] == ev_before == 5)
check("A4: ts_ms=0 的原文保留",
      conn.execute("SELECT COUNT(*) FROM ods_jsonl_raw WHERE line_no=4").fetchone()[0] == 1)
check("A5: 近期原文保留",
      conn.execute("SELECT COUNT(*) FROM ods_jsonl_raw WHERE line_no=3").fetchone()[0] == 1)
check("A6: 孤儿上报且不删（本轮策略）", orphans == 1
      and conn.execute("SELECT COUNT(*) FROM ods_jsonl_raw WHERE line_no=99").fetchone()[0] == 1)
check("A7: 再次运行 → 幂等（0 删除）",
      C.raw_retention(conn, ttl_days=30)[:2] == (0, 0))

print("\n[B] vacuum_db：删除后文件真正缩小（写入 → 落盘 → 删 → VACUUM）")
conn.commit()
big = "x" * 200000
conn.execute("INSERT INTO ods_jsonl_raw(file_path, line_no, raw_json)"
             " VALUES ('f.jsonl', 98, ?)", (big,))
conn.commit()
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # 把写入落进主文件
grown = os.path.getsize(tmp)
check("B1: 大行写入并落盘后文件增长", grown > 100000, f"grown={grown}")
conn.execute("DELETE FROM ods_jsonl_raw WHERE line_no=98")
conn.commit()
C.vacuum_db(conn)
after = os.path.getsize(tmp)
check("B2: VACUUM 后文件回收（空闲页交还）", after < grown, f"{grown} → {after}")
check("B3: 回收后数据完好（消费层行数不变）",
      conn.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0] == 5)
conn.close()
os.remove(tmp)

print("\n[C] rotate_if_large：按大小轮转，保留一代")
tlog = os.path.join(tempfile.gettempdir(), "komi-test-events.log")
for p in (tlog, tlog + ".1"):
    if os.path.exists(p):
        os.remove(p)
with open(tlog, "w") as f:
    f.write("x" * 2000)
rotate_if_large(tlog, max_bytes=1000)
check("C1: 超限 → 原文件轮转为 .1，主文件消失",
      os.path.exists(tlog + ".1") and not os.path.exists(tlog))
check("C2: .1 内容 = 旧内容", open(tlog + ".1").read() == "x" * 2000)
with open(tlog, "w") as f:
    f.write("new")
rotate_if_large(tlog, max_bytes=1000)
check("C3: 新文件未超限 → 不轮转",
      open(tlog).read() == "new" and open(tlog + ".1").read() == "x" * 2000)
with open(tlog, "w") as f:
    f.write("y" * 2000)
rotate_if_large(tlog, max_bytes=1000)
check("C4: 再轮转 → 旧 .1 被覆盖（保留一代）",
      open(tlog + ".1").read() == "y" * 2000)
for p in (tlog, tlog + ".1"):
    if os.path.exists(p):
        os.remove(p)

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
