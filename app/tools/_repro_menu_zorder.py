#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_repro_menu_zorder.py — 最小复现：自绘菜单（topmost 分层窗口）的 z 序行为
================================================================================
背景（用户报障 2026-09-29）：右键设置面板打开时 ① 人物卡顿不动 ② 要一直按着右键才能
在面板里选。菜单与桌宠**都在 topmost 带里**，所以真凶很可能就是「谁是最后置顶的那个」。

本脚本只做 z 序实验（不碰设置、不改真实窗口）：
  Q1 后建的分层窗口是否在上面（基线）
  Q2 **ULW 重绘（present）会不会把窗口拉到最上？**  ← 决定菜单要不要每帧重钉
  Q3 SetWindowPos(HWND_TOPMOST) 能否把窗口拉到最上？（桌宠 tick() 每秒在干的事）

⚠️ 只信 **z 序链 dump**，不信 WindowFromPoint：这台机器上 WorkBuddy 自己有全屏
topmost 窗口，命中探针全被它吃掉（探针结果没有参考价值）。

跑法：<托管python> app/tools/_repro_menu_zorder.py
"""
import ctypes
import os
import sys
from ctypes import wintypes as wt

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
if APP not in sys.path:
    sys.path.insert(0, APP)

import wb_whale_win as W                                        # noqa: E402

U = W._user32
EX_MENU = 0x00080000 | 0x00000080 | 0x00000020                  # LAYERED|TOOLWIN|TOPMOST
WS_POPUP = 0x80000000
GW_HWNDNEXT = 2
GW_HWNDPREV = 3

U.GetTopWindow.restype = wt.HWND
U.GetWindow.restype = wt.HWND
U.GetWindow.argtypes = [wt.HWND, wt.UINT]


def cls_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    U.GetClassNameW(hwnd, buf, 256)
    return buf.value


def zchain(limit=400):
    """从最顶层往下（GW_HWNDNEXT）的可见顶层窗口链。"""
    out, h = [], U.GetTopWindow(None)
    while h and len(out) < limit:
        if U.IsWindowVisible(h):
            out.append(h)
        h = U.GetWindow(h, GW_HWNDNEXT)
    return out


def rank(hwnd, chain):
    return chain.index(hwnd) if hwnd in chain else -1


def show(label, a, b):
    ch = zchain()
    ra, rb = rank(a, ch), rank(b, ch)
    verdict = ("菜单在上 ✅" if 0 <= ra < rb else
               "桌宠在上 🔴" if 0 <= rb < ra else "?（不在链里）")
    print("   %-26s 菜单#%-3s 桌宠#%-3s → %s" % (label, ra, rb, verdict))
    return ch


def dump(ch, marks, n=10):
    print("      顶层 z 序前 %d 个：%s" % (n, " > ".join(
        ("[%s]" % marks.get(h, cls_name(h)[:22])) for h in ch[:n])))


def mk(x, y, w, h, color):
    hwnd = U.CreateWindowExW(EX_MENU, "WBMenuClass", None, WS_POPUP,
                             x, y, w, h, None, None,
                             W._kernel32.GetModuleHandleW(None), None)
    if not hwnd:
        raise RuntimeError("CreateWindowExW 失败")
    surf = W.Surface(w, h)
    U.ShowWindow(hwnd, 8)
    paint(surf, w, h, color)
    surf.present(hwnd)
    return hwnd, surf


def paint(surf, w, h, color):
    surf.clear()
    surf.round_rect(color, 0, 0, w, h, 6)


def main():
    # ⚠️ GDI+ 必须先启动：否则 Surface 的 round_rect 静默不画 → 窗口**全透明**，
    #    命中探针（WindowFromPoint）会全部落到别的窗口上，得出错误结论（踩过）。
    si = W.GdiplusStartupInput()
    si.GdiplusVersion = 1
    _tok = W.P()
    if W._GdiplusStartup(ctypes.byref(_tok), ctypes.byref(si), None) != 0:
        print("GdiplusStartup 失败")
        return 1
    W.MenuSession._ensure_class()          # 复用菜单窗口类（wndproc 已注册）
    a, sa = mk(240, 240, 300, 220, 0xFF808080)     # "菜单"（先建）
    b, sb = mk(330, 300, 300, 220, 0xFF4F3F6E)     # "桌宠"（后建）
    mark = {a: "菜单", b: "桌宠"}

    print("=" * 68)
    print("Q1 基线：后建的窗口是否在上面")
    dump(show("建好两个窗口", a, b), mark)

    print("\nQ2 ULW 重绘（present）会不会把窗口拉到最上")
    paint(sa, 300, 220, 0xFF808080)
    sa.present(a)
    dump(show("菜单 present 一次", a, b), mark)

    print("\nQ3 SetWindowPos(HWND_TOPMOST) 能否把它拉到最上")
    U.SetWindowPos(a, ctypes.c_void_p(W.HWND_TOPMOST), 0, 0, 0, 0,
                   W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)
    dump(show("菜单 SetWindowPos(TOPMOST)", a, b), mark)

    print("\nQ3b 反向：桌宠 SetWindowPos(TOPMOST) 之后")
    U.SetWindowPos(b, ctypes.c_void_p(W.HWND_TOPMOST), 0, 0, 0, 0,
                   W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)
    dump(show("桌宠 SetWindowPos(TOPMOST)", a, b), mark)

    print("\nQ4 菜单再钉一次（模拟 _pin_top）")
    U.SetWindowPos(a, ctypes.c_void_p(W.HWND_TOPMOST), 0, 0, 0, 0,
                   W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)
    dump(show("菜单再 SetWindowPos(TOPMOST)", a, b), mark)

    print("\nQ5 用 SetWindowPos 的『插到谁之后』写法")
    U.SetWindowPos(a, ctypes.c_void_p(W.HWND_TOPMOST | 0), 0, 0, 0, 0,
                   W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)
    U.SetWindowPos(a, ctypes.c_void_p(0), 0, 0, 0, 0,
                   W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)   # HWND_TOP
    dump(show("菜单 HWND_TOP", a, b), mark)

    print("\nQ6/Q7 ★根因验证：ex-style 里那个 0x20 到底是什么")
    print("   WS_EX_TOPMOST=0x8（想要）  WS_EX_TRANSPARENT=0x20（现有代码手写的值）")
    for tag, ex in (("现有写法 LAYERED|TOOLWIN|0x20", 0x00080000 | 0x00000080 | 0x00000020),
                    ("修好后 LAYERED|TOPMOST|TOOLWIN", 0x00080000 | 0x00000008 | 0x00000080)):
        h = U.CreateWindowExW(ex, "WBMenuClass", None, WS_POPUP,
                              240, 700, 260, 180, None, None,
                              W._kernel32.GetModuleHandleW(None), None)
        s = W.Surface(260, 180)
        U.ShowWindow(h, 8)
        paint(s, 260, 180, 0xFF8060A0)
        s.present(h)
        got = U.GetWindowLongW(h, -20) & 0xFFFFFFFF
        ch = zchain()
        r = rank(h, ch)
        pt = (240 + 40, 700 + 40)                    # 窗口内、不透明区
        p = wt.POINT(*pt)
        who = U.WindowFromPoint(p)
        print("   %-30s 实测 ex=0x%08X  z序#%-4s  命中=%s"
              % (tag, got, r, "自己 ✅" if who == h else
                 "0x%X(%s) ← 穿透了！" % (who or 0, cls_name(who) if who else "None")))
        s.close()
        U.DestroyWindow(h)

    for h, s in ((a, sa), (b, sb)):
        try:
            s.close()
        except Exception:
            pass
        U.DestroyWindow(h)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
