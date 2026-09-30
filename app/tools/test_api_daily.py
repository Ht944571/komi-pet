# -*- coding: utf-8 -*-
r"""日趋势的**时间粒度**测试（2026-09-29：单日口径改按小时）

需求：区间是一天时（今天 / 昨天 / 自定义里的单独一天），"日趋势"与"缓存命中率趋势"的
横轴要按小时铺满 **00:00–24:00**；原来按天分桶只有一根柱子，看不出日内节奏。

验证点：
  A. gran=hour 且**单日** → 24 桶（00..23）、空桶补零、有数据的桶数值/命中率正确
  B. gran=hour 且**非单日** → 照实按 天+小时 返回，**不补零**（不给前端造 30×24 行）
  C. gran=day 不受影响（多日仍按天；单日仍是 1 行）
  D. **缓存 key 必须带 gran**：同区间 day / hour 两次查询不能互相顶掉（踩过的老坑）
  E. `_single_day_date` 口径：today / yesterday / custom 同日 / 跨日 / 非法

用法：python tools/test_api_daily.py
"""
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "wb_usage"))
import wb_api                                          # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name if cond else f"{name}  {detail}")
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))


TODAY = time.strftime("%Y-%m-%d")
YEST = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))

COLS = ("turn_id, day, session_id, project, title, first_time, last_time, user_prompt, "
        "api_calls, credit, total_tokens, cached_tokens, miss_tokens, completion_tokens, "
        "thinking_tokens, model_used, source, agent")
DDL = """CREATE TABLE dws_turn(
    turn_id TEXT, day TEXT, session_id TEXT, project TEXT, title TEXT,
    first_time TEXT, last_time TEXT, user_prompt TEXT, api_calls INTEGER,
    credit REAL, total_tokens INTEGER, cached_tokens INTEGER, miss_tokens INTEGER,
    completion_tokens INTEGER, thinking_tokens INTEGER, model_used TEXT,
    source TEXT, agent TEXT)"""

# (day, first_time, tokens, cached, miss, completion, credit)
ROWS = [
    (TODAY, f"{TODAY} 09:05:00", 1000, 800, 200, 50, 1.5),
    (TODAY, f"{TODAY} 09:40:00", 2000, 1500, 500, 60, 2.5),
    (TODAY, f"{TODAY} 13:10:00", 4000, 3000, 1000, 80, 3.0),
    (YEST, f"{YEST} 10:15:00", 900, 300, 600, 40, 0.5),
]


def make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.execute(DDL)
    c.execute("CREATE TABLE dws_meta(k TEXT, v TEXT)")     # turn_src 只看 dws_turn 有没有数据
    for i, (day, ft, tok, ca, mi, co, cr) in enumerate(ROWS):
        c.execute(f"INSERT INTO dws_turn({COLS}) VALUES ({','.join('?'*18)})",
                  (f"turn_{i}", day, "sess_1", "测试项目", "标题", ft, ft, "prompt",
                   2, cr, tok, ca, mi, co, 0, "m1", "jsonl", "workbuddy"))
    c.commit()
    c.close()
    return path


db = make_db()
api = wb_api.Api(db)

print("\n[A] 单日 → 按小时（补零到 24 桶）")
h = api.daily("today", "all", "hour")["data"]
check("A1: 今天 = 24 桶", len(h) == 24, f"got {len(h)}")
check("A2: 桶是 00..23 且有序", [x["hour"] for x in h] == [f"{i:02d}" for i in range(24)],
      f"got {[x['hour'] for x in h][:6]}…")
h09 = h[9]
check("A3: 09 点两轮合并正确（tokens/命中/未命中）",
      h09["turns"] == 2 and h09["total_tokens"] == 3000
      and h09["cached_tokens"] == 2300 and h09["miss_tokens"] == 700,
      f"got turns={h09['turns']} tok={h09['total_tokens']} "
      f"cached={h09['cached_tokens']} miss={h09['miss_tokens']}")
check("A4: 命中率按小时桶算（2300/(2300+700)）",
      abs((h09["cache_hit_rate"] or 0) - 2300 / 3000) < 1e-9, f"got {h09['cache_hit_rate']}")
check("A5: credit 合并（1.5+2.5）", abs((h09["credit"] or 0) - 4.0) < 1e-9, f"got {h09['credit']}")
check("A6: 13 点单独成桶", h[13]["turns"] == 1 and h[13]["total_tokens"] == 4000,
      f"got {h[13]}")
check("A7: 空桶补零且命中率为 None（折线留空而不是画 0）",
      h[3]["turns"] == 0 and h[3]["total_tokens"] == 0 and h[3]["cache_hit_rate"] is None,
      f"got {h[3]}")
check("A8: 补零桶带当天日期（tooltip 要显示日期）", all(x["day"] == TODAY for x in h),
      f"got {sorted({x['day'] for x in h})}")
hy = api.daily("yesterday", "all", "hour")["data"]
check("A9: 昨天也是 24 桶且 10 点有数据",
      len(hy) == 24 and hy[10]["turns"] == 1 and hy[10]["total_tokens"] == 900,
      f"got len={len(hy)} h10={hy[10]}")
hc = api.daily(f"custom:{TODAY}:{TODAY}", "all", "hour")["data"]
check("A10: 自定义「同一天」同样按小时（24 桶）", len(hc) == 24, f"got {len(hc)}")

print("\n[B] 非单日 → 按 天+小时，照实返回（不补零）")
hm = api.daily(f"custom:{YEST}:{TODAY}", "all", "hour")["data"]
keys = [(x["day"], x["hour"]) for x in hm]
check("B1: 只有真有数据的天/小时（3 条）", len(hm) == 3, f"got {keys}")
check("B2: 跨天按 day 升序", [k[0] for k in keys] == [YEST, TODAY, TODAY], f"got {keys}")

print("\n[C] gran=day 不受影响")
d1 = api.daily("today", "all", "day")["data"]
check("C1: 单日按天仍是 1 行", len(d1) == 1 and d1[0]["day"] == TODAY, f"got {d1}")
d7 = api.daily("7", "all", "day")["data"]
check("C2: 近 7 天按天 = 2 行（昨天+今天）", len(d7) == 2, f"got {[x['day'] for x in d7]}")
check("C3: 按天聚合值正确（今天 3 轮 7000 tokens）",
      d7[-1]["turns"] == 3 and d7[-1]["total_tokens"] == 7000, f"got {d7[-1]}")

print("\n[D] 缓存 key 带 gran（同区间的 day / hour 不能互相顶掉）")
_a = api.daily("today", "all", "day")["data"]
_b = api.daily("today", "all", "hour")["data"]
_a2 = api.daily("today", "all", "day")["data"]
check("D1: day→hour→day 三次结果各自正确（key 不带 gran 时这里会串）",
      len(_a) == 1 and len(_b) == 24 and len(_a2) == 1,
      f"day={len(_a)} hour={len(_b)} day2={len(_a2)}")

print("\n[E] _single_day_date 口径")
sd = wb_api.Api._single_day_date
check("E1: today/yesterday 给今天/昨天", sd("today") == TODAY and sd("yesterday") == YEST,
      f"got {sd('today')} / {sd('yesterday')}")
check("E2: custom 同一天 → 该日期", sd(f"custom:{TODAY}:{TODAY}") == TODAY)
check("E3: custom 跨天 → None", sd(f"custom:{YEST}:{TODAY}") is None)
check("E4: 多日/非法 → None",
      sd("7") is None and sd("all") is None and sd("custom:2026-13-99:x") is None
      and sd("") is None and sd(None) is None)

try:
    os.unlink(db)
except OSError:
    pass

print("\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
