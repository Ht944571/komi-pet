# -*- coding: utf-8 -*-
r"""眨眼状态调制 + 部分眨眼 专项测试（眨眼标准 ④⑤ 层）

验证点：
  A. 工作中降频：间隔放大 BLINK_WORK_RATE_SCALE，率降幅落在 30–50%
  B. 部分眨眼：占比 65%、闭合深度 40–70%、无保持段、无接触线区间、曲线闭合
  C. 慢眨（久无人）：概率、时长 ×1.5–2、必定全闭
  D. 禁眨（suppressed）：禁眨期不开新眨眼；进行中的跳到睁开段收尾；解除后不立刻补眨
  E. 应用层否决 _blink_vetoed：拖拽/悬停/按压/OK 弹入/成功态逐项生效
  F. _update_motion 集成：active → 降频、久无人 → idle、成功态 → 禁眨
  G. 签名量化：部分眨眼也能触发重绘（4 级量化）

用法：python tools/test_blink_modulation.py
"""

import os
import sys
import time
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_motion as MOTION                         # noqa: E402
import wb_whale_win as W                            # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


class patched:
    """临时改写 MOTION 模块常量（退出时恢复）。"""

    def __init__(self, **kw):
        self.kw = kw
        self.saved = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.saved[k] = getattr(MOTION, k)
            setattr(MOTION, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(MOTION, k, v)
        return False


def make_sched(**attrs):
    b = MOTION.BlinkScheduler(0.0)
    for k, v in attrs.items():
        setattr(b, k, v)
    return b


def force_trigger(b, t):
    """把调度拉到 t 时刻立即触发一次眨眼，返回 (depth, total)。"""
    b.next_at = t
    b.blink_t0 = 0.0
    b.blink_total = 0.0
    b._pending_double = False
    b._was_suppressed = False
    b.suppressed = False
    b.tick(t)
    return b.close_depth, b.blink_total


# ===== A. 工作中降频 =====
print("\n[A] 工作中（会话 running）眨眼率降 30–50%")
rate_drop = 1.0 - 1.0 / MOTION.BLINK_WORK_RATE_SCALE
check("A1: BLINK_WORK_RATE_SCALE=1.6 → 率降 ~37.5%（30–50% 区间）",
      0.30 <= rate_drop <= 0.50, f"got {rate_drop:.3f}")
random.seed(7)
waits_norm = [MOTION._next_blink_wait(0.0) for _ in range(2000)]
waits_work = [MOTION._next_blink_wait(0.0, MOTION.BLINK_WORK_RATE_SCALE)
              for _ in range(2000)]
m_norm = sum(waits_norm) / len(waits_norm)
m_work = sum(waits_work) / len(waits_work)
check("A2: 工作中间隔均值 ≈ 5.5×1.6=8.8s",
      abs(m_work - MOTION.BLINK_MEAN_S * MOTION.BLINK_WORK_RATE_SCALE) < 0.6,
      f"got {m_work:.2f}")
check("A3: 工作中上限随之放宽（MAX×scale），分布不被钳平",
      max(waits_work) > MOTION.BLINK_MAX_S + 0.5,
      f"max={max(waits_work):.2f}")
check("A4: 两种模式均不小于 BLINK_MIN_S",
      min(waits_norm) >= MOTION.BLINK_MIN_S and min(waits_work) >= MOTION.BLINK_MIN_S)
b = make_sched(rate_scale=MOTION.BLINK_WORK_RATE_SCALE)
with patched(BLINK_DOUBLE_PROB=0.0):
    _, total = force_trigger(b, 0.0)
gap = b.next_at - (b.blink_t0 + total)
check("A5: 触发后的调度间隔使用了放大系数",
      MOTION.BLINK_MIN_S <= gap <= MOTION.BLINK_MAX_S * MOTION.BLINK_WORK_RATE_SCALE + 1e-9,
      f"gap={gap:.2f}")

# ===== B. 部分眨眼 =====
print("\n[B] 部分眨眼（60–70% 的眨眼只闭到 40–70%）")
with patched(BLINK_PARTIAL_PROB=1.0, BLINK_SLOW_PROB=0.0):
    random.seed(11)
    depths, totals = [], []
    for _ in range(50):
        b = make_sched()
        d, t = force_trigger(b, 0.0)
        depths.append(d)
        totals.append(t)
    check("B1: 全部为部分眨眼，深度 ∈ [0.40, 0.70]",
          all(MOTION.BLINK_PARTIAL_DEPTH_MIN - 1e-9 <= d
              <= MOTION.BLINK_PARTIAL_DEPTH_MAX + 1e-9 for d in depths),
          f"min={min(depths):.2f} max={max(depths):.2f}")
    check("B2: 部分眨眼无保持段（上下睑不接触）",
          all(abs(t - (MOTION.BLINK_CLOSE_S + MOTION.BLINK_OPEN_S)) < 1e-9
              for t in totals), f"total={totals[0]:.3f}")
    b = make_sched()
    d, _ = force_trigger(b, 0.0)
    bottom = b.eye_opening_ratio(b.seg_close + 1e-6)
    check("B3: 最低睁开度 = 1-深度（本例 1-%.2f）" % d,
          abs(bottom - (1.0 - d)) < 0.02, f"bottom={bottom:.3f}")
    check("B4: 最低睁开度 ≥ 0.30（不会近乎全闭）",
          1.0 - MOTION.BLINK_PARTIAL_DEPTH_MAX >= 0.30 - 1e-9)
    check("B5: 部分眨眼全程不进入 hold 相位",
          b.blink_phase(b.seg_close + b.seg_open * 0.5) == "open"
          and b.blink_phase(b.seg_close - 1e-6) == "close")
    check("B6: 睁眼段从 1-深度 回到 1",
          abs(b.eye_opening_ratio(b.blink_t0 + b.blink_total) - 1.0) < 0.02)
with patched(BLINK_PARTIAL_PROB=0.0, BLINK_SLOW_PROB=0.0):
    b = make_sched()
    d, t = force_trigger(b, 0.0)
    check("B7: 概率钉 0 → 全闭眨眼（深度 1.0、含保持段、总时长不变）",
          d == 1.0 and abs(t - MOTION.BLINK_TOTAL_S) < 1e-9)
random.seed(23)
n_part = 0
for _ in range(400):
    b = make_sched()
    d, _ = force_trigger(b, 0.0)
    n_part += 1 if d < 1.0 else 0
frac = n_part / 400
check("B8: 65% 概率的实测占比落在 [0.55, 0.75]",
      0.55 <= frac <= 0.75, f"frac={frac:.2f}")

# ===== C. 慢眨（长时间无人） =====
print("\n[C] 久无人偶发慢眨（时长 ×1.5–2，必定全闭）")
with patched(BLINK_SLOW_PROB=1.0, BLINK_PARTIAL_PROB=0.0):
    random.seed(31)
    b = make_sched(idle=True)
    d, t = force_trigger(b, 0.0)
    check("C1: 慢眨时长落在 ×1.5–2.0 区间",
          MOTION.BLINK_TOTAL_S * MOTION.BLINK_SLOW_SCALE_MIN - 1e-9 <= t
          <= MOTION.BLINK_TOTAL_S * MOTION.BLINK_SLOW_SCALE_MAX + 1e-9,
          f"t={t:.3f}")
    check("C2: 慢眨必定全闭（深度 1.0，犯困感靠深而慢）", d == 1.0)
    check("C3: 慢眨保留全闭保持段",
          abs(b.seg_hold - MOTION.BLINK_HOLD_S * (t / MOTION.BLINK_TOTAL_S)) < 1e-9)
with patched(BLINK_SLOW_PROB=0.0, BLINK_PARTIAL_PROB=0.0):
    b = make_sched(idle=True)
    d, t = force_trigger(b, 0.0)
    check("C4: 慢眨概率钉 0 → 时长与常态一致", d == 1.0
          and abs(t - MOTION.BLINK_TOTAL_S) < 1e-9)
random.seed(37)
n_slow = 0
for _ in range(300):
    b = make_sched(idle=True)
    _, t = force_trigger(b, 0.0)
    n_slow += 1 if t > MOTION.BLINK_TOTAL_S + 1e-9 else 0
frac = n_slow / 300
check("C5: 45% 慢眨概率的实测占比落在 [0.35, 0.55]",
      0.35 <= frac <= 0.55, f"frac={frac:.2f}")
b = make_sched(idle=False)
with patched(BLINK_SLOW_PROB=1.0):
    d, t = force_trigger(b, 0.0)
    check("C6: 非 idle 状态不触发慢眨",
          d == 1.0 and abs(t - MOTION.BLINK_TOTAL_S) < 1e-9)

# ===== D. 禁眨（suppressed） =====
print("\n[D] 拖拽/被端详/反应动画期间禁眨")
# D 段断言依赖全闭眨眼的固定三段时序 → 钉住部分眨眼概率
with patched(BLINK_PARTIAL_PROB=0.0, BLINK_SLOW_PROB=0.0):
    b = make_sched(suppressed=True)
    for i in range(40):
        b.tick(i * 0.5)
    check("D1: 长时间禁眨期不开新眨眼", b.blink_total == 0.0 and b.blink_t0 == 0.0)
    b = make_sched(suppressed=False)
    _, _ = force_trigger(b, 0.0)
    t_mid = MOTION.BLINK_CLOSE_S * 0.5
    b.suppressed = True
    b.tick(t_mid)
    check("D2: 闭眼段被禁眨 → 跳到睁开段（不定格成半闭眼）",
          b.blink_phase(t_mid) == "open", f"phase={b.blink_phase(t_mid)}")
    check("D3: 跳段后从闭合度平滑睁开到 1",
          b.eye_opening_ratio(t_mid) < 1.0
          and abs(b.eye_opening_ratio(t_mid + b.seg_open) - 1.0) < 0.02,
          f"skip_at={b.eye_opening_ratio(t_mid):.2f}")
    b2 = make_sched(suppressed=False)
    force_trigger(b2, 0.0)
    t_hold = MOTION.BLINK_CLOSE_S + MOTION.BLINK_HOLD_S * 0.5
    b2.suppressed = True
    b2.tick(t_hold)
    check("D4: 全闭保持段被禁眨 → 立即进入睁眼段",
          b2.blink_phase(t_hold) == "open" and b2.eye_opening_ratio(t_hold) <= 0.02)
    b2.suppressed = False
    b2.tick(t_hold + 0.01)
    check("D5: 解除禁眨后不立刻补眨（间隔从当下重排）",
          b2.next_at >= t_hold + MOTION.BLINK_MIN_S - 1e-9,
          f"next_at={b2.next_at:.2f}")
    check("D6: 解除禁眨时被打断的双眨不接回（_pending_double 清除）",
          b2._pending_double is False)
    b3 = make_sched(suppressed=False)
    force_trigger(b3, 0.0)
    t_open = MOTION.BLINK_CLOSE_S + MOTION.BLINK_HOLD_S + MOTION.BLINK_OPEN_S * 0.5
    b3.suppressed = True
    b3.tick(t_open)
    check("D7: 睁眼段被禁眨 → 不跳段（已在睁开，让它自然结束）",
          abs(b3.blink_t0 - 0.0) < 1e-9 and b3.blink_phase(t_open) == "open")

# ===== E. 应用层否决 _blink_vetoed =====
print("\n[E] WhalePet._blink_vetoed 逐项生效")
app = W.WhalePet(run_seconds=3)
now = time.time()
check("E1: 默认（无事发生）→ 不否决", app._blink_vetoed(now) is False)
app._dragging = True
check("E2: 拖拽中 → 否决", app._blink_vetoed(now) is True)
app._dragging = False
app._hovering = True
check("E3: 悬停端详中 → 否决", app._blink_vetoed(now) is True)
app._hovering = False
app._pressed = True
check("E4: 按压中 → 否决", app._blink_vetoed(now) is True)
app._pressed = False
app._squash_t0 = now
check("E5: 按压回弹（squash 曲线）期间 → 否决", app._blink_vetoed(now) is True)
app._squash_t0 = 0.0
app._bub_mode = W.BUBBLE_OK
app._ok_anim_t0 = now
app._ok_anim_dur = MOTION.OK_POP_IN_S
check("E6: OK 弹入动画期间 → 否决", app._blink_vetoed(now) is True)
check("E7: OK 弹入动画结束后 → 不再否决",
      app._blink_vetoed(now + MOTION.OK_POP_IN_S + 0.01) is False)
app._ok_anim_dur = 0.0
app._bub_mode = W.BUBBLE_DEFAULT
app._emotion = MOTION.EMOTION_SUCCESS
check("E8: 成功情绪（眯眼笑）→ 否决（眨眼让位）",
      app._blink_vetoed(now) is True)
app._emotion = MOTION.EMOTION_WELCOME
check("E9: 欢迎情绪（同为 happy 脸）→ 否决", app._blink_vetoed(now) is True)
app._emotion = MOTION.EMOTION_FAIL
check("E10: 失败情绪（pout 脸有独立眼位）→ 不否决",
      app._blink_vetoed(now) is False)
app._emotion = MOTION.EMOTION_NEUTRAL

# ===== F. _update_motion 集成 =====
print("\n[F] _update_motion 每帧喂状态")
app.active = [{"title": "t"}]
app._update_motion(now)
check("F1: 会话运行中 → 调度器拿到工作降频系数",
      app._blinker.rate_scale == MOTION.BLINK_WORK_RATE_SCALE)
app.active = []
app._update_motion(now)
check("F2: 无运行会话 → 系数回 1.0", app._blinker.rate_scale == 1.0)
app._last_interact = now - MOTION.IDLE_DOWNGRADE_S - 1.0
app._update_motion(now)
check("F3: 久无人交互 → idle=True（偶发慢眨）", app._blinker.idle is True)
app._last_interact = now
app._update_motion(now)
check("F4: 刚有交互 → idle=False", app._blinker.idle is False)
app._emotion = MOTION.EMOTION_SUCCESS
app._update_motion(now)
check("F5: 成功情绪帧内 → suppressed=True", app._blinker.suppressed is True)
app._emotion = MOTION.EMOTION_NEUTRAL
app._dragging = True
app._update_motion(now)
check("F6: 拖拽帧内 → suppressed=True", app._blinker.suppressed is True)
app._dragging = False
app._update_motion(now)
check("F7: 恢复常态 → suppressed=False", app._blinker.suppressed is False)

# ===== G. 签名量化（部分眨眼也要触发重绘） =====
print("\n[G] 动效签名对眨眼做 4 级量化")
base = 100.0
app._blinker.blink_t0 = 0.0
app._blinker.blink_total = 0.0
sig_open = app._motion_signature(base, "")
app._blinker.blink_t0 = base
app._blinker.blink_total = MOTION.BLINK_TOTAL_S   # 全闭眨眼（默认分段）
t_mid = base + MOTION.BLINK_CLOSE_S * 0.75
sig_mid = app._motion_signature(t_mid, "")
sig_closed = app._motion_signature(
    base + MOTION.BLINK_CLOSE_S + MOTION.BLINK_HOLD_S * 0.5, "")
check("G1: 全闭眨眼的中间态/闭合态与全开签名不同",
      sig_mid != sig_open and sig_closed != sig_open)
app._blinker.close_depth = 0.45          # 部分眨眼：最低睁到 0.55
app._blinker.seg_hold = 0.0
app._blinker.blink_total = MOTION.BLINK_CLOSE_S + MOTION.BLINK_OPEN_S
sig_part = app._motion_signature(base + MOTION.BLINK_CLOSE_S, "")
check("G2: 部分眨眼最低点（ratio≈0.55）签名 ≠ 全开（旧 >0.5 判定会漏）",
      sig_part != sig_open,
      f"part={sig_part[7]} open={sig_open[7]}")
app._blinker.close_depth = 1.0
app._blinker.seg_hold = MOTION.BLINK_HOLD_S
app._blinker.blink_total = 0.0
app._blinker.blink_t0 = 0.0

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")

sys.exit(0 if not FAILED else 1)
