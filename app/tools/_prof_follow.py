#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_prof_follow.py — 跟随模式（前台切换 agent）的耗时画像（开发脚本，只测不改）

用户报障：**在前台切换不同 agent 时会卡顿**。跟随逻辑全程在桌宠的 UI 线程上跑
（WinEvent 钩子只记 hwnd → 动画帧里解析 → 去抖 → 提交），所以卡顿 = 这条线程上某段太长。
逐段计时：

  ① _fg_window_info(hwnd)        取前台进程名 + 窗口标题（OpenProcess + 读窗口文本）
  ② _follow_hint_map + resolve   登记册名片 + 身份解析（5s 缓存的命中情况）
  ③ _follow_commit(change)       **确认切换**：埋点 + 接续检测（历史上这里查视图 → 10.3s！）
  ④ 连切 5 次                     模拟用户连续 Alt+Tab 来回切
  ⑤ _follow_tick 稳态             每帧轮询兜底的成本
  ⑥ 切换后的重绘                  _drawn_sig=None → 下一帧整绘

用法：<托管python> app/tools/_prof_follow.py
"""
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_whale_win as W                                        # noqa: E402
import wb_follow as FOLLOW                                      # noqa: E402


def ms(fn, n=1):
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) * 1000.0 / n


def main():
    si = W.GdiplusStartupInput()
    si.GdiplusVersion = 1
    tok = W.P()
    if W._GdiplusStartup(ctypes.byref(tok), ctypes.byref(si), None) != 0:
        print("GdiplusStartup 失败")
        return 1

    tmp = os.path.join(HERE, "_tmp_prof_follow")
    os.makedirs(tmp, exist_ok=True)
    saved = (W.SETTINGS_FILE, W.POS_FILE)
    W.SETTINGS_FILE = os.path.join(tmp, ".whale_settings.json")
    W.POS_FILE = os.path.join(tmp, ".whale_pos.json")
    app = W.WhalePet()
    app.follow_on = True

    fg = W._user32.GetForegroundWindow()
    if not fg:
        print("没有前台窗口（拿不到 hwnd）")
        app.close()
        return 2
    proc, title = app._fg_window_info(fg)
    print("当前前台: proc=%r title=%r" % (proc, (title or "")[:40]))

    t = ms(lambda: app._fg_window_info(fg), n=20)
    print("① _fg_window_info（取进程名+标题）      : %6.2f ms" % t)

    t = ms(lambda: app._follow_hint_map(time.time()), n=20)
    print("②a _follow_hint_map（5s 缓存命中）      : %6.2f ms" % t)
    hm = app._follow_hint_map(time.time())
    t = ms(lambda: FOLLOW.resolve(hm, proc, title), n=50)
    print("②b FOLLOW.resolve（纯匹配）             : %6.2f ms" % t)

    # ③ 确认切换（含接续检测）
    FOLLOW._HANDOFF_CACHE.clear()
    t = ms(lambda: app._follow_commit({"event": "change", "key": "workbuddy",
                                       "from": "workbuddy"}, time.time()), n=5)
    print("③ _follow_commit（含接续检测，冷查）    : %6.2f ms   ← 原来这里 10304 ms" % t)
    FOLLOW._HANDOFF_CACHE.clear()

    # ④ 连切 5 次（不同来源 agent）
    keys = ["workbuddy", "zcode", "codex", "ccusage", "workbuddy"]
    t0 = time.perf_counter()
    for k in keys:
        app._follow_commit({"event": "change", "key": k, "from": "workbuddy"}, time.time())
    dt = (time.perf_counter() - t0) * 1000
    print("④ 连切 5 次（全冷查）                   : %6.1f ms   ← 原来 45912 ms" % dt)
    t0 = time.perf_counter()
    for k in keys:                                  # 再切一轮：应命中 TTL 记忆
        app._follow_commit({"event": "change", "key": k, "from": "workbuddy"}, time.time())
    print("   连切 5 次（TTL 命中）                 : %6.1f ms" % ((time.perf_counter() - t0) * 1000))

    # ⑤ 稳态：每帧轮询兜底
    t = ms(app._follow_tick_now if hasattr(app, "_follow_tick_now") else
           (lambda: app._follow_tick(time.time())), n=100)
    print("⑤ _follow_tick 稳态（每帧）              : %6.2f ms" % t)

    # ⑥ 切换后的整绘
    t = ms(app.draw, n=20)
    print("⑥ 桌宠 draw()（切换后那一帧）            : %6.2f ms" % t)

    app.close()
    W.SETTINGS_FILE, W.POS_FILE = saved
    print("\n提示：③④ 是「每次前台切换」都要付的代价；>16ms 就掉帧，>100ms 肉眼可见卡。")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
