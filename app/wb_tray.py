# -*- coding: utf-8 -*-
r"""wb_tray.py — 3D 桌宠的托盘图标与菜单

为什么必须有：桌宠窗口是 `WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW`（无边框、不抢焦点、
不进任务栏），**没有任何系统级的关闭入口**，键盘也进不来。
没有托盘就等于"只能用任务管理器杀掉"。

提供一个最小但完整的控制面：
    · 左键点托盘 = 打一次招呼（气泡）
    · 右键 = 菜单：气泡 / 随机走动 / 跟随鼠标 / 退出

用法：
    from wb_tray import Tray
    tray = Tray(pet)          # pet 需要有 say() / enable_wander / enable_follow / quit()
    tray.pump()               # 每帧调用（处理托盘消息）
    tray.remove()             # 退出时移除图标
"""
import ctypes
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
sh32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_USER = 0x0400
WM_TRAY = WM_USER + 1
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
HWND_MESSAGE = -3
NIM_ADD, NIM_DELETE, NIM_MODIFY = 0x00000000, 0x00000002, 0x00000001
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x00000001, 0x00000002, 0x00000004
TPM_RIGHTALIGN, TPM_BOTTOMALIGN = 0x0008, 0x0020
TPM_RETURNCMD = 0x0100
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_UNCHECKED = 0x0000, 0x0800, 0x0008, 0x0000
ID_SAY, ID_WANDER, ID_FOLLOW, ID_QUIT = 1001, 1002, 1003, 1099


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uTimeout", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


u32.DefWindowProcW.restype = ctypes.c_longlong
u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
u32.RegisterClassW.restype = wintypes.WORD
u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
u32.LoadIconW.restype = wintypes.HANDLE
u32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
sh32.Shell_NotifyIconW.restype = wintypes.BOOL
sh32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
u32.CreatePopupMenu.restype = wintypes.HMENU
u32.AppendMenuW.restype = wintypes.BOOL
u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT, wintypes.LPCWSTR]
u32.CheckMenuItem.restype = wintypes.DWORD
u32.CheckMenuItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
u32.TrackPopupMenu.restype = wintypes.BOOL
u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.GetCursorPos.restype = wintypes.BOOL
u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
u32.SetForegroundWindow.restype = wintypes.BOOL
u32.SetForegroundWindow.argtypes = [wintypes.HWND]
u32.PostMessageW.restype = wintypes.BOOL
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.GetSystemMetrics.restype = ctypes.c_int
u32.GetSystemMetrics.argtypes = [ctypes.c_int]

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class Tray:
    _instances = {}

    def __init__(self, pet, tip="古见同学 3D"):
        self.pet = pet
        self.hwnd = None
        self.menu = None
        self._build_menu()
        self._create_window()
        self._add_icon(tip)
        Tray._instances[id(self)] = self

    # ---------- 菜单 ----------
    def _build_menu(self):
        self.menu = u32.CreatePopupMenu()
        u32.AppendMenuW(self.menu, MF_STRING, ID_SAY, "打个招呼")
        u32.AppendMenuW(self.menu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(self.menu, MF_STRING, ID_WANDER, "随机走动")
        u32.AppendMenuW(self.menu, MF_STRING, ID_FOLLOW, "跟随鼠标")
        u32.AppendMenuW(self.menu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(self.menu, MF_STRING, ID_QUIT, "退出古见同学")
        self._sync_checks()

    def _sync_checks(self):
        if not self.menu:
            return
        for mid, on in ((ID_WANDER, getattr(self.pet, "enable_wander", False)),
                        (ID_FOLLOW, getattr(self.pet, "enable_follow", False))):
            u32.CheckMenuItem(self.menu, mid, MF_CHECKED if on else MF_UNCHECKED)

    def _popup(self):
        p = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(p))
        u32.SetForegroundWindow(self.hwnd)
        cmd = u32.TrackPopupMenu(self.menu, TPM_RIGHTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD,
                                 p.x, p.y, 0, self.hwnd, None)
        self._on_command(cmd)

    def _on_command(self, cmd):
        if cmd == ID_SAY:
            self.pet.say("在这儿呢～", 2.6)
        elif cmd == ID_WANDER:
            self.pet.enable_wander = not self.pet.enable_wander
            self._sync_checks()
            self.pet.say("随机走动：" + ("开" if self.pet.enable_wander else "关"), 1.8)
        elif cmd == ID_FOLLOW:
            self.pet.enable_follow = not self.pet.enable_follow
            self._sync_checks()
            self.pet.say("跟随鼠标：" + ("开" if self.pet.enable_follow else "关"), 1.8)
        elif cmd == ID_QUIT:
            self.pet.running = False

    # ---------- 消息窗口 ----------
    def _create_window(self):
        hinst = k32.GetModuleHandleW(None)

        def proc(hwnd, msg, wp, lp):
            try:
                if msg == WM_TRAY:
                    if lp in (WM_RBUTTONUP, 0x0205):
                        self._popup()
                    elif lp == WM_LBUTTONUP:
                        self.pet.say("在这儿呢～", 2.6)
                    return 0
                return u32.DefWindowProcW(hwnd, msg, wp, lp)
            except BaseException:
                return u32.DefWindowProcW(hwnd, msg, wp, lp)

        # ⚠️ 回调必须是**模块级单例**，否则被 GC 后 Windows 跳进垃圾内存 → 崩溃
        if not hasattr(Tray, "_proc_stub"):
            Tray._proc_stub = WNDPROC(proc)
        self._proc = Tray._proc_stub

        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p).value
        wc.hInstance = hinst
        wc.lpszClassName = "WBPet3DTrayClass"
        u32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = u32.CreateWindowExW(0, "WBPet3DTrayClass", "tray", 0,
                                        0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE),
                                        None, hinst, None)

    def _add_icon(self, tip):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = u32.LoadIconW(None, 32512)     # IDI_APPLICATION
        nid.szTip = tip[:127]
        sh32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def remove(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        sh32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        Tray._instances.pop(id(self), None)
