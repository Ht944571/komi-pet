# -*- coding: utf-8 -*-
r"""跟随模式 P2 连续锚点 + P3 pin 锁定 专项测试

验证点（对照《桌宠跨Agent体验设计.md》§4.2/§8 P2·P3）：
  A. 叙述格式化：「今天：Codex 3 轮 · WorkBuddy 12 轮」
  B. today_timeline 数据层（真库只读）：summary/recent 形状
  C. 气泡 row3：有数据 → 跨 agent 叙述；无数据 → 回退「活跃 X 分钟前」
  D. P3 pin：锁定 → 跟随不覆盖；解开 → 跟随即刻接管；手动聚焦生效；持久化
  E. Codex 配件：登记册一行 JSON → accessories() 含 codex

用法：python tools/test_timeline_pin.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_motion as MOTION                     # noqa: E402
import wb_whale_win as W                       # noqa: E402
from wb_hover_core import today_timeline       # noqa: E402
import wb_agent_registry as REG                # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


print("\n[A] 时间线叙述格式化")
check("A1: 多 agent 按轮次叙述",
      W.format_timeline_summary([("Codex", 3), ("WorkBuddy", 12)])
      == "Codex 3 轮 · WorkBuddy 12 轮")
check("A2: 空数据 → 空串（调用方回退）", W.format_timeline_summary([]) == "")

print("\n[B] today_timeline 数据层（真库只读，v_turn_total 零新链路）")
db = os.path.join(APP, "wb_usage", "data", "wb_usage_dw.db")
summary, recent = today_timeline(db)
import sqlite3 as _s3
_c = _s3.connect(f"file:{db}?mode=ro", uri=True)
_today_n = _c.execute(
    "SELECT COUNT(*) FROM v_turn_total WHERE day = date('now','localtime') AND agent != ''"
).fetchone()[0]
_c.close()
if _today_n:
    check("B1: 今日有轮次 → summary 非空", len(summary) > 0, f"summary={summary}")
else:
    check("B1: 今日暂无轮次 → summary 为空也一致（午夜跨天不误报）",
          summary == [], f"summary={summary}")
check("B2: summary 形状 = (agent, int 轮次)",
      all(isinstance(k, str) and isinstance(n, int) for k, n in summary))
check("B3: recent 形状 = (HH:MM, agent, 标题)",
      all(len(t) == 5 and t[2] == ":" for t, _a, _ti in recent[:1]) or not recent)
check("B4: recent 按时间降序",
      all(recent[i][0] >= recent[i + 1][0] for i in range(len(recent) - 1)))

print("\n[C] 气泡 row3：跨 agent 叙述 / 无数据回退")
tmpdir = os.path.join(HERE, "_tmp_tl_test")
os.makedirs(tmpdir, exist_ok=True)
settings_file = os.path.join(tmpdir, ".whale_settings.json")
saved_sf = W.SETTINGS_FILE
W.SETTINGS_FILE = settings_file
try:
    if os.path.exists(settings_file):
        os.remove(settings_file)
    app = W.WhalePet(run_seconds=2)
    app.active = []
    app.db_ok = True
    app.api_ok = True
    app.latest_turn = {"credit": 1.0, "total_tokens": 1000,
                       "first_ts": time.time() - 60, "last_ts": time.time(),
                       "title": "测试会话", "project": "proj"}
    app._today_timeline = lambda: ([("Codex", 3), ("WorkBuddy", 12)], [])
    row1, row2, row3 = app._bubble_lines()
    check("C1: row3 = 「今天：Codex 3 轮 · WorkBuddy 12 轮 · 双击开看板」",
          row3 == "今天：Codex 3 轮 · WorkBuddy 12 轮 · 双击开看板", f"row3={row3}")
    app._today_timeline = lambda: ([], [])
    _r1, _r2, row3 = app._bubble_lines()
    check("C2: 无数据 → 回退「活跃 …」叙述", row3.startswith("活跃 "), f"row3={row3}")

    print("\n[D] P3 pin 锁定 + 手动聚焦")
    saved_gfw = W._user32.GetForegroundWindow
    W._user32.GetForegroundWindow = lambda: 4321
    fg = {"proc": "workbuddy.exe", "title": "WorkBuddy"}
    app._fg_window_info = lambda hwnd: (fg["proc"], fg["title"])
    app._follow_hook = None                       # 强制走轮询路径（可 Stub）
    app._report_event = lambda ev, ok=None, detail="": None
    now = time.time()
    app.focus_pin = False
    for i in range(6):                            # 0.6s > 去抖 → 聚焦 workbuddy
        app._follow_tick(now + i * 0.1)
    check("D1: 跟随先确认 workbuddy",
          (app._follow_focus or {}).get("key") == "workbuddy")
    app.focus_pin = True
    app._follow_focus = {"key": "codex", "accent": "#10A37F",
                         "kind": "change", "t0": now}
    fg["proc"], fg["title"] = "workbuddy.exe", "WorkBuddy"
    for i in range(8):                            # 0.8s：前台回到 workbuddy
        app._follow_tick(now + 1 + i * 0.1)
    check("D2: pin 期间跟随不覆盖（仍是 codex）",
          (app._follow_focus or {}).get("key") == "codex")
    app._set_manual_focus("zcode")
    check("D3: 手动聚焦生效（pin 下可切）",
          (app._follow_focus or {}).get("key") == "zcode")
    app._toggle_focus_pin()                       # pin on（key=zcode 入盘）
    app.focus_pin = False                         # 解开 → 跟随接管
    fg["proc"], fg["title"] = "workbuddy.exe", "WorkBuddy"
    for i in range(8):
        app._follow_tick(now + 2 + i * 0.1)
    check("D4: 解开 pin → 跟随即刻接管回 workbuddy",
          (app._follow_focus or {}).get("key") == "workbuddy")
    with open(settings_file, encoding="utf-8") as f:
        st = json.load(f)
    check("D5: pin 状态持久化（focus_pin/focus_pin_key）",
          "focus_pin" in st and "focus_pin_key" in st)
    W._user32.GetForegroundWindow = saved_gfw
    app.close()
finally:
    W.SETTINGS_FILE = saved_sf

print("\n[E] Codex 配件（P2 一行 JSON）")
acc = REG.accessories()
check("E1: 登记册 codex.accessory = beret",
      REG.load()["agents"]["codex"].get("accessory") == "beret")
check("E2: accessories() 含 codex（并集语义，无需新美术）",
      "codex" in acc and acc["codex"]["accessory"] == "beret")

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
