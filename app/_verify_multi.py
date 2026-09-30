# -*- coding: utf-8 -*-
"""一次性验证：多动作素材 + 帧率 + 进程内存。

看三件事：
  1. 五个动作的帧数/fps 是否与源片一致（24fps，一帧未减）
  2. 七个动作是否都画得出来，内容框是否随动作切换
  3. 工作集内存（确认「建完缓存释放原始位图」这个优化真的生效）
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wb_whale_win as W          # noqa: E402
import wb_anim as A               # noqa: E402

app = W.WhalePet()

print("动作素材 :", {k: f"{v.count}帧@{v.fps:g}fps" for k, v in sorted(app._clips.items())})
print("预缩放   :", {k: len(v) for k, v in app._anim_caches.items()})
print("残留位图 :", {k: len(v) for k, v in app._anim_imgs.items()}, "  ← 应为空（建完缓存就释放）")

lay = app._layout()
now = time.time()
for act in (A.ACT_IDLE, A.ACT_DOZE, A.ACT_SLEEPY, A.ACT_WAKE, A.ACT_PICKUP,
            A.ACT_WRITE, A.ACT_PRESENT):
    app._wr.act = act
    app._anim_key = None                      # 强制重算，模拟真实切换
    ok = app._draw_anim_frame(app.surf, lay, now)
    print(f"  {act:<8} 素材={app._wr.clip_name(app._clips):<7} 画出={ok} "
          f"内容框y0={app._spr_cbox['__anim__'][1]:.3f}")


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


pmc = PMC()
pmc.cb = ctypes.sizeof(PMC)
ctypes.WinDLL("psapi").GetProcessMemoryInfo(
    ctypes.WinDLL("kernel32").GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
print("工作集   : %.0f MB（峰值 %.0f MB）" % (pmc.WorkingSetSize / 1e6,
                                             pmc.PeakWorkingSetSize / 1e6))

sys.stdout.flush()
os._exit(0)          # 跳过 GDI+ 清理：本进程只为验证
