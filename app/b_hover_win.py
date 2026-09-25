#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
b_hover_win.py — WorkBuddy 悬浮球 Windows 原生版（纯 ctypes + GDI+，零第三方依赖）
================================================================================
与 macOS 版（b_hover.py / Cocoa NSPanel）行为对齐：

  1. 真圆形悬浮球：WS_EX_LAYERED 分层窗口 + GDI+ 抗锯齿绘制，边缘不是方块
  2. 永远置顶（HWND_TOPMOST）；无边框无标题栏；不抢焦点（WM_MOUSEACTIVATE → MA_NOACTIVATE）
  3. 不进任务栏、不进 Alt-Tab（WS_EX_TOOLWINDOW）
  4. 1 秒轮询：活跃对话数（workbuddy.db sessions.status='working'）
  5. 单击 → 活跃对话面板：本轮积分 / tokens / 请求内容 / 用时
  6. 双击 → 浏览器打开完整看板
  7. 按住拖动，位置自动记忆（.hover_pos.json）
  8. 右键菜单：打开看板 / 退出

球上数字 = 当前活跃对话数；右上角状态点：绿=看板服务正常，红=服务未响应。
取数 / 格式化 / 卡片文案全部走 wb_hover_core.py，与 macOS 版同一份口径。

依赖：仅 Python 标准库（ctypes）。gdiplus.dll / user32.dll 为 Windows 系统自带。
启动：pythonw b_hover_win.py        （pythonw 无控制台窗口）
      python  b_hover_win.py --seconds 20   （冒烟测试：20 秒后自动退出）
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes as wt
from urllib.parse import urlparse

# ---------- 公共层 ----------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wb_hover_core import (                                   # noqa: E402
    load_paths, query_db, api_healthy,
    load_pos, save_pos, report_state, log_exception, POLL_SEC, ERR_LOG,
    fmt_tokens, fmt_duration, fmt_ago,
)

# ---------- 尺寸 / 定时 ----------
BALL = 88                 # 球体直径（与 macOS 版一致）
POPUP_W, POPUP_H = 440, 574
CLICK_DELAY_MS = 220      # 单击判定延迟（等待是否出现第二次点击）
TICK_MS = int(POLL_SEC * 1000)
WATCH_MS = 40             # 面板显示期间的"点击外部"检测间隔（≤40ms 才能捕获到快速单击的按下瞬间）
ID_TIMER_TICK, ID_TIMER_CLICK, ID_TIMER_WATCH = 1, 2, 3
EVENT_LOG = os.path.join(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "C:/Windows/Temp",
                         "wb-hover.events.log")   # 交互/自愈事件日志（可验证事件绑定与回调）
DASH_READY_WAIT = 8.0     # 拉起看板服务后最多等待其就绪的秒数

# 面板排版
POPUP_RADIUS = 16         # 面板外圆角
CARD_H, CARD_GAP = 96, 10
CARD_STEP = CARD_H + CARD_GAP
CARD_TOP = 80             # 第一张卡片的上缘（头部区之下）
MAX_CARDS = 4             # 面板最多展示条数（超出截断，保持可读）
HINT_Y = POPUP_H - 30     # 底部提示行

# ---------- 配色（ARGB，与 dashboard.html 暗蓝科技风一致）----------
C_BG = 0xFA0F172A        # --bg    #0f172a
C_CARD = 0xFF1E293B      # --card  #1e293b
C_LINE = 0xFF2D3A52      # --line  #2d3a52
C_LINE_DIM = 0x662D3A52  # 弱化分隔线（低 alpha）
C_BLUE = 0xFF60A5FA      # 强调蓝  #60a5fa
C_GRAY = 0xFF8EA0B8      # 次级文字 #8ea0b8
C_TXT = 0xFFE2E8F0       # 主文字  #e2e8f0
C_ONLINE = 0xFF3ED68A
C_OFFLINE = 0xFFFF6B6B

# ---------- Win32 常量 ----------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WM_TIMER = 0x0113
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_MOUSEMOVE = 0x0200
WM_MOUSEACTIVATE = 0x0021
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NULL = 0x0000
MA_NOACTIVATE = 3
MK_LBUTTON = 0x0001
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
SW_SHOWNA = 8
SW_HIDE = 0
ULW_ALPHA = 0x00000002
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
SPI_GETWORKAREA = 0x0030
MF_STRING = 0x00000000
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100
SMOOTHING_ANTIALIAS = 4
TEXT_HINT_ANTIALIAS = 4
PIXEL_OFFSET_HIGHQUALITY = 2
UNIT_PIXEL = 2
FONT_REGULAR, FONT_BOLD = 0, 1
IDM_DASHBOARD, IDM_QUIT = 1001, 1002
CLASS_NAME = "WBHoverBallClass"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    _gdiplus = ctypes.WinDLL("gdiplus", use_last_error=True)
except OSError:                                              # pragma: no cover
    _gdiplus = None


def _set_dpi_aware():
    """声明高 DPI 感知，否则高分屏上球体会被系统拉伸成模糊的马赛克。"""
    try:  # Win10 1607+ / Win11
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:  # Win8.1+
        _shcore = ctypes.WinDLL("shcore", use_last_error=True)
        _shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:  # WinVista+
        _user32.SetProcessDPIAware()
    except Exception:
        pass


# ---------- 结构体 ----------
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", POINT)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class RectF(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float),
                ("width", ctypes.c_float), ("height", ctypes.c_float)]


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", ctypes.c_int), ("SuppressExternalCodecs", ctypes.c_int)]


# ---------- 函数签名 ----------
_user32.CreateWindowExW.restype = wt.HWND
_user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
_user32.GetDC.restype = wt.HDC
_user32.GetDC.argtypes = [wt.HWND]
_user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
_user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wt.UINT]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
_user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(POINT),
                                        ctypes.POINTER(SIZE), wt.HDC, ctypes.POINTER(POINT),
                                        wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
_user32.CreatePopupMenu.restype = wt.HMENU
_user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, ctypes.POINTER(RECT)]
_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.CreateDIBSection.restype = wt.HBITMAP
_gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
_gdi32.SelectObject.restype = wt.HGDIOBJ
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
_kernel32.GetModuleHandleW.restype = wt.HINSTANCE
_kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

# 不声明签名时 ctypes 会把 Python int 当 32 位 C int 传参，遇到 64 位句柄/指针即 OverflowError，
# 因此下面这些高频调用统一声明 argtypes。
# 注：在 WINFUNCTYPE 回调里调用带 argtypes 的函数是安全的；历史"回调内崩栈"的判断有误，
# 真正根因是注册进窗口类的回调桩被 GC 释放（见 _WNDPROC_STUB 处的详细注释）。
_user32.DefWindowProcW.restype = ctypes.c_ssize_t
_user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_user32.LoadCursorW.restype = wt.HANDLE
_user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_user32.DestroyWindow.argtypes = [wt.HWND]
_user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_user32.SetTimer.restype = wt.UINT_PTR if hasattr(wt, "UINT_PTR") else ctypes.c_size_t
_user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
_user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
_user32.SetCapture.argtypes = [wt.HWND]
_user32.ReleaseCapture.restype = ctypes.c_int
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetDoubleClickTime.restype = wt.UINT
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.SetForegroundWindow.argtypes = [wt.HWND]
_user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
_user32.DestroyMenu.argtypes = [wt.HMENU]
_gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wt.HDC]


def _gp(name, argtypes):
    """取 GDI+ 函数并声明签名（返回 GpStatus）。"""
    fn = getattr(_gdiplus, name)
    fn.argtypes = argtypes
    fn.restype = ctypes.c_int
    return fn


P = ctypes.c_void_p
F = ctypes.c_float
U32 = ctypes.c_uint32
INT = ctypes.c_int

if _gdiplus is not None:
    _GdiplusStartup = _gp("GdiplusStartup", [P, P, P])
    _GdiplusShutdown = _gp("GdiplusShutdown", [P])
    _CreateFromHDC = _gp("GdipCreateFromHDC", [wt.HDC, P])
    _DeleteGraphics = _gp("GdipDeleteGraphics", [P])
    _SetSmoothing = _gp("GdipSetSmoothingMode", [P, INT])
    _SetTextHint = _gp("GdipSetTextRenderingHint", [P, INT])
    _SetPixelOffset = _gp("GdipSetPixelOffsetMode", [P, INT])
    _GraphicsClear = _gp("GdipGraphicsClear", [P, U32])
    _SolidFill = _gp("GdipCreateSolidFill", [U32, P])
    _DeleteBrush = _gp("GdipDeleteBrush", [P])
    _CreatePen = _gp("GdipCreatePen1", [U32, F, INT, P])
    _DeletePen = _gp("GdipDeletePen", [P])
    _FillEllipse = _gp("GdipFillEllipse", [P, P, F, F, F, F])
    _DrawEllipse = _gp("GdipDrawEllipse", [P, P, F, F, F, F])
    _FillRect = _gp("GdipFillRectangle", [P, P, F, F, F, F])
    _CreatePath = _gp("GdipCreatePath", [INT, P])
    _DeletePath = _gp("GdipDeletePath", [P])
    _AddPathArc = _gp("GdipAddPathArc", [P, F, F, F, F, F, F])
    _AddPathLine = _gp("GdipAddPathLine", [P, F, F, F, F])
    _CloseFigure = _gp("GdipClosePathFigure", [P])
    _FillPath = _gp("GdipFillPath", [P, P, P])
    _DrawPath = _gp("GdipDrawPath", [P, P, P])
    _CreateFamily = _gp("GdipCreateFontFamilyFromName", [wt.LPCWSTR, P, P])
    _GenericFamily = _gp("GdipGetGenericFontFamilySansSerif", [P])
    _DeleteFamily = _gp("GdipDeleteFontFamily", [P])
    _CreateFont = _gp("GdipCreateFont", [P, F, INT, INT, P])
    _DeleteFont = _gp("GdipDeleteFont", [P])
    _CreateStringFormat = _gp("GdipCreateStringFormat", [INT, ctypes.c_ushort, P])
    _SetAlign = _gp("GdipSetStringFormatAlign", [P, INT])
    _SetLineAlign = _gp("GdipSetStringFormatLineAlign", [P, INT])
    _SetFormatFlags = _gp("GdipSetStringFormatFlags", [P, INT])
    _DeleteStringFormat = _gp("GdipDeleteStringFormat", [P])
    _DrawString = _gp("GdipDrawString", [P, wt.LPCWSTR, INT, P, P, P, P])
    _MeasureString = _gp("GdipMeasureString", [P, wt.LPCWSTR, INT, P, P, P, P, P, P])
    _UpdateLayered = _user32.UpdateLayeredWindow

_FONT_FAMILY = None
_STR_NOWRAP = 0x00001000


def _family():
    """中文字体：优先微软雅黑；都失败则退回 GDI+ 默认无衬线族。"""
    global _FONT_FAMILY
    if _FONT_FAMILY is not None:
        return _FONT_FAMILY
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI"):
        fam = P()
        if _CreateFamily(name, None, ctypes.byref(fam)) == 0:
            _FONT_FAMILY = fam
            return fam
    _FONT_FAMILY = P()
    _GenericFamily(ctypes.byref(_FONT_FAMILY))
    return _FONT_FAMILY


# ---------- GDI+ 画布（32bpp 位图，用于 UpdateLayeredWindow）----------
class Surface:
    """离屏画布。所有绘制 API 用『自上而下』坐标，内部转换成 GDI 的自下而上坐标。

    字体 / 画笔 / 格式都缓存复用——每帧 alloc/free 大量 GDI+ 资源曾触发 DispatchMessageW
    路径上的访问违例（Python 3.13 ctypes + GDI+ 的极端组合）。"""
    _fonts = {}       # 类级共享字体缓存：(family_id, size, bold) -> GpFont*
    _fmt_cache = {}   # {(center:bool)} -> GpStringFormat*

    def __init__(self, w, h):
        self.w, self.h = w, h
        hdc_screen = _user32.GetDC(None)
        self.hdc = _gdi32.CreateCompatibleDC(hdc_screen)
        _user32.ReleaseDC(None, hdc_screen)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = h                 # 正数 = 自下而上
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        self.bits = ctypes.c_void_p()
        self.hbm = _gdi32.CreateDIBSection(self.hdc, ctypes.byref(bi), DIB_RGB_COLORS,
                                           ctypes.byref(self.bits), None, 0)
        if not self.hbm:
            raise RuntimeError("CreateDIBSection 失败，无法创建悬浮球画布")
        self.old = _gdi32.SelectObject(self.hdc, self.hbm)
        self.g = P()
        _CreateFromHDC(self.hdc, ctypes.byref(self.g))
        _SetSmoothing(self.g, SMOOTHING_ANTIALIAS)
        _SetTextHint(self.g, TEXT_HINT_ANTIALIAS)
        _SetPixelOffset(self.g, PIXEL_OFFSET_HIGHQUALITY)

    # ---- 缓存的资源（多 Surface 共享同一份 GDI+ 资源）----
    def _font(self, size, bold):
        key = (size, bold)
        f = self._fonts.get(key)
        if f is None:
            f = P()
            _CreateFont(_family(), float(size), FONT_BOLD if bold else FONT_REGULAR,
                        UNIT_PIXEL, ctypes.byref(f))
            self._fonts[key] = f
        return f

    def _format(self, center):
        key = (bool(center),)
        f = self._fmt_cache.get(key)
        if f is None:
            f = P()
            _CreateStringFormat(0, 0, ctypes.byref(f))
            _SetFormatFlags(f, _STR_NOWRAP)
            _SetAlign(f, 1 if center else 0)
            _SetLineAlign(f, 1)
            self._fmt_cache[key] = f
        return f

    # ---- 内部：坐标换算 ----
    def _fy(self, y, hh):
        # GDI+ 在内存 DC 上的逻辑坐标本身就是自上而下（原点位图左上），
        # 无需再翻转——翻转会导致整个画面上下颠倒（2026-09-06 经像素审计证实）。
        return y

    def clear(self):
        _GraphicsClear(self.g, 0x00000000)

    def ellipse(self, argb, x, y, w, h, line_argb=None, line_w=1.0):
        b = P(); _SolidFill(argb, ctypes.byref(b))
        _FillEllipse(self.g, b, F(float(x)), F(float(self._fy(y, h))), F(float(w)), F(float(h)))
        _DeleteBrush(b)
        if line_argb is not None:
            p = P(); _CreatePen(line_argb, float(line_w), UNIT_PIXEL, ctypes.byref(p))
            _DrawEllipse(self.g, p, F(float(x)), F(float(self._fy(y, h))),
                         F(float(w)), F(float(h)))
            _DeletePen(p)

    def round_rect(self, argb, x, y, w, h, r, line_argb=None, line_w=1.0):
        path = P(); _CreatePath(0, ctypes.byref(path))
        d2 = 2.0 * r
        yy = self._fy(y, h)
        _AddPathArc(path, F(x), F(yy), F(d2), F(d2), F(180.0), F(90.0))
        _AddPathLine(path, F(x + r), F(yy), F(x + w - r), F(yy))
        _AddPathArc(path, F(x + w - d2), F(yy), F(d2), F(d2), F(270.0), F(90.0))
        _AddPathLine(path, F(x + w), F(yy + r), F(x + w), F(yy + h - r))
        _AddPathArc(path, F(x + w - d2), F(yy + h - d2), F(d2), F(d2), F(0.0), F(90.0))
        _AddPathLine(path, F(x + w - r), F(yy + h), F(x + r), F(yy + h))
        _AddPathArc(path, F(x), F(yy + h - d2), F(d2), F(d2), F(90.0), F(90.0))
        _CloseFigure(path)
        b = P(); _SolidFill(argb, ctypes.byref(b))
        _FillPath(self.g, b, path)
        _DeleteBrush(b)
        if line_argb is not None:
            p = P(); _CreatePen(line_argb, float(line_w), UNIT_PIXEL, ctypes.byref(p))
            _DrawPath(self.g, p, path)
            _DeletePen(p)
        _DeletePath(path)

    def hline(self, argb, x, y, w2, h=1):
        """水平细线（实心矩形，抗锯齿关闭场景下边缘 1px 已够用）。"""
        b = P(); _SolidFill(argb, ctypes.byref(b))
        _FillRect(self.g, b, F(float(x)), F(float(self._fy(y, h))), F(float(w2)), F(float(h)))
        _DeleteBrush(b)

    def vline(self, argb, x, y, h2, w2=1):
        b = P(); _SolidFill(argb, ctypes.byref(b))
        _FillRect(self.g, b, F(float(x)), F(float(self._fy(y, h2))), F(float(w2)), F(float(h2)))
        _DeleteBrush(b)

    def measure(self, s, size, bold=False):
        font = self._font(size, bold)
        fmt = self._format(False)
        layout = RectF(0, 0, 100000.0, float(size * 3))
        bb = RectF()
        cp, ln = INT(0), INT(0)
        _MeasureString(self.g, s, -1, font, ctypes.byref(layout), fmt,
                       ctypes.byref(bb), ctypes.byref(cp), ctypes.byref(ln))
        return bb.width, bb.height

    def fit(self, s, size, bold, maxw):
        if maxw and self.measure(s, size, bold)[0] > maxw:
            lo, hi = 0, len(s)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self.measure(s[:mid] + "…", size, bold)[0] <= maxw:
                    lo = mid
                else:
                    hi = mid - 1
            s = s[:lo] + "…"
        return s

    def text(self, s, x, y, size, argb, bold=False, maxw=None, center=False):
        if not s:
            return
        s = self.fit(s, size, bold, maxw)
        font = self._font(size, bold)
        fmt = self._format(center)
        bh = size * 1.45
        rect = RectF(0.0 if center else float(x), F(float(self._fy(y, bh))),
                     float(self.w) if center else float(maxw or (self.w - x)), F(float(bh)))
        b = P(); _SolidFill(argb, ctypes.byref(b))
        _DrawString(self.g, s, -1, font, ctypes.byref(rect), fmt, b)
        _DeleteBrush(b)

    def text_right(self, s, x_right, y, size, argb, bold=False):
        """右对齐文本：以 (x_right, y) 为右上锚点。"""
        if not s:
            return
        tw = self.measure(s, size, bold)[0]
        self.text(s, int(x_right - tw), y, size, argb, bold=bold)

    def ctext(self, s, cx, y, size, argb, bold=False):
        """在指定水平中心点 cx 处居中画一行文本。"""
        if not s:
            return
        tw = self.measure(s, size, bold)[0]
        self.text(s, int(cx - tw / 2), y, size, argb, bold=bold)

    def present(self, hwnd):
        size = SIZE(self.w, self.h)
        src = POINT(0, 0)
        r = RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        dst = POINT(r.left, r.top)
        blend = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
        hdc_screen = _user32.GetDC(None)
        ok = _UpdateLayered(hwnd, hdc_screen, ctypes.byref(dst), ctypes.byref(size),
                            self.hdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        _user32.ReleaseDC(None, hdc_screen)
        return ok

    def close(self):
        try:
            _DeleteGraphics(self.g)
        except Exception:
            pass
        try:
            _gdi32.SelectObject(self.hdc, self.old)
            _gdi32.DeleteObject(self.hbm)
            _gdi32.DeleteDC(self.hdc)
        except Exception:
            pass


# ---------- 窗口过程 ----------
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_WINDOWS = {}          # hwnd -> HoverWin 实例（必须全局持有，避免回调被 GC）


def _wndproc(hwnd, msg, wparam, lparam):
    """窗口过程。必须极其健壮：任何异常都不能逃出回调（ctypes 回调抛异常 → 返回值未定义）。"""
    try:
        app = _WINDOWS.get(hwnd)
        if app is not None:
            return app.on_message(hwnd, msg, wparam, lparam)
        # 窗口尚未绑定 app（WM_NCCREATE 等创建早期消息）→ 走系统默认处理
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    except BaseException:
        try:
            log_exception(f"[wndproc msg=0x{msg:X}]")
        except Exception:
            pass
        try:
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0


# 全局唯一回调桩：注册进窗口类的必须是这个实例本身，且进程存活期间绝不能被回收。
# 若在 _register_class 里临时 WNDPROC(_wndproc) 再 cast，临时桩函数返回即被 GC 释放，
# 类里存的就是悬空指针——释放块稍后被分配器复用覆盖，Windows 再派发消息会跳进垃圾代码，
# 表现为 0xc000001d 非法指令 / 访问违例，且延迟几十秒才崩（早期短测 25~40s 看不出来）。
_WNDPROC_STUB = WNDPROC(_wndproc)


def work_area():
    """主显示器工作区（排除任务栏），等价于 macOS 的 visibleFrame。"""
    r = RECT()
    if _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return (0, 0, _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1))


# ---------- 应用 ----------
class HoverWin:
    def __init__(self, run_seconds=None):
        if _gdiplus is None:
            raise RuntimeError("未找到 gdiplus.dll，Windows 悬浮球无法启动")
        _set_dpi_aware()
        tok = P()
        si = GdiplusStartupInput()
        si.GdiplusVersion = 1
        if _GdiplusStartup(ctypes.byref(tok), ctypes.byref(si), None) != 0:
            raise RuntimeError("GdiplusStartup 失败")
        self._gp_token = tok

        p = load_paths()
        self.db_path = p["db_path"]
        self.wb_db = p["workbuddy_db"]
        self.pos_file = p["pos_file"]
        self.dashboard = p["dashboard_url"]

        self.active = []
        self.db_ok = False
        self.api_ok = None
        self.run_seconds = run_seconds
        self.t0 = time.time()
        self._dragging = False
        self._moved = False
        self._down = (0, 0)
        self._win0 = (0, 0)
        self._pending = 0.0
        self._last_double = 0.0
        self._popup_visible = False
        self._drawn_sig = None                  # 上次已绘制的内容签名
        self._shown_popup = False               # 面板可见状态是否已反映到重绘
        self._spawning_dash = False             # 正在尝试拉起看板服务（防并发重复拉起）
        self._spawn_lock = threading.Lock()

        self._register_class()
        self.hwnd_ball = self._create_window(0, 0, BALL, BALL)
        self.hwnd_popup = self._create_window(0, 0, POPUP_W, POPUP_H)
        self.surf_ball = Surface(BALL, BALL)
        self.surf_popup = Surface(POPUP_W, POPUP_H)
        _WINDOWS[self.hwnd_ball] = self
        _WINDOWS[self.hwnd_popup] = self

        self._place_ball()
        self.tick()
        _user32.ShowWindow(self.hwnd_ball, SW_SHOWNA)
        _user32.SetTimer(self.hwnd_ball, ID_TIMER_TICK, TICK_MS, None)
        threading.Thread(target=self._health_loop, daemon=True).start()

    # ---- 窗口 ----
    def _register_class(self):
        wc = WNDCLASSW()
        wc.style = 0x0008                       # CS_DBLCLKS（双击消息，备用）
        wc.lpfnWndProc = ctypes.cast(_WNDPROC_STUB, ctypes.c_void_p)
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.hCursor = _user32.LoadCursorW(
            None, ctypes.cast(ctypes.c_void_p(32512), wt.LPCWSTR))    # IDC_ARROW
        wc.hbrBackground = None
        wc.lpszClassName = CLASS_NAME
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:                     # ERROR_CLASS_ALREADY_EXISTS
                raise RuntimeError(f"RegisterClassW 失败 (err={err})")

    def _create_window(self, x, y, w, h):
        hwnd = _user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            CLASS_NAME, "WB Hover", WS_POPUP, x, y, w, h, None, None,
            _kernel32.GetModuleHandleW(None), None)
        if not hwnd:
            raise RuntimeError(f"CreateWindowExW 失败 (err={ctypes.get_last_error()})")
        return hwnd

    def _place_ball(self):
        ax, ay, aw, ah = work_area()
        saved = load_pos(self.pos_file)
        if saved:
            bx, by = saved
        else:
            bx, by = ax + aw - BALL - 24, ay + ah - BALL - 24      # 默认右下角
        bx = max(ax + 8, min(bx, ax + aw - BALL - 8))
        by = max(ay + 8, min(by, ay + ah - BALL - 8))
        self._move_window(self.hwnd_ball, int(bx), int(by))

    def _move_window(self, hwnd, x, y):
        _user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), x, y, 0, 0,
                             SWP_NOSIZE | SWP_NOACTIVATE)

    def _window_xy(self, hwnd):
        r = RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top

    # ---- 数据 ----
    def _draw_sig(self):
        """内容签名：内容不变就不重绘（消除每 tick 全量重画导致的消息队列卡顿）。"""
        a = self.active
        return (self.db_ok, self.api_ok,
                tuple((x.get("_wb_title") or x.get("title") or "",
                       x.get("credit"), x.get("total_tokens"),
                       x.get("_last_act") or x.get("last_ts"),
                       (x.get("user_prompt") or "")[:16])
                      for x in a[:MAX_CARDS]))

    def _health_loop(self):
        """后台线程：每 2s 探测一次看板健康。网络探测绝不阻塞 UI 消息循环。"""
        while True:
            try:
                self.api_ok = api_healthy(self.dashboard)
            except Exception:
                pass
            time.sleep(2.0)

    def tick(self):
        # 拖动期间：不做 DB/HTTP/绘制/置顶，消息队列只服务拖动事件 → 拖动始终跟手
        if self._dragging:
            x, y = self._window_xy(self.hwnd_ball)
            report_state(os.getpid(), True, 2, [x, y], len(self.active),
                         self.db_ok, self.api_ok)
            if self.run_seconds and time.time() - self.t0 > self.run_seconds:
                _user32.PostQuitMessage(0)
            return
        _kpi, active, ok = query_db(self.db_path, self.wb_db)
        if active is not None:                 # 读失败保留上次值，防闪烁归零
            self.active = active
        self.db_ok = ok
        # self.api_ok 由后台 _health_loop 线程维护（api_healthy 对无响应端口最长阻塞 0.5s，
        # 若放在 tick 里会每秒卡死消息循环——这正是此前“拖动不流畅/点击迟钝”的根因）
        sig = self._draw_sig()
        if sig != self._drawn_sig or self._popup_visible != self._shown_popup:
            self.draw_ball()
            if self._popup_visible:
                self.draw_popup()
            self._drawn_sig = sig
        self._shown_popup = self._popup_visible
        # 把球/面板强制抬到 TOPMOST 链顶端：WorkBuddy 自己也有全屏 MFC 顶层窗口，
        # z-order 在我们上方会吃掉所有点击；每 tick 一次即可压回去（开销可忽略）。
        _user32.SetWindowPos(self.hwnd_ball, ctypes.c_void_p(HWND_TOPMOST),
                             0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        if self._popup_visible:            # 再抬一次面板，保证盖过球体（两者偶有重叠）
            _user32.SetWindowPos(self.hwnd_popup, ctypes.c_void_p(HWND_TOPMOST),
                                 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        x, y = self._window_xy(self.hwnd_ball)
        report_state(os.getpid(), True, 2, [x, y], len(self.active), self.db_ok, self.api_ok)
        if self.run_seconds and time.time() - self.t0 > self.run_seconds:
            _user32.PostQuitMessage(0)

    # ---- 绘制 ----
    def draw_ball(self):
        s = self.surf_ball
        s.clear()
        d = BALL - 6
        off = (BALL - d) / 2
        s.ellipse(C_BG, off, off, d, d, line_argb=C_LINE, line_w=2.0)
        dot = C_OFFLINE if (self.api_ok is False or not self.db_ok) else C_ONLINE
        s.ellipse(dot, BALL - 22, 4, 8, 8)
        if self.db_ok:
            s.text(str(len(self.active)), 0, 16, 26, C_TXT, bold=True, center=True)
            s.text("活跃", 0, 52, 10, C_GRAY, center=True)
        else:
            s.text("·", 0, 26, 24, C_TXT, bold=True, center=True)
            s.text("等待数据", 0, 54, 9, C_GRAY, center=True)
        s.present(self.hwnd_ball)

    def draw_popup(self):
        s = self.surf_popup
        w, h = POPUP_W, POPUP_H
        s.clear()
        # 面板底
        s.round_rect(C_BG, 0, 0, w, h, POPUP_RADIUS, line_argb=C_LINE, line_w=1.0)

        # ---------- 头部：标题 + 在线状态徽章 ----------
        s.ellipse(C_BLUE, 18, 21, 9, 9)                       # logo 圆点
        s.text("WorkBuddy 用量监控", 36, 16, 10, C_GRAY)
        s.text("活跃对话", 18, 35, 18, C_TXT, bold=True)
        n = len(self.active)
        s.text(f"{n} 个会话运行中", 18, 64, 10, C_GRAY) if n else \
            s.text("暂无会话", 18, 64, 10, C_GRAY)
        # 右侧状态徽章（圆点 + 文字）
        online = not (self.api_ok is False or not self.db_ok)
        stxt = "看板在线" if online else "看板离线"
        scol = C_ONLINE if online else C_OFFLINE
        tw = s.measure(stxt, 9)[0]
        bw = 34 + tw
        bx = w - 16 - bw
        s.round_rect(C_CARD, bx, 24, bw, 26, 13, line_argb=C_LINE, line_w=1.0)
        s.ellipse(scol, bx + 11, 33, 8, 8)
        s.text(stxt, bx + 27, 27, 9, C_GRAY)
        s.hline(C_LINE_DIM, 16, 78, w - 32, 1)                # 头部分隔线

        # ---------- 卡片区 ----------
        if not self.active:
            s.ctext("当前没有活跃对话", w / 2, h / 2 - 26, 16, C_TXT, bold=True)
            s.ctext("WorkBuddy 有会话运行时，此处会自动刷新", w / 2, h / 2 + 6, 10, C_GRAY)
        else:
            y = CARD_TOP
            for it in self.active[:MAX_CARDS]:
                self._draw_card(s, it, y)
                y += CARD_STEP

        # ---------- 底部提示 ----------
        s.ctext("点击面板外任意位置关闭  ·  双击小球打开完整看板", w / 2, HINT_Y, 9, C_GRAY)

    # 卡片内部横向坐标（三列指标）
    _STAT_COLS = 3

    def _card_col_x(self, k, w):
        """第 k 列(0..2)的水平中心：把卡片内部宽三等分。"""
        inner_w = w - 40
        return 20 + inner_w * (k + 0.5) / self._STAT_COLS

    def _draw_card(self, s, it, y):
        w, ch = POPUP_W, CARD_H
        cw = w - 20
        s.round_rect(C_CARD, 10, y, cw, ch, 10, line_argb=C_LINE, line_w=1.0)

        # ① 标题行：项目徽章(如有) + 会话标题，最近活跃时间右对齐
        title = (it.get("_wb_title") or it.get("title") or it.get("project")
                 or "(无标题)")
        ago = fmt_ago(it.get("_last_act") or it.get("last_ts") or 0)
        ago_w = s.measure(ago, 9)[0]
        left = 20
        proj = it.get("project") or ""
        if proj and s.measure(proj, 9, True)[0] <= 84:
            s.text(proj, 20, y + 13, 9, C_BLUE, bold=True)
            left = 20 + s.measure(proj, 9, True)[0] + 10
        title_max = max((w - 20 - left) - ago_w - 12, 30)
        s.text(title, left, y + 11, 13, C_TXT, bold=True, maxw=title_max)
        s.text_right(ago, w - 20, y + 15, 9, C_GRAY)

        # ② 指标行：积分 / Token / 本轮用时，三列均分，列间弱分隔线
        yy_top = y + 36
        for k in range(self._STAT_COLS - 1):            # 列间竖线（低对比，不抢内容）
            s.vline(C_LINE_DIM, 20 + int((w - 40) * (k + 1) / self._STAT_COLS),
                    y + 35, 26)
        stats = [
            (f"{(it.get('credit') or 0):.1f}", "积分 (分)", C_BLUE, 16),
            (fmt_tokens(it.get("total_tokens") or 0), "Token", C_TXT, 16),
            (fmt_duration(it), "本轮用时", C_TXT, 13),
        ]
        for k, (val, lab, col, vsz) in enumerate(stats):
            cx = self._card_col_x(k, w)
            s.ctext(val, cx, yy_top, vsz, col, bold=True)
            s.ctext(lab, cx, yy_top + 24, 8.5, C_GRAY)

        # ③ 请求预览（截断到一行，保持卡片整齐）
        req = (it.get("user_prompt") or "").replace("\n", " ").strip()
        if req:
            body = req[:64]
            if len(req) > 64:
                body += "…"
            s.text("「" + body + "」", 20, y + 80, 9, C_GRAY, maxw=w - 40)

    # ---- 面板 ----
    def show_popup(self):
        bx, by = self._window_xy(self.hwnd_ball)
        ax, ay, aw, ah = work_area()
        px = bx - POPUP_W - 10
        if px < ax + 8:
            px = bx + BALL + 10
        py = by + BALL - POPUP_H
        px = max(ax + 8, min(px, ax + aw - POPUP_W - 8))
        py = max(ay + 8, min(py, ay + ah - POPUP_H - 8))
        self._move_window(self.hwnd_popup, int(px), int(py))
        self._popup_visible = True
        self._report_event("panel_open")
        self.draw_popup()
        self._shown_popup = True
        _user32.ShowWindow(self.hwnd_popup, SW_SHOWNA)
        # ShowWindow 之后立刻再抬一次，确保刚弹出的面板压过所有已存在的顶层窗口
        _user32.SetWindowPos(self.hwnd_popup, ctypes.c_void_p(HWND_TOPMOST),
                             0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        _user32.SetTimer(self.hwnd_popup, ID_TIMER_WATCH, WATCH_MS, None)

    def hide_popup(self):
        if self._popup_visible:
            _user32.ShowWindow(self.hwnd_popup, SW_HIDE)
            _user32.KillTimer(self.hwnd_popup, ID_TIMER_WATCH)
            self._popup_visible = False
            self._shown_popup = False
            self._report_event("panel_close")

    def toggle_popup(self):
        self.hide_popup() if self._popup_visible else self.show_popup()

    # ---- 事件日志（可观测：验证绑定/回调/动作是否执行）----
    def _report_event(self, ev, ok=None, detail=""):
        try:
            line = json.dumps(
                {"ts": time.strftime("%H:%M:%S"), "ev": ev, "ok": ok, "d": detail},
                ensure_ascii=False)
            with open(EVENT_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _dashboard_port(self):
        try:
            return urlparse(self.dashboard).port or 8801
        except Exception:
            return 8801

    def _ensure_dashboard_server(self):
        """看板服务没在运行时，用同解释器拉起 scripts/wb_usage/wb_api.py（幂等，防并发）。"""
        with self._spawn_lock:
            if self._spawning_dash:
                return
            self._spawning_dash = True
        try:
            scripts_dir = os.path.dirname(os.path.abspath(__file__))
            api_py = os.path.join(scripts_dir, "wb_usage", "wb_api.py")
            if not os.path.isfile(api_py):
                log_exception(f"[dashboard] 找不到看板服务脚本 {api_py}")
                return
            port = self._dashboard_port()
            exe = sys.executable
            self._report_event("ensure_server", ok=True,
                               detail=f"启动 {os.path.basename(api_py)} --port {port}")
            tmp = os.environ.get("TEMP") or "C:/Windows/Temp"
            with open(os.path.join(tmp, "wb-usage.log"), "ab") as fout, \
                 open(os.path.join(tmp, "wb-usage.err.log"), "ab") as ferr:
                subprocess.Popen(
                    [exe, api_py, "--port", str(port)],
                    cwd=os.path.dirname(api_py),
                    stdin=subprocess.DEVNULL, stdout=fout, stderr=ferr,
                    creationflags=subprocess.DETACHED_PROCESS
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            log_exception("[dashboard] 拉起看板服务失败")
        finally:
            self._spawning_dash = False

    def open_dashboard(self):
        """双击 / 右键菜单"打开看板"：异步线程里 探健康 → 无服务则自启 → 就绪后开浏览器。
        全程不阻塞 UI 消息循环；结果写事件日志，彻底失败给原生提示。"""
        self._report_event("open_dashboard", detail="请求打开 " + self.dashboard)
        threading.Thread(target=self._open_dashboard_worker, daemon=True).start()

    def _open_dashboard_worker(self):
        url = self.dashboard
        got = False
        try:
            got = api_healthy(url)                       # 已就绪？
            if not got:                                  # 服务没跑 → 自动拉起并等待就绪
                self._ensure_dashboard_server()
                deadline = time.time() + DASH_READY_WAIT
                while time.time() < deadline:
                    time.sleep(0.25)
                    if api_healthy(url):
                        got = True
                        break
            if got:
                webbrowser.open(url)
                self._report_event("open_dashboard", ok=True, detail=url)
                return
            # 彻底失败：给用户看得见的反馈，而不是静默失败
            self._report_event("open_dashboard", ok=False,
                               detail=f"{url} 服务在 {DASH_READY_WAIT:.0f}s 内未就绪")
            log_exception(f"[dashboard] 打开看板失败：{url} 服务未就绪")
            try:
                self._msgbox(f"WorkBuddy 用量看板\n\n{url} 的服务未能启动。\n"
                             f"请运行看板服务后重试：\n"
                             f"python {os.path.join('scripts', 'wb_usage', 'wb_api.py')} "
                             f"--port {self._dashboard_port()}\n\n"
                             f"详见日志：%TEMP%\\wb-hover.err.log")
            except Exception:
                pass
        except Exception:
            log_exception("[dashboard] open_dashboard_worker 异常")

    def _msgbox(self, text):
        """原生提示框（异步线程内调用安全）。"""
        try:
            u = ctypes.WinDLL("user32", use_last_error=True)
            if not getattr(u, "MessageBoxW", None):
                return
            u.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
            u.MessageBoxW.restype = ctypes.c_int
            u.MessageBoxW(None, text, "WorkBuddy 悬浮球", 0x40 | 0x1000)  # MB_OK|MB_SETFOREGROUND
        except Exception:
            pass

    def context_menu(self, hwnd):
        pt = POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        menu = _user32.CreatePopupMenu()
        _user32.AppendMenuW(menu, MF_STRING, IDM_DASHBOARD, "打开看板")
        _user32.AppendMenuW(menu, MF_STRING, IDM_QUIT, "退出悬浮球")
        _user32.SetForegroundWindow(hwnd)
        cmd = _user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
            pt.x, pt.y, 0, hwnd, None)
        _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        _user32.DestroyMenu(menu)
        if cmd == IDM_DASHBOARD:
            self._report_event("menu_dashboard")
            self.open_dashboard()
        elif cmd == IDM_QUIT:
            self._report_event("menu_quit")
            _user32.PostQuitMessage(0)

    # ---- 消息 ----
    def on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE                       # 永不抢焦点
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        if msg == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_TIMER:
            return self._on_timer(hwnd, wparam)
        if hwnd == self.hwnd_ball:
            return self._ball_mouse(hwnd, msg, wparam, lparam)
        if hwnd == self.hwnd_popup:
            return self._popup_mouse(hwnd, msg, lparam)
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_timer(self, hwnd, timer_id):
        if timer_id == ID_TIMER_TICK:
            self.tick()
        elif timer_id == ID_TIMER_CLICK:
            _user32.KillTimer(hwnd, ID_TIMER_CLICK)
            now = time.time()
            if (self._pending and now - self._pending >= 0.2
                    and now - self._last_double > 0.45):
                self._pending = 0.0
                self._report_event("single_click")
                self.toggle_popup()
        elif timer_id == ID_TIMER_WATCH:
            # 面板打开期间：鼠标左/右键按下且光标不在面板/球体范围内 → 关闭。
            # 40ms 高频轮询足以捕获一次真实点击的“按下”瞬间（人类点击按下通常 ≥60ms）。
            if self._popup_visible and (
                    (_user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
                    or (_user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)):
                pt = POINT()
                _user32.GetCursorPos(ctypes.byref(pt))
                if not self._hit_popup(pt) and not self._hit_ball(pt):
                    self.hide_popup()
        return 0

    def _hit_popup(self, pt):
        x, y = self._window_xy(self.hwnd_popup)
        return x <= pt.x <= x + POPUP_W and y <= pt.y <= y + POPUP_H

    def _hit_ball(self, pt):
        x, y = self._window_xy(self.hwnd_ball)
        return x <= pt.x <= x + BALL and y <= pt.y <= y + BALL

    def _ball_mouse(self, hwnd, msg, wparam, lparam):
        if msg == WM_LBUTTONDOWN:
            # 用**屏幕坐标**记录按下点与窗口原点：窗口跟随光标的总位移 = 光标当前位置 - 按下点。
            # （不要用 lparam 客户区坐标——窗口在移动，客户坐标系原点也在动，会产生来回抖动。）
            pt = POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            self._down = (pt.x, pt.y)
            self._win0 = self._window_xy(hwnd)
            self._dragging = True
            self._moved = False
            _user32.SetCapture(hwnd)
            return 0
        if msg == WM_MOUSEMOVE and self._dragging and (wparam & MK_LBUTTON):
            pt = POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            dx, dy = pt.x - self._down[0], pt.y - self._down[1]
            if abs(dx) + abs(dy) > 3:
                self._moved = True
                ax, ay, aw, ah = work_area()            # 钳制在屏幕内，拖不出去
                nx = max(ax + 8, min(self._win0[0] + dx, ax + aw - BALL - 8))
                ny = max(ay + 8, min(self._win0[1] + dy, ay + ah - BALL - 8))
                self._move_window(hwnd, int(nx), int(ny))
            return 0
        if msg == WM_LBUTTONUP:
            _user32.ReleaseCapture()
            self._dragging = False
            if self._moved:                             # 拖动结束 → 记住位置
                x, y = self._window_xy(hwnd)
                save_pos(self.pos_file, x, y)
                return 0
            now = time.time()
            if self._pending and now - self._pending < _user32.GetDoubleClickTime() / 1000.0:
                self._pending = 0.0                     # 第二次点击 = 双击
                self._last_double = now
                self._report_event("double_click")
                self.open_dashboard()
            else:
                self._pending = now                     # 等 220ms 确认不是双击
                _user32.SetTimer(hwnd, ID_TIMER_CLICK, CLICK_DELAY_MS, None)
            return 0
        if msg == WM_RBUTTONUP:
            if self._popup_visible:                     # 呼出菜单前收起面板
                self.hide_popup()
            self.context_menu(hwnd)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_mouse(self, hwnd, msg, lparam):
        # 关闭面板不再依赖按钮：点击面板外任意位置即关闭（见 ID_TIMER_WATCH）。
        # 面板内部点击保留原样（无副作用），右键菜单仍可用。
        if msg == WM_RBUTTONUP:
            if self._popup_visible:                     # 面板上呼出菜单也先收起面板
                self.hide_popup()
            self.context_menu(hwnd)
            return 0
        if msg == WM_LBUTTONUP:
            # 面板自身是“展示区”，左键不做任何动作，避免误触。
            return 0
        return _user32.DefWindowProcW(hwnd, msg, 0, lparam)

    # ---- 主循环 ----
    def run(self):
        msg = MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def close(self):
        self.hide_popup()
        try:
            self.surf_ball.close()
            self.surf_popup.close()
        except Exception:
            pass
        try:
            _GdiplusShutdown(self._gp_token)
        except Exception:
            pass


def main():
    seconds = None
    if "--seconds" in sys.argv:
        try:
            seconds = float(sys.argv[sys.argv.index("--seconds") + 1])
        except Exception:
            seconds = None
    if os.name != "nt":
        sys.stderr.write("[wb-hover] 本脚本仅适用于 Windows，其他平台请运行 hover.py 自动分发。\n")
        return 2
    app = None
    try:
        app = HoverWin(run_seconds=seconds)
        app.run()
    except Exception:
        log_exception("[windows]")
        sys.stderr.write(f"[wb-hover] 启动失败，详见日志：{ERR_LOG}\n")
        return 1
    finally:
        if app:
            app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
