#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_diag_procs.py — 只读诊断：桌宠相关进程（路径/启动时间/窗口）+ 合成输入是否可用
================================================================================
两个问题一直干扰排查，先钉死：
  1. **运行中的桌宠到底是哪份代码**（仓库源码 `app/`？打包 exe？）——决定改了代码要不要
     重启、以及"我看到的源码"是否就是"跑着的代码"。
  2. **沙箱允不允许合成输入**（SetCursorPos / mouse_event）——不允许的话，
     "移动光标看 hover"这类 E2E 探针全是假阴性。
     （实测现象：SetCursorPos 后 GetCursorPos 读回来没变 → 输入被拦）

只读，不改任何东西。用法：<托管python> app/tools/_diag_procs.py
"""
import ctypes
import os
import sys
from ctypes import wintypes as wt

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.windll.user32

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.OpenProcess.restype = wt.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                           ctypes.POINTER(wt.DWORD)]


def img_path(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "(打不开)"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return "(查询失败)"
    finally:
        k32.CloseHandle(h)


def start_time(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        c, e, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                   ctypes.byref(k), ctypes.byref(u)):
            return ""
        ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime      # 1601 起 100ns
        secs = ticks / 1e7 - 11644473600                        # → Unix
        import time
        return time.strftime("%H:%M:%S", time.localtime(secs))
    finally:
        k32.CloseHandle(h)


def main():
    print("=" * 72)
    print("① 桌宠相关进程（含启动时间 / 镜像路径）")
    print("=" * 72)
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    rows = []
    if k32.Process32FirstW(snap, ctypes.byref(pe)):
        while True:
            name = pe.szExeFile
            if name.lower().startswith(("python", "komipet", "pythonw")):
                rows.append((pe.th32ProcessID, name))
            if not k32.Process32NextW(snap, ctypes.byref(pe)):
                break
    k32.CloseHandle(snap)
    for pid, name in rows:
        print("  PID=%-7d %-14s 启动=%-9s %s" % (pid, name, start_time(pid), img_path(pid)))
    if not rows:
        print("  （没找到 python/KomiPet 进程）")

    print()
    print("=" * 72)
    print("② 合成输入是否可用（SetCursorPos 是否真的生效）")
    print("=" * 72)
    p0 = wt.POINT()
    u32.GetCursorPos(ctypes.byref(p0))
    tx, ty = p0.x + 37, p0.y + 23
    r = u32.SetCursorPos(tx, ty)
    p1 = wt.POINT()
    u32.GetCursorPos(ctypes.byref(p1))
    back = u32.SetCursorPos(p0.x, p0.y)
    print("  SetCursorPos 返回=%s（还原返回=%s）" % (r, back))
    print("  原位置 (%d,%d) → 目标 (%d,%d) → 读回 (%d,%d)"
          % (p0.x, p0.y, tx, ty, p1.x, p1.y))
    if (p1.x, p1.y) == (tx, ty):
        print("  ✅ 合成输入可用（可用 SetCursorPos 做 hover E2E）")
    else:
        print("  🔴 合成输入被拦（光标没动）→ 所有『移动光标看 hover』的探针均为假阴性，"
              "不能用它判菜单好坏")

    print()
    print("=" * 72)
    print("③ 仓库源码 vs 运行进程 是否同一份")
    print("=" * 72)
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "wb_whale_win.py")
    import time
    print("  仓库源码 %s" % src)
    print("    mtime = %s   size = %d"
          % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(src))),
             os.path.getsize(src)))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
