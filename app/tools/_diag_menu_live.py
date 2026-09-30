#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_diag_menu_live.py — 真机 E2E 诊断：右键菜单 vs 桌宠（z 序 / hover / 点击路由）
================================================================================
为什么需要真机：用户报障的「菜单打开时人物卡顿不动」+「要一直按着右键才能选」，
根因都在 Win32 层（谁是最后置顶的那个 / 鼠标消息被谁吃掉 / SetCapture 是否生效）——
mock 单测与离屏渲染都测不到。

本脚本对**正在运行的桌宠**做只读诊断：

  S1 PostMessage 桌宠 WM_RBUTTONUP → 走真实 context_menu 弹出菜单
  S2 dump 顶层 z 序（间隔 2.5s 两次）→ 看桌宠会不会爬到菜单之上
  S3 PrintWindow(PW_RENDERFULLCONTENT) 抓菜单像素做哈希，移动光标到不同条目
     → 哈希变化 = hover 生效（**不按任何键**，即用户说的"不用一直按着"）
  S4 可选：合成一次左键点条目（--click N），靠事件日志确认命令是否分发
  S5 PostMessage(WM_KEYDOWN, VK_ESCAPE) 取消菜单；光标还原

用法：
    <托管python> app/tools/_diag_menu_live.py            # 只诊断（只读）
    <托管python> app/tools/_diag_menu_live.py --click 1  # 额外点一下第 1 项（0 基）
"""
import ctypes
import hashlib
import os
import sys
import time
from ctypes import wintypes as wt

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
if APP not in sys.path:
    sys.path.insert(0, APP)

import wb_whale_win as W                                        # noqa: E402

U = W._user32
PET_CLASS, MENU_CLASS = "WBWhalePetClass", "WBMenuClass"
WM_RBUTTONUP, WM_KEYDOWN, VK_ESCAPE = 0x0205, 0x0100, 0x1B
GW_HWNDNEXT = 2
PW_RENDERFULLCONTENT = 2
EVENTS = os.path.join(os.environ.get("TEMP") or ".", "komi-wb-hover.events.log")

U.GetTopWindow.restype = wt.HWND
U.GetWindow.restype = wt.HWND
U.GetWindow.argtypes = [wt.HWND, wt.UINT]
U.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
U.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]


def cls_name(h):
    buf = ctypes.create_unicode_buffer(256)
    U.GetClassNameW(h, buf, 256)
    return buf.value


EX_TAGS = ((0x00080000, "LAYERED"), (0x00000008, "TOPMOST"),
           (0x00000080, "TOOLWIN"), (0x08000000, "NOACTIVATE"),
           (0x00000020, "TRANSPARENT") if False else (0x00000020, "?"),
           (0x00000004, "APPWINDOW"), (0x00000001, "DLGMODALFRAME"))


def ex_flags_of(ex):
    tags = [name for bit, name in EX_TAGS if ex & bit and name != "?"]
    return "+".join(tags) or "-"


def wrect(h):
    r = W.RECT()                    # ⚠️ 同上：用模块声明的 RECT
    U.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def zchain(limit=600):
    out, h = [], U.GetTopWindow(None)
    while h and len(out) < limit:
        if U.IsWindowVisible(h):
            out.append(h)
        h = U.GetWindow(h, GW_HWNDNEXT)
    return out


def find(cls):
    U.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    U.FindWindowW.restype = wt.HWND
    return U.FindWindowW(cls, None)


def rank(h, ch):
    return ch.index(h) if h in ch else -1


def probe_z(pet, menu, tag):
    ch = zchain()
    rp, rm = rank(pet, ch), rank(menu, ch)
    ok = 0 <= rm < rp
    print("  %-18s 菜单#%-3s 桌宠#%-3s → %s" % (tag, rm, rp,
          "✅ 菜单在桌宠之上" if ok else "🔴 桌宠压在菜单之上"))
    return ok


def win_hash(hwnd):
    """PrintWindow 抓任意窗口像素 → md5 前 12 位（用来判"有没有在动/高亮"）。"""
    l, t, r, b = wrect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return "0x0"
    s = W.Surface(w, h)
    try:
        U.PrintWindow(hwnd, s.hdc, PW_RENDERFULLCONTENT)
        return hashlib.md5(ctypes.string_at(s.bits, w * h * 4)).hexdigest()[:12]
    finally:
        s.close()


def menu_hash(menu):
    """PrintWindow 抓菜单窗口像素 → md5（悬停高亮会改变像素）。"""
    l, t, r, b = wrect(menu)
    w, h = r - l, b - t
    s = W.Surface(w, h)
    try:
        U.PrintWindow(menu, s.hdc, PW_RENDERFULLCONTENT)
        return hashlib.md5(ctypes.string_at(s.bits, w * h * 4)).hexdigest()[:12], (l, t, w, h)
    finally:
        s.close()


def menu_scale(win_w):
    """从菜单窗口宽度反推绘制比例：w = round(MENU_W*s) + 2*round(14*s) ≈ (MENU_W+28)*s。

    ⚠️ 不能用 `GetDpiForWindow(pet)` 猜：实测它给出 144 而菜单是按 96 画的
    （进程 DPI 感知/显示器不同），猜错会让"条目中心"落到别的行上。
    """
    return win_w / float(W.MENU_W + 2 * W._MENU_SHADOW)


def item_center(l, t, idx, scale):
    """第 idx 个条目的屏幕中心（前两项永远是 打开完整看板 / 想法气泡，没有 sep）。"""
    h = round(W.MENU_ITEM_H * scale)
    mg = round(W._MENU_SHADOW * scale)
    pad_v = round(6 * scale)
    return l + mg + round(W.MENU_W * scale) / 2.0, t + mg + pad_v + idx * h + h / 2.0


def item_client_xy(scale, idx):
    """条目中心 → client 坐标（PostMessage 造 mousemove 用）。"""
    h = round(W.MENU_ITEM_H * scale)
    mg = round(W._MENU_SHADOW * scale)
    return (mg + round(W.MENU_W * scale) / 2.0,
            mg + round(6 * scale) + idx * h + h / 2.0)


def wfp(x, y):
    p = W.POINT(int(x), int(y))
    U.WindowFromPoint.argtypes = [W.POINT]
    U.WindowFromPoint.restype = wt.HWND
    h = U.WindowFromPoint(p)
    if not h:
        return "None"
    if h == X_PET[0]:
        return "桌宠"
    if h == X_MENU[0]:
        return "菜单"
    return "0x%X(%s)" % (h, cls_name(h))


X_PET, X_MENU = [None], [None]      # 让 wfp 认识两个窗口（简易闭包）


def capture_of(tid):
    """GetGUIThreadInfo → 该 GUI 线程当前的 capture / focus 窗口。"""
    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                    ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                    ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
                    ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
                    ("rcCaret", W.RECT)]
    g = GUITHREADINFO()
    g.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not U.GetGUIThreadInfo(wt.DWORD(tid), ctypes.byref(g)):
        return None
    return g


def main():
    click_idx = None
    if "--click" in sys.argv:
        click_idx = int(sys.argv[sys.argv.index("--click") + 1])

    pet = find(PET_CLASS)
    if not pet:
        print("❌ 桌宠没在跑（找不到 %s）" % PET_CLASS)
        return 2
    X_PET[0] = pet
    U.WindowFromPoint.restype = wt.HWND
    cur = W.POINT()                 # ⚠️ 用模块自己的 POINT：argtypes 就是它声明的
    U.GetCursorPos(ctypes.byref(cur))
    print("桌宠 hwnd=0x%X rect=%s" % (pet, wrect(pet)))
    print("光标原位置 (%d,%d)" % (cur.x, cur.y))

    print("\nS1 向桌宠 PostMessage(WM_RBUTTONUP) 弹真实菜单…")
    U.PostMessageW(pet, WM_RBUTTONUP, 0, 0)
    menu = None
    for _ in range(30):
        time.sleep(0.1)
        menu = find(MENU_CLASS)
        if menu:
            break
    if not menu:
        print("❌ 菜单没弹出来")
        return 3
    X_MENU[0] = menu
    _l, _t, _r, _b = wrect(menu)                  # ⚠️ wrect 返回 left/top/right/bottom
    l, t, w, h = _l, _t, _r - _l, _b - _t
    print("   菜单 hwnd=0x%X rect=%s (%dx%d)" % (menu, wrect(menu), w, h))

    print("\nS1b 菜单窗口自己的样式位（关键：TOPMOST 位在不在）")
    ex = U.GetWindowLongW(menu, -20) & 0xFFFFFFFF
    print("   exstyle=0x%08X  %s" % (ex, ex_flags_of(ex)))
    print("   IsWindowVisible=%s  WS_VISIBLE=%s"
          % (bool(U.IsWindowVisible(menu)),
             bool(U.GetWindowLongW(menu, -16) & 0x10000000)))
    print("   → %s" % ("✅ 带 TOPMOST 位" if ex & 0x8 else
                       "🔴 **没有 TOPMOST 位** —— CreateWindowExW 的 WS_EX_TOPMOST 被忽略了！"
                       "窗口落在普通层，必被常驻 topmost 的桌宠压住"))

    print("\nS1c 立方网格命中探针（菜单区域 5×5，看整片是不是都不接受鼠标）")
    grid = {}
    for iy in range(5):
        row = []
        for ix in range(5):
            px = l + w * (0.1 + 0.2 * ix)
            py = t + h * (0.1 + 0.2 * iy)
            row.append(wfp(px, py))
        grid[iy] = row
        print("   y=%.0f%%: %s" % (h and (10 + 20 * iy), " | ".join(row)))

    print("\nS2 z 序（间隔 2.5s 两次，看桌宠会不会爬上来）")
    ok1 = probe_z(pet, menu, "刚弹出")
    time.sleep(2.5)
    ok2 = probe_z(pet, menu, "2.5s 后")

    print("\nS2b 菜单开着时，桌宠还在动吗（PrintWindow 抓桌宠像素，间隔 0.8s 两次）")
    _a = win_hash(pet)
    time.sleep(0.8)
    _b = win_hash(pet)
    print("   桌宠像素 hash：%s → %s  %s"
          % (_a, _b, "✅ 逐帧在动（没冻住）" if _a != _b else "🔴 像素没变（冻住了）"))
    anim_ok = (_a != _b)

    scale = menu_scale(w)
    print("   几何：l=%s t=%s w=%s h=%s scale=%.3f（窗口 rect=%s）"
          % (l, t, w, h, scale, wrect(menu)))

    print("\nS3a 命中归属（WindowFromPoint）：菜单区域内的点到底属于谁")
    for idx, name in ((1, "第1项"), (4, "第4项"), (0, "第0项")):
        p = item_center(l, t, idx, scale)
        print("   %s (%.0f,%.0f) → %s" % (name, p[0], p[1], wfp(p[0], p[1])))

    print("\nS3b 捕获状态（GetGUIThreadInfo）：菜单有没有拿到鼠标 capture")
    pid = wt.DWORD()
    tid = U.GetWindowThreadProcessId(pet, ctypes.byref(pid))
    g = capture_of(tid)
    if g is None:
        print("   GetGUIThreadInfo 失败（跨进程常有的限制）")
    else:
        cap = g.hwndCapture
        who = "菜单 ✅" if cap == menu else ("桌宠" if cap == pet else "0x%X(%s)"
                                            % (cap, cls_name(cap) if cap else "None"))
        print("   桌宠线程 tid=%s  hwndCapture=%s  → %s" % (tid, cap, who))
        print("   （capture 在菜单 = 全屏鼠标消息都该给菜单 → hover 理应生效）")

    print("\nS3c 子菜单「停留才展开」：先扫出哪个条目带子菜单，再验延迟")
    def _n_menus():
        n, h = 0, U.GetTopWindow(None)
        while h:
            if U.IsWindowVisible(h) and cls_name(h) == MENU_CLASS:
                n += 1
            h = U.GetWindow(h, GW_HWNDNEXT)
        return n

    def _move_to_row(i):
        cx, cy = item_client_xy(scale, i)
        pr = wrect(menu)
        U.SetCursorPos(int(pr[0] + cx), int(pr[1] + cy))

    sub_row = None
    for i in range(1, 15):
        _move_to_row(i)
        time.sleep(0.35)
        if _n_menus() >= 2:
            sub_row = i
            break
    if sub_row is None:
        print("   ⚠️ 没找到带子菜单的条目（菜单项变了？）")
    else:
        _move_to_row(0)                       # 先移开，收起子菜单
        time.sleep(0.4)
        _move_to_row(sub_row)
        time.sleep(0.05)                       # 刚进入：不该展开
        _early = _n_menus()
        time.sleep(0.45)                       # 停留够久：应展开
        _late = _n_menus()
        print("   带子菜单的条目 = 第 %d 行" % sub_row)
        print("   刚移上去 50ms：菜单窗口 %d 个  %s"
              % (_early, "✅ 还没展开（停留才开）" if _early < 2 else "🔴 立刻展开了"))
        print("   停留 500ms 后：菜单窗口 %d 个  %s"
              % (_late, "✅ 已展开" if _late >= 2 else "🔴 没展开"))
        _move_to_row(0)
        time.sleep(0.3)

    print("\nS4 hover 三连测（像素哈希）")
    h0, _ = menu_hash(menu)
    # ① PostMessage 直接喂 WM_MOUSEMOVE（验证 PrintWindow 是否可信）
    cx, cy = item_client_xy(scale, 1)
    U.PostMessageW(menu, 0x0200, 0, (int(cy) << 16) | (int(cx) & 0xFFFF))
    time.sleep(0.35)
    hs, _ = menu_hash(menu)
    print("   ①PostMessage(WM_MOUSEMOVE) → hash=%s  %s"
          % (hs, "✅ 变了（PrintWindow 可信）" if hs != h0 else "🔴 没变（PrintWindow 不可信）"))
    # ② 真实光标移动（不按任何键）—— 用户报障就是这个：不按键收不到 hover
    p1 = item_center(l, t, 4, scale)
    U.SetCursorPos(int(p1[0]), int(p1[1]))
    time.sleep(0.4)
    h1, _ = menu_hash(menu)
    print("   ②真实光标移到第4项（不按任何键）→ hash=%s  %s"
          % (h1, "✅ 高亮生效" if h1 != hs else "🔴 无变化（真机 input 没到菜单）"))
    # ③ 移回第 1 项，确认高亮是跟着光标走的（不是碰巧变了）
    p2 = item_center(l, t, 1, scale)
    U.SetCursorPos(int(p2[0]), int(p2[1]))
    time.sleep(0.4)
    h2, _ = menu_hash(menu)
    print("   ③再移回第1项 → hash=%s  %s"
          % (h2, "✅ 跟着光标走" if h2 != h1 else "🔴 没跟着动"))
    hover_ok = (h1 != hs and h2 != h1)

    if click_idx is not None:
        print("\nS5 合成左键点击第 %d 项（条目中心），随后用事件日志核对…" % click_idx)
        pc = item_center(l, t, click_idx, scale)
        U.SetCursorPos(int(pc[0]), int(pc[1]))
        time.sleep(0.15)
        U.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
        time.sleep(0.06)
        U.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP
        time.sleep(0.8)
        tail = []
        try:
            with open(EVENTS, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-6:]
        except OSError:
            pass
        print("   事件日志尾部：")
        for ln in tail:
            print("     " + ln.rstrip())
        print("   （看到 menu_* 事件 = 命令分发成功；只有 double_click = 点击被桌宠吃了）")

    print("\nS6 收菜单（Esc ×N：子菜单先关再关主菜单）+ 还原光标")
    for _ in range(4):
        if not find(MENU_CLASS):
            break
        U.PostMessageW(menu, WM_KEYDOWN, VK_ESCAPE, 0)
        time.sleep(0.35)
    U.SetCursorPos(cur.x, cur.y)
    alive = find(MENU_CLASS)
    print("   菜单 %s；光标还原到 (%d,%d)"
          % ("已关闭 ✅" if not alive else "★仍然存在（Esc 没生效）", cur.x, cur.y))
    print("\n结论：z 序 %s ；hover %s ；菜单开着时人物 %s"
          % ("OK" if (ok1 and ok2) else "🔴 桌宠压在菜单上",
             "OK（无需按住任何键）" if hover_ok else "🔴 不按键收不到 hover",
             "继续动 ✅" if anim_ok else "🔴 冻住"))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
