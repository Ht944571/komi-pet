# -*- coding: utf-8 -*-
r"""对**运行中的**桌宠做只读 E2E：弹真实右键菜单 → 逐行 hover → 校验子菜单窗口尺寸。

修复前（2026-09-29 用户报障「设置面板设置时会有空出来」）：`_render_sub` 用了父菜单的
surface，`Surface.present` 按 bitmap 尺寸上屏 → 子菜单窗口被 ULW 撑成**父菜单**高度，
下面多出一大截空白。
修复后：子菜单窗口 = 自己的内容高（< 父菜单高）。

只 hover、不点任何条目；结束前一律 Esc，兜底 WM_CLOSE，最后复核没有残留菜单窗口。
用法：python tools/_menu_sub_live.py
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import wb_whale_win as W                              # noqa: E402
from _diag_menu_live import item_client_xy, menu_scale, wrect   # noqa: E402

U = W._user32
PET_CLASS = W.CLASS_NAME
MENU_CLASS = "WBMenuClass"
WM_MOUSEMOVE, WM_RBUTTONUP = 0x0200, 0x0205
WM_KEYDOWN, WM_CLOSE = 0x0100, 0x0010
VK_ESCAPE = 0x1B

U.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
U.FindWindowW.restype = wt.HWND

_enum_out = []


def _cb(hwnd, _lparam):
    buf = ctypes.create_unicode_buffer(64)
    U.GetClassNameW.argtypes = [wt.HWND, wt.LPCWSTR, ctypes.c_int]
    U.GetClassNameW(hwnd, buf, 64)
    if buf.value == MENU_CLASS and U.IsWindowVisible(hwnd):
        _enum_out.append(hwnd)
    return True


_ENUM_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb)   # 模块级持有防 GC


def menus():
    """当前所有可见的菜单窗口（父 + 子）。"""
    _enum_out.clear()
    U.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
    U.EnumWindows(_ENUM_PROC, 0)
    return list(_enum_out)


def close_all():
    """兜底收尾：Esc + WM_CLOSE，直到没有菜单窗口。"""
    for _ in range(6):
        ms = menus()
        if not ms:
            return True
        for h in ms:
            U.PostMessageW(h, WM_KEYDOWN, VK_ESCAPE, 0)
            U.PostMessageW(h, WM_CLOSE, 0, 0)
        time.sleep(0.25)
    return not menus()


pet = U.FindWindowW(PET_CLASS, None)
if not pet:
    print("❌ 桌宠没在跑（找不到 %s）" % PET_CLASS)
    sys.exit(2)

print("桌宠 hwnd = 0x%X" % pet)
ok_all = True


def dump(tag, hs):
    for h in hs:
        l, t, r, b = wrect(h)                       # ⚠️ wrect 返回 (l,t,r,b)，不是宽高
        print("   %s 0x%X  rect=(%d,%d)-(%d,%d)  %dx%d"
              % (tag, h, l, t, r, b, r - l, b - t))


try:
    before = menus()
    if before:
        print("⚠️ 弹菜单前已有菜单窗口（残留？）：")
        dump("旧", before)
    U.PostMessageW(pet, WM_RBUTTONUP, 0, 0)          # 弹真实菜单
    time.sleep(0.7)
    ms = menus()
    new = [h for h in ms if h not in before]
    if not new:
        print("❌ 菜单没弹出来")
        sys.exit(1)
    parent = new[0]
    pl, pt, pr, pb = wrect(parent)
    pw, ph = pr - pl, pb - pt
    scale = menu_scale(pw)
    print("父菜单 0x%X  %dx%d @ (%d,%d)  scale≈%.3f" % (parent, pw, ph, pl, pt, scale))

    found = []
    for idx in range(16):                            # 逐行 hover，找带子菜单的行
        cx, cy = item_client_xy(scale, idx)
        _p = int(cy) << 16 | (int(cx) & 0xFFFF)
        U.PostMessageW(parent, WM_MOUSEMOVE, 0, _p)
        time.sleep(0.18)
        cur = [h for h in menus() if h not in before and h != parent]
        if cur:
            sl, st, sr, sb = wrect(cur[0])
            found.append((idx, cur[0], sr - sl, sb - st))
            print("  行 %2d → 子菜单 0x%X  %dx%d @ (%d,%d)"
                  % (idx, cur[0], sr - sl, sb - st, sl, st))

    if not found:
        print("🔴 一行都没开出子菜单（poster/dpi 估计错？）")
        ok_all = False
    else:
        for idx, h, sw, sh in found:
            same_h = (sh == ph)
            print("  行 %2d：子菜单高 %d vs 父菜单高 %d → %s"
                  % (idx, sh, ph, "🔴 仍被撑成父菜单高" if same_h else "✅ 是自己的内容高"))
            ok_all &= not same_h
        print("\n结论：子菜单尺寸 %s" % ("✅ 全部正常（不再多出一截空白）" if ok_all
                                        else "🔴 仍有多余空白"))
finally:
    gone = close_all()
    print("收尾：菜单 %s" % ("已全部收起 ✅" if gone else "★仍有残留（请按 Esc）"))
    print("残留窗口:", ["0x%X" % h for h in menus()])

sys.exit(0 if ok_all else 1)
