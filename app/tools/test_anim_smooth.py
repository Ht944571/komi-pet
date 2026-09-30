# -*- coding: utf-8 -*-
r"""写字/待机动画"流畅度"回归测试

背景 —— 2026-09-30 用户报障「写字状态不流畅」（参原片 Q版古见同学写字.mp4，24fps）：
  ① `SetTimer(41)` 被系统定时器粒度（15.6ms）拉长到 **46.8ms** → 写字态只有 21.3fps
     （本机 `timeBeginPeriod(1)` 实测无效）；
  ② 帧号按墙钟 `time.time()` 取（Windows 上只有 15.6ms 粒度），采样抖动 → **一次跳 2 帧**
     （实测 111 个 tick 里 14 次），肉眼就是"一顿一顿"。
修：① 高精度可等待定时器（`CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`）+ 后台线程投递
    `WM_APP_ANIM`；② 帧号改 `time.perf_counter()`；③ 循环动作「单 tick 最多推进 1 帧」。

验证点：
  A. 时钟机制：act_is_loop 分类 / 高精度定时器真能按 41ms 走 / 周期参数生效
  B. 取帧钳位：跳帧被钳成 +1、回绕不被钳、一次性动作不钳、换动作重置
  C. 挂载与自愈：不双重挂 SetTimer / 线程真在投递 / 看门狗退回 SetTimer / 停时钟

用法：python tools/test_anim_smooth.py
"""
import ctypes
import os
import json
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_anim as A                            # noqa: E402
import wb_whale_win as W                       # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


print("\n[A] 时钟机制与素材声明")

_clip = A.load("write", os.path.join(APP, "assets"))
_meta = json.load(open(os.path.join(APP, "assets", "anim", "write", "_anim_meta.json"),
                       encoding="utf-8"))
check("A1: write 素材加载（24fps，count / loop_start 与 meta 一致）",
      _clip is not None and _clip.fps == 24.0
      and _clip.count == _meta["count"] and _clip.loop_start == _meta.get("loop_start"),
      f"count={getattr(_clip, 'count', None)} loop_start={getattr(_clip, 'loop_start', None)} "
      f"meta={_meta.get('count')}/{_meta.get('loop_start')}")
_lo, _hi = _clip.loop_range(A.ACT_WRITE)
check("A1b: 写字循环区间 = meta.loop_start..count-1（首轮播前摇、之后只循环尾巴）",
      _lo == (_meta.get("loop_start") or 0) and _hi == _clip.count - 1,
      f"loop_range=({_lo},{_hi}) count={_clip.count} loop_start={_clip.loop_start}")

# 首轮从头播（含「进入写字」），第二轮才进循环段
check("A1c: index —— 首轮从头播（0 → count-1），之后落到 loop_start",
      _clip.index(A.ACT_WRITE, 0.0, 0.0) == 0
      and _clip.index(A.ACT_WRITE, 0.0, (_clip.count - 1) / _clip.fps) == _clip.count - 1
      and _clip.index(A.ACT_WRITE, 0.0, _clip.count / _clip.fps) == _lo,
      f"0→{_clip.index(A.ACT_WRITE, 0.0, 0.0)} "
      f"尾→{_clip.index(A.ACT_WRITE, 0.0, (_clip.count - 1) / _clip.fps)} "
      f"下一轮→{_clip.index(A.ACT_WRITE, 0.0, _clip.count / _clip.fps)}")

_loops = [A.ACT_IDLE, A.ACT_SLEEPY, A.ACT_WRITE]
_oneshots = [A.ACT_PICKUP, A.ACT_DOZE, A.ACT_WAKE, A.ACT_PRESENT]
check("A2: act_is_loop 只认 idle/sleepy/write",
      all(_clip.act_is_loop(a) for a in _loops)
      and not any(_clip.act_is_loop(a) for a in _oneshots),
      f"loops={[_clip.act_is_loop(a) for a in _loops]} "
      f"oneshot={[_clip.act_is_loop(a) for a in _oneshots]}")


def _measure(h, n=12):
    ts = []
    for _ in range(n):
        if W._kernel32.WaitForSingleObject(h, 400) != 0:
            return None
        ts.append(time.perf_counter())
    iv = [(ts[i + 1] - ts[i]) * 1000 for i in range(len(ts) - 1)]
    return statistics.median(iv)


_h = W._make_hires_timer(W.ANIM_MS)
if _h:
    _m41 = _measure(_h)
    check("A3: 高精度定时器实测周期 ≈41ms（SetTimer 会变成 46.8ms）",
          _m41 is not None and 38.0 <= _m41 <= 45.0, f"中位 {_m41} ms")
    W._kernel32.CloseHandle(_h)
    _h10 = W._make_hires_timer(10)
    _m10 = _measure(_h10) if _h10 else None
    check("A4: 周期参数真生效（10ms → 中位 < 20ms）",
          _m10 is not None and _m10 < 20.0, f"中位 {_m10} ms")
    if _h10:
        W._kernel32.CloseHandle(_h10)
else:
    check("A3: 本机不支持高精度可等待定时器（应自动退回 SetTimer）", False,
          "_make_hires_timer(41) 返回 None")
    check("A4: （同上，跳过周期参数验证）", False, "")

print("\n[B] 取帧步进（循环动作不跳帧）")

app = W.WhalePet(run_seconds=0)
try:
    lay = app._layout()
    app._wr.feed = lambda *a, **k: None
    app._wr.act = A.ACT_WRITE
    app._anim_key = None                       # 强制首次进入时重置 _anim_t0

    def draw_at(_shift=0.0):
        """按墙钟画一帧；_shift > 0 表示把计时起点往前挪（= 模拟 tick 被拉长）。"""
        if _shift:
            app._anim_t0 -= _shift
        return app._draw_anim_frame(app.surf, lay, time.time())

    draw_at()                                   # 第一帧（建立 _anim_t0 / prev）
    seq = []
    for _ in range(_clip.count + 20):           # 跑过"首轮 + 至少一次回绕"
        draw_at(0.5)                            # 每 tick 都"墙钟跳了 12 帧"
        seq.append(app._anim_i_prev)
    deltas = [seq[i + 1] - seq[i] for i in range(len(seq) - 1)]
    _wrap = _lo - _hi                            # 回绕那一步的差值（负数）
    _fwd = [d for d in deltas if d != _wrap]
    check("B1: 墙钟狂跳时，帧号每 tick 仍只推进 1 帧（不跳帧、不停顿）",
          all(d == 1 for d in _fwd), f"deltas={deltas[:14]}")
    check("B2: 只有循环回绕那一步回退，且正好落到 loop_start",
          sum(1 for d in deltas if d < 0) >= 1
          and all(d == _wrap for d in deltas if d < 0)
          and _hi in seq and seq[seq.index(_hi) + 1] == _lo,
          f"回退次数={sum(1 for d in deltas if d < 0)} deltas={deltas[:14]}")

    # 回绕：固定步进下 "循环末帧 → 循环首帧" 必须精确发生
    app._anim_i_key = (A.ACT_WRITE, "write")
    app._anim_i_prev = _hi
    draw_at()
    check("B3: 固定步进精确回绕（循环末帧 → loop_start，不越界）",
          app._anim_i_prev == _lo, f"prev={_hi} → now={app._anim_i_prev}")

    # 首轮中途不会提前跳进循环段（前摇必须播完）
    app._anim_i_key = (A.ACT_WRITE, "write")
    app._anim_i_prev = _lo - 2
    draw_at()
    check("B3b: 前摇段逐步推进（loop_start-2 → loop_start-1，不提前跳进循环段）",
          app._anim_i_prev == _lo - 1, f"now={app._anim_i_prev} loop_start={_lo}")

    # 一次性动作不钳：时间跳一大截时应当直接跳（否则永远播不到末帧）
    app._wr.act = A.ACT_PICKUP
    app._anim_key = None
    draw_at()
    app._anim_t0 -= 0.5
    draw_at()
    _pk = app._anim_i_prev
    check("B4: 一次性动作（pickup）不钳位 —— 允许一次跳多帧", _pk > 1,
          f"pickup 帧号={_pk}")

    # 换动作后钳位/步进状态重置：新动作从头（0）开始，不会接着上一动作的帧号
    app._wr.act = A.ACT_IDLE
    app._anim_key = None
    draw_at()
    _first = app._anim_i_prev
    draw_at()
    check("B5: 换动作后从头开始并恢复正常步进（第一帧 0 → 第二帧 1）",
          _first == 0 and app._anim_i_prev == 1
          and app._anim_i_key == (A.ACT_IDLE, "idle"),
          f"第一帧={_first} 第二帧={app._anim_i_prev} key={app._anim_i_key}")
finally:
    pass

print("\n[C] 挂载 / 投递 / 自愈")

_sets, _kills = [], []
_saved_st, _saved_kt = W._user32.SetTimer, W._user32.KillTimer
W._user32.SetTimer = lambda *a: (_sets.append(a[1]), 1)[1]
W._user32.KillTimer = lambda *a: (_kills.append(a[1]), 1)[1]
try:
    app._arm_timers()
finally:
    W._user32.SetTimer, W._user32.KillTimer = _saved_st, _saved_kt
if app._anim_clock_on:
    check("C1: 高精度时钟接管后，不再挂 ID_TIMER_ANIM 的 SetTimer（避免双重投递）",
          W.ID_TIMER_ANIM not in _sets and W.ID_TIMER_ANIM in _kills,
          f"SetTimer={_sets} KillTimer={_kills}")
else:
    check("C1: 时钟没起来时仍退回 SetTimer(ID_TIMER_ANIM)",
          W.ID_TIMER_ANIM in _sets, f"SetTimer={_sets}")
check("C2: ID_TIMER_TICK 始终挂着（查库/看门狗都靠它）",
      W.ID_TIMER_TICK in _sets, f"SetTimer={_sets}")

if app._anim_clock_on:
    _n0 = app._anim_last_tick
    app.run_seconds = 0.5
    app.t0 = time.time()
    app.run()                                  # 跑 0.5s 消息循环
    check("C3: 时钟线程真在投递 WM_APP_ANIM（0.5s 内至少收到几次）",
          app._anim_last_tick > _n0, f"{_n0} → {app._anim_last_tick}")
else:
    check("C3: （时钟未接管，跳过投递验证）", False, "")

# 看门狗：伪造"时钟停了 5 秒" → 下一次 tick 必须退回 SetTimer
app._anim_last_tick = time.time() - 5.0
app._report_event = lambda *a, **k: None       # 别把测试事件写进用户的事件日志
if app._anim_clock_on:
    _sets2 = []
    W._user32.SetTimer = lambda *a: (_sets2.append(a[1]), 1)[1]
    try:
        app.tick()
    finally:
        W._user32.SetTimer = _saved_st
    check("C4: 看门狗发现时钟停摆 → 退回 SetTimer(ID_TIMER_ANIM) 并标记 dead",
          (not app._anim_clock_on) and app._anim_clock_dead
          and W.ID_TIMER_ANIM in _sets2,
          f"on={app._anim_clock_on} dead={app._anim_clock_dead} sets={_sets2}")
else:
    check("C4: （时钟未接管，看门狗分支不适用）", False, "")

app._stop_anim_clock()
check("C5: _stop_anim_clock 清句柄与在途标记",
      app._anim_clock is None and not app._anim_clock_on and not app._anim_pending)

app.close()

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
