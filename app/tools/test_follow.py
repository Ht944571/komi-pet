# -*- coding: utf-8 -*-
r"""跟随模式（聚焦信号）专项测试 —— 设计文档《桌宠跨Agent体验设计.md》P1

验证点（对照设计验收）：
  A. resolve 三值语义：L1 进程名 / L2 标题关键字 / 都不中 = 未知态（**不猜**，§2.3）
  B. 去抖状态机：确认切换 1 条事件；**快速翻飞 ≤1 条**（§2.4 一次 Alt+Tab ≤1）；
     高频往返 → "return" 退化为颜色渐变（§3.3）；进入未知态 → "unknown"（§2.3）
  C. 集成：_follow_tick 用 stub 前台 → 徽章状态/埋点/开关关闭清空
  D. 常量在 wb_motion（红线 2）

用法：python tools/test_follow.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_motion as MOTION                     # noqa: E402
import wb_follow as FOLLOW                     # noqa: E402
import wb_whale_win as W                       # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


HINTS = {
    "workbuddy": (frozenset({"workbuddy"}), ("workbuddy", "codebuddy")),
    "codex": (frozenset(), ("codex", "codex-cli")),
}

print("\n[A] resolve 三值语义（未知态不猜，§2.3）")
check("A1: L1 进程名命中", FOLLOW.resolve(HINTS, "workbuddy.exe", "随便什么") == "workbuddy")
check("A2: L1 大小写/扩展名容错", FOLLOW.resolve(HINTS, "WORKBUDDY.EXE", "") == "workbuddy")
check("A3: L2 标题关键字（进程名不可分的 CLI 场景）",
      FOLLOW.resolve(HINTS, "node.exe", "codex — 终端") == "codex")
check("A4: 都不中 → None（不猜）",
      FOLLOW.resolve(HINTS, "explorer.exe", "资源管理器") is None)
check("A5: 拿不到前台信息 → None",
      FOLLOW.resolve(HINTS, None, None) is None)
check("A6: 空名片 → None", FOLLOW.resolve({}, "workbuddy.exe", "x") is None)

print("\n[B] 去抖状态机（§2.4 一次 Alt+Tab 徽章变化 ≤1）")
tr = FOLLOW.FollowTracker()
t = 100.0
check("B1: 去抖窗口内不提交", tr.update("workbuddy", t) is None)
ev = tr.update("workbuddy", t + 0.45)                     # 已停留 0.45s ≥ 0.4
check("B2: 停留够去抖窗口 → 1 条 change",
      ev == {"event": "change", "key": "workbuddy", "from": None})
check("B3: 同身份持续 → 无事件", tr.update("workbuddy", t + 1.0) is None)
# 稳定切到 codex
tr.update("codex", t + 2.0)
ev = tr.update("codex", t + 2.5)
check("B3b: 稳定切换 → change（from=上一个身份）",
      ev == {"event": "change", "key": "codex", "from": "workbuddy"})
# 快速回扫（1.2s 内每 0.1s 翻飞一次，全部短于去抖窗口）
flap = 0
for i in range(12):
    key = "workbuddy" if i % 2 == 0 else "codex"
    flap += 1 if tr.update(key, t + 3.0 + i * 0.1) else 0
check("B4: 1.2s 快速翻飞 → 0 条事件（只认最终停留）", flap == 0, f"flap={flap}")
# 停留回 workbuddy：离开 workbuddy 后 2.3s ≤ FOLLOW_RETURN_S(5s) → return
tr.update("workbuddy", t + 4.3)
ev = tr.update("workbuddy", t + 4.8)
check("B5: 5s 内再聚焦 → 'return'（高频往返退化为颜色渐变，§3.3）",
      ev is not None and ev["event"] == "return" and ev["key"] == "workbuddy",
      f"ev={ev}")
# 切走并稳定 → change；再进未知 → unknown
tr.update("codex", t + 10.0)
events = tr.update("codex", t + 10.5)
check("B7: 稳定切到另一 agent → change",
      events == {"event": "change", "key": "codex", "from": "workbuddy"})
ev = tr.update(None, t + 20.0)
check("B8: 去抖窗口内不提交（快速闪过的未知不翻转徽章）", ev is None)
ev = tr.update(None, t + 20.5)
check("B8b: 前台不属于任何 agent 且已停留 → 确认进入未知态（不猜）",
      ev == {"event": "unknown", "key": None, "from": "codex"}
      and tr.current is None)
check("B9: 未知态持续 → 无事件", tr.update(None, t + 30.0) is None)

print("\n[C] 集成：WhalePet._follow_tick（stub 前台，不依赖真实桌面）")
tmpdir = os.path.join(HERE, "_tmp_follow_test")
os.makedirs(tmpdir, exist_ok=True)
settings_file = os.path.join(tmpdir, ".whale_settings.json")
saved_sf = W.SETTINGS_FILE
W.SETTINGS_FILE = settings_file
try:
    if os.path.exists(settings_file):
        os.remove(settings_file)
    app = W.WhalePet(run_seconds=2)
    evlog = []
    app._report_event = lambda ev, ok=None, detail="": evlog.append((ev, detail))
    fg = {"proc": "workbuddy.exe", "title": "WorkBuddy"}
    app._fg_window_info = lambda hwnd: (fg["proc"], fg["title"])
    W._user32.GetForegroundWindow = lambda: 4321     # 恒返回真值 hwnd（解析走 stub）
    app.follow_on = True
    app._follow_hook = None                          # 强制走轮询路径

    t0 = time.time()
    now = t0
    for _ in range(8):                               # 0.53s > 去抖 0.4s
        now += 0.1
        app._follow_tick(now)
    check("C1: 前台=WorkBuddy → 去抖后聚焦徽章确认",
          (app._follow_focus or {}).get("key") == "workbuddy")
    check("C2: 埋点 follow_change 已上报",
          any(e[0] == "follow_change" and "workbuddy" in e[1] for e in evlog))
    # 切到未知（explorer）
    fg["proc"], fg["title"] = "explorer.exe", "资源管理器"
    evlog.clear()
    for _ in range(8):
        now += 0.1
        app._follow_tick(now)
    check("C3: 前台未知 → 徽章进入未知态（key=None，渲染层画灰半透明）",
          (app._follow_focus or {}).get("key") is None
          and (app._follow_focus or {}).get("kind") == "unknown")
    check("C4: 埋点 follow_unknown 已上报", any(e[0] == "follow_unknown" for e in evlog))
    # 关闭跟随 → 徽章清空且不再解析
    evlog.clear()
    app.follow_on = False
    app._follow_tick(now + 0.1)
    check("C5: 关闭跟随 → 徽章清空", app._follow_focus is None)
    # 菜单开关方法
    app.follow_on = True
    with open(settings_file, "w") as f:
        json.dump({"follow": True}, f)
    W.SETTINGS_FILE = settings_file
    try:
        app._toggle_follow()
        check("C6: 菜单开关翻转 + 写盘",
              app.follow_on is False
              and json.load(open(settings_file)).get("follow") is False)
    finally:
        W.SETTINGS_FILE = saved_sf
    # 注意：不在这里 close——进程内二次 GdiplusStartup 会让 Surface 类级字体
    # 缓存句柄失效 → MeasureString 访问冲突。E 段复用本实例，最后统一 close。
finally:
    W.SETTINGS_FILE = saved_sf

print("\n[D] 常量在 wb_motion（红线 2）")
check("D1: 跟随令牌齐备且在 wb_motion",
      0.3 <= MOTION.FOLLOW_DEBOUNCE_S <= 0.5
      and 0.18 <= MOTION.FOLLOW_FADE_S <= 0.30
      and MOTION.FOLLOW_RETURN_S == 5.0)

print("\n[E] P3 全局热键：手动聚焦轮换（cycle_next 纯函数 + 集成）")
check("E1: 当前在列表 → 下一个（环绕）",
      FOLLOW.cycle_next(["a", "b", "c"], "b") == "c")
check("E2: 当前在末位 → 回绕到第一个", FOLLOW.cycle_next(["a", "b"], "b") == "a")
check("E3: 当前不在列表 → 第一个", FOLLOW.cycle_next(["a", "b"], "z") == "a")
check("E4: 无候选 → None", FOLLOW.cycle_next([], None) is None)
saved_sf = W.SETTINGS_FILE
W.SETTINGS_FILE = os.path.join(tmpdir, ".whale_settings.json")
try:
    # 复用 [C] 段的 app：进程内二次 GdiplusStartup 会让 Surface 类级字体缓存
    # 句柄失效（实测 MeasureString 访问冲突），本文件只创建一次 WhalePet。
    app._report_event = lambda ev, ok=None, detail="": None
    app._follow_hook = None
    seen = []
    app.focus_pin = False
    app._hotkey_cycle_focus()
    k1 = (app._follow_focus or {}).get("key")
    seen.append(k1)
    check("E5: 无聚焦时轮换 → 第一个启用 agent 且自动 pin",
          k1 in app._focus_candidates() and app.focus_pin is True)
    app._hotkey_cycle_focus()
    k2 = (app._follow_focus or {}).get("key")
    seen.append(k2)
    check("E6: 再次轮换 → 换到下一个（非重复）", k2 != k1 and k2 in app._focus_candidates())
    check("E7: 轮换已同步去抖状态机（force）",
          app._follow_tracker.current == k2)
finally:
    W.SETTINGS_FILE = saved_sf
    app.close()                    # 全部段共用同一实例，最后统一关闭（GDI+ 生命周期）

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
