#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_prof_menu.py — 右键菜单的耗时画像（开发脚本，只测不改）

用户报障：右键打开设置面板、在里面选择设置时**卡顿**。
菜单全程在**桌宠的 UI 线程**上跑（PyWin32 消息循环 + GDI+ 绘制），所以卡顿 = 这条线程上
某一段耗时太长。这个脚本把整条链路拆开逐个计时，先量再优化：

  ① context_menu 的**条目构建**（弹菜单之前做的事：REG.load / 今日时间线 / 残留会话扫码 / 更新源）
  ② MenuSession._open（建窗 + 首绘 + ULW 上屏）
  ③ MenuSession._render（一次完整重绘：阴影 + 卡片 + N 条目 GDI+ 文字）
  ④ 悬停切换一次（= ③ + 一次 ULW）——鼠标在条目上移动时的代价
  ⑤ 子菜单展开（建窗 + Surface + 渲染）
  ⑥ 桌宠自身 draw()（菜单开着时它仍在 24fps 重绘，会和菜单抢同一条线程）
  ⑦ 点完条目的事后动作（如 _recreate_window / _save_settings）

用法：<托管python> app/tools/_prof_menu.py
"""
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_whale_win as W                                        # noqa: E402


def ms(fn, n=1):
    """跑 n 次取平均毫秒（带一次预热）。"""
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) * 1000.0 / n


def main():
    # GDI+ 必须先起（离屏绘制）
    si = W.GdiplusStartupInput()
    si.GdiplusVersion = 1
    tok = W.P()
    if W._GdiplusStartup(ctypes.byref(tok), ctypes.byref(si), None) != 0:
        print("GdiplusStartup 失败")
        return 1

    # ---- 桌宠实例：只用来拿真实的条目构建路径（不 tick、不写状态心跳）----
    tmp = os.path.join(HERE, "_tmp_prof_menu")
    os.makedirs(tmp, exist_ok=True)
    saved_settings, saved_pos = W.SETTINGS_FILE, W.POS_FILE
    W.SETTINGS_FILE = os.path.join(tmp, ".whale_settings.json")
    W.POS_FILE = os.path.join(tmp, ".whale_pos.json")
    t_boot = time.perf_counter()
    app = W.WhalePet()
    boot_ms = (time.perf_counter() - t_boot) * 1000
    print("桌宠实例启动: %.0f ms（含窗口创建，仅参考）" % boot_ms)

    # ---- ① 条目构建：真跑 context_menu，但把 MenuSession 换成"立刻返回 0" ----
    class _NullMenu:
        def __init__(self, items, dpi=96):
            self.items = items
            _NullMenu.last = items

        def run(self, owner, pt):
            return 0

    real_cls = W.MenuSession
    W.MenuSession = _NullMenu
    try:
        build = ms(lambda: app.context_menu(app.hwnd), n=5)
        items = _NullMenu.last
    finally:
        W.MenuSession = real_cls
    print("① 条目构建 context_menu（弹菜单之前）: %.1f ms  （%d 个条目）" % (build, len(items)))

    # 再拆细：三个可疑的重活各测一次
    parts = {}
    try:
        parts["REG.load()（读 agent 登记册 JSON）"] = ms(lambda: W.REG.load(), n=10)
    except Exception as e:
        parts["REG.load()"] = float("nan")
        print("   REG.load 失败:", e)
    try:
        parts["_today_timeline()（查今日轮次）"] = ms(lambda: app._today_timeline(), n=5)
    except Exception as e:
        parts["_today_timeline()"] = float("nan")
        print("   _today_timeline 失败:", e)
    try:
        parts["stale_working_sessions()（残留会话扫码·2 次 SQL）"] = ms(
            lambda: W.stale_working_sessions(app.db_path, app.wb_db), n=3)
    except Exception as e:
        parts["stale_working_sessions()"] = float("nan")
        print("   stale 失败:", e)
    for k, v in parts.items():
        print("   ├─ %-52s %.1f ms" % (k, v))

    # ---- ②③④⑤ 菜单绘制链路 ----
    _it = lambda label, cmd, **kw: {"label": label, "cmd": cmd, **kw}
    mitems = [_it("打开完整看板", 1), _it("想法气泡", 2, checked=True),
              _it("音效", 3, checked=True), _it("完成态自动收起", 4, checked=True),
              _it("随 Agent 退出联动关闭", 5, checked=True), _it("跟随前台切换聚焦", 6, checked=False),
              _it("锁定聚焦（pin）", 7, checked=False), {"sep": True},
              {"label": "今日时间线", "sub": [_it("今天：workbuddy 3 轮", 100), {"sep": True},
                                              _it("12:01 zcode · 测试轮次", 101)]},
              {"label": "手动聚焦（Ctrl+Alt+F9 轮换）",
               "sub": [_it("WorkBuddy", 200, checked=True), _it("ZCode", 201, checked=True)]},
              {"sep": True},
              {"label": "桌宠大小", "sub": [_it("1.0x（默认）", 300, checked=True)]},
              {"label": "动效质量", "sub": [_it("完整", 400, checked=True)]},
              {"sep": True}, _it("退出古见同学", 999, danger=True)]

    sess = W.MenuSession(mitems, dpi=96)
    t_open = None

    def _open_once():
        nonlocal t_open
        s = W.MenuSession(mitems, dpi=96)
        t0 = time.perf_counter()
        s._open(app.hwnd, (200, 200), grab=False)
        t_open = (time.perf_counter() - t0) * 1000
        s._teardown()

    _open_once()
    print("② _open（建窗 + 首绘 + ULW 上屏）: %.1f ms" % t_open)

    sess = W.MenuSession(mitems, dpi=96)
    sess._open(app.hwnd, (200, 200), grab=False)
    r_full = ms(lambda: sess._render(), n=20)
    print("③ _render（整卡重绘 + ULW）: %.1f ms  （%d 条目）" % (r_full, len(mitems)))
    # 拆：只画不推屏
    r_draw = ms(lambda: (sess.surf.clear(), sess._render_item(
        sess.surf, sess.margins, sess.margins, 32, mitems[0], True)), n=50)
    print("   ├─ 单条目 GDI+ 绘制: %.2f ms → ×%d 条目 ≈ %.1f ms" % (r_draw, len(mitems), r_draw * len(mitems)))
    # 悬停切换一次 = 一次完整重绘
    def _hover_cycle():
        sess._hover = 0
        sess._render()
        sess._hover = 1
        sess._render()
    h_cycle = ms(_hover_cycle, n=8) / 2
    print("④ 悬停切换一次（重绘 + ULW）: %.1f ms  ← 鼠标划过条目时的代价" % h_cycle)

    # ⑤ 子菜单：展开一次（建窗 + Surface + 渲染）
    sub_idx = next(i for i, it in enumerate(mitems) if it.get("sub"))
    def _sub_cycle():
        sess._open_sub(sub_idx)
        sess._close_sub()
    s_cycle = ms(_sub_cycle, n=8)
    print("⑤ 子菜单展开+收起一次: %.1f ms  （建窗 + %dx%d DIB + 渲染 + 销毁）"
          % (s_cycle, sess.m["w"], 0))
    sess._teardown()

    # ---- ⑥ 桌宠自身 draw()（菜单开着时它仍按 24fps 重绘）----
    app.active = []
    try:
        d_pet = ms(app.draw, n=20)
        print("⑥ 桌宠 draw()（含气泡文字）: %.1f ms  → 24fps 时每帧占 UI 线程 %.1f%%"
              % (d_pet, d_pet / (W.ANIM_MS / 1000.0) * 100))
    except Exception as e:
        print("⑥ 桌宠 draw() 失败:", e)

    # ---- ⑦ 点完条目的事后动作 ----
    print("\n⑦ 点完条目的事后代价")
    calls = []
    orig_build = app._build_cache_objects

    def _counting():
        calls.append(1)
        return orig_build()
    app._build_cache_objects = _counting
    saved_bubble = app.bubble_on

    def _toggle_bubble():
        app.bubble_on = not app.bubble_on
        app._recreate_window()
    try:
        t_bub = ms(_toggle_bubble, n=3)
        print("   ├─ 「想法气泡」切换（只改窗口高度）: %.1f ms   缓存重建次数=%d（应为 0）"
              % (t_bub, len(calls)))
    except Exception as e:
        print("   ├─ 切换气泡失败:", e)
    app.bubble_on = saved_bubble
    try:
        app._recreate_window()
    except Exception:
        pass
    calls.clear()

    # 真改大小：应**立刻返回**（后台建），不阻塞 UI
    t0 = time.perf_counter()
    app.scale = 0.8 if abs(app.scale - 0.8) > 1e-6 else 1.0
    app._recreate_window()
    t_scale_call = (time.perf_counter() - t0) * 1000
    print("   ├─ 「桌宠大小」切换 _recreate_window 返回: %.1f ms（后台重建中=%s）"
          % (t_scale_call, app._cache_building))
    t0 = time.perf_counter()
    while app._cache_building and time.perf_counter() - t0 < 30:
        app._wait_cache_new()                 # 平时由 _anim_tick 每帧调
        time.sleep(0.05)
    print("   └─ 后台重建总耗时: %.2f s；缓存已换新=%s"
          % (time.perf_counter() - t0, app._cache_sig_done == app._cache_sig()))
    app._build_cache_objects = orig_build
    try:
        t_save = ms(app._save_settings, n=10)
        print("   ├─ _save_settings: %.2f ms" % t_save)
    except Exception as e:
        print("   _save_settings 失败:", e)

    # 收尾
    try:
        app.close()
    except Exception:
        pass
    W.SETTINGS_FILE, W.POS_FILE = saved_settings, saved_pos
    print("\n提示：主机上『菜单开着』时 UI 线程还要跑桌宠 24fps 重绘 —— ⑥ 的耗时越高，"
          "菜单悬停时的掉帧越明显。")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
