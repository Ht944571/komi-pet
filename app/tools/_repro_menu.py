# -*- coding: utf-8 -*-
r"""最小 repro：owner-draw 菜单在「普通窗口」上是否正常（对照桌宠 MA_NOACTIVATE 窗口）。

普通窗口 + 同款 owner-draw 菜单 → TrackPopupMenu → 查 #32768 尺寸：
  若 ~272px 宽 = measure 正常 → 问题锁定在桌宠窗口特性（无激活/分层）
  若同样 19px = measure 协议本身在我们环境下不工作 → 需换宿主窗口方案
"""
import ctypes
import time
import ctypes.wintypes as wt

u = ctypes.WinDLL("user32")
g = ctypes.WinDLL("gdi32")
k = ctypes.WinDLL("kernel32")

WM_MEASUREITEM, WM_DRAWITEM, WM_RBUTTONUP, WM_CLOSE = 0x211, 0x21B, 0x205, 0x10
MF_OWNERDRAW, MF_CHECKED, MF_GRAYED, MF_SEPARATOR, MF_POPUP = 0x100, 8, 1, 0x800, 0x10
TPM_RIGHTBUTTON, TPM_RETURNCMD = 2, 0x100
MIM_BACKGROUND, MIM_APPLYTOSUBMENUS = 2, 0x80000000


class MENUINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("dwMask", wt.DWORD), ("dwStyle", wt.DWORD),
                ("cyMax", wt.UINT), ("hbrBack", wt.HBRUSH), ("dwContextHelpID", wt.DWORD),
                ("dwMenuData", ctypes.c_size_t)]


class MEASUREITEMSTRUCT(ctypes.Structure):
    _fields_ = [("CtlType", wt.UINT), ("CtlID", wt.UINT), ("itemID", wt.UINT),
                ("itemWidth", wt.UINT), ("itemHeight", wt.UINT), ("itemData", ctypes.c_size_t)]


u.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, ctypes.c_size_t]
u.SetMenuInfo.argtypes = [wt.HMENU, ctypes.POINTER(MENUINFO)]

ITEMS = [{"label": "打开完整看板"}, {"label": "想法气泡", "checked": True},
         {"label": "退出古见同学"}]
state = {"measures": 0, "draws": 0, "lps": []}


def wndproc(hwnd, msg, wp, lp):
    if msg == WM_MEASUREITEM:
        state["measures"] += 1
        state["lps"].append(lp)
        if lp:
            mis = ctypes.cast(lp, ctypes.POINTER(MEASUREITEMSTRUCT)).contents
            mis.itemWidth, mis.itemHeight = 272, 32
            return 1
        return 0
    if msg == WM_DRAWITEM:
        state["draws"] += 1
        return 1
    if msg == WM_RBUTTONUP:
        menu = u.CreatePopupMenu()
        for i, it in enumerate(ITEMS):
            u.AppendMenuW(menu, MF_OWNERDRAW | (MF_CHECKED if it.get("checked") else 0),
                          1000 + i, i + 1)
        hbr = g.CreateSolidBrush(0xFFFFFF)
        mi = MENUINFO(ctypes.sizeof(MENUINFO))
        mi.dwMask, mi.hbrBack = MIM_BACKGROUND | MIM_APPLYTOSUBMENUS, hbr
        u.SetMenuInfo(menu, ctypes.byref(mi))
        u.SetForegroundWindow(hwnd)
        cmd = u.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, 500, 500, 0, hwnd, None)
        u.DestroyMenu(menu)
        state["cmd"] = cmd
        u.PostQuitMessage(0)
        return 0
    return u.DefWindowProcW(hwnd, msg, wp, lp)


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
cb = WNDPROC(wndproc)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


wc = WNDCLASSW()
wc.lpfnWndProc = cb
wc.lpszClassName = "ReproMenuOwner"
wc.hInstance = k.GetModuleHandleW(None)
u.RegisterClassW(ctypes.byref(wc))
u.CreateWindowExW.restype = wt.HWND
u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
hwnd = u.CreateWindowExW(0, wc.lpszClassName, "repro", 0x90C00000,  # WS_OVERLAPPEDWINDOW
                         300, 300, 300, 200, None, None, wc.hInstance, None)
u.ShowWindow(hwnd, 5)
time.sleep(0.3)

# 后台线程：1.2s 后量菜单尺寸并发 ESC 关闭（否则模态循环永不返回）
import threading


def closer():
    time.sleep(1.2)
    mh = u.FindWindowW("#32768", None)
    if mh and u.IsWindowVisible(mh):
        r = wt.RECT()
        u.GetWindowRect(mh, ctypes.byref(r))
        state["menu_size"] = (r.right - r.left, r.bottom - r.top)
        u.PostMessageW(mh, 0x0100, 0x1B, 0)   # ESC down
        time.sleep(0.1)
        u.PostMessageW(mh, 0x0101, 0x1B, 0)   # ESC up


threading.Thread(target=closer, daemon=True).start()
u.PostMessageW(hwnd, WM_RBUTTONUP, 0, 0)      # 触发菜单

msg = wt.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg))
    u.DispatchMessageW(ctypes.byref(msg))
u.DestroyWindow(hwnd)

# 期间菜单弹出（模态），在 wndproc TrackPopupMenu 返回后 PostQuitMessage 退出循环
print(f"measures={state['measures']} draws={state['draws']} cmd={state.get('cmd')}")
print("measure lparams:", [hex(x) for x in state["lps"][:5]])
mh = u.FindWindowW("#32768", None)
print("菜单窗口残留:", mh)
