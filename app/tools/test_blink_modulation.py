# -*- coding: utf-8 -*-
r"""眨眼状态调制 + 部分眨眼 + 三层标准 专项测试（BlinkScheduler v2）

对照《眨眼最高标准》（三层：通用核心 / 分技术路线 / 交互级）：
  A. 间隔带：常态 3~8s 随机（禁止固定周期）；注视/专注 8~12s；夜间拉长
  B. 部分眨眼：占比 65%、闭合深度 40–70%、无保持段
  C. 慢眨（久无人）：时长 ×1.5–2、必定全闭
  D. 禁眨（suppressed）：拖拽/被端详/反应动画期间
  E. 应用层否决 _blink_vetoed：拖拽/悬停/按压/OK 弹入/成功态
  F. _update_motion 接线：attention=悬停、busy=会话、night=夜间
  G. 受惊连眨（交互级 §三.2）：立即双连眨
  H. 强制眨眼 blink_now（状态回归待机先眨一次，§三.3）
  I. 头部微点 nod（§一.2：眨眼不孤立）
  J. 签名量化（部分眨眼也触发重绘）

用法：python tools/test_blink_modulation.py
"""
import json
import os
import sys
import time
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_motion as MOTION                         # noqa: E402
import wb_whale_win as W                           # noqa: E402

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
    b._refresh_interval_band(0.0)      # 属性设置后刷新间隔带（attention/night/busy）
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


# ===== A. 间隔带（完全随机；注视/专注 8~12s；夜间拉长） =====
print("\n[A] 间隔带（常态 / 注视·专注 / 夜间）")
random.seed(7)
waits_norm = [MOTION._next_blink_wait(0.0) for _ in range(2000)]
check("A0: 常态带宽 = 3~8s（生理规律 §一.1）",
      min(waits_norm) >= MOTION.BLINK_MIN_S and max(waits_norm) <= MOTION.BLINK_MAX_S)
m_norm = sum(waits_norm) / len(waits_norm)
check("A0b: 常态均值 ≈ 3.6s（≈16 次/分，接近最佳 18 次/分）",
      abs(m_norm - MOTION.BLINK_MEAN_S) < 0.6, f"mean={m_norm:.2f}")
b = make_sched(attention=True)
waits_att = [MOTION._next_blink_wait(0.0, b._interval_mean, b._interval_lo,
                                     b._interval_hi) for _ in range(2000)]
check("A1: 注视/专注带 = 8~12s（§三.1 注视式）",
      min(waits_att) >= MOTION.BLINK_ATTENTION_MIN_S - 0.01
      and max(waits_att) <= MOTION.BLINK_ATTENTION_MAX_S + 0.01,
      f"[{min(waits_att):.2f},{max(waits_att):.2f}]")
bn = make_sched(night=True)
waits_night = [MOTION._next_blink_wait(0.0, bn._interval_mean, bn._interval_lo,
                                       bn._interval_hi) for _ in range(2000)]
check("A2: 夜间均值 > 常态均值（频率降低，§三.4）",
      sum(waits_night) / len(waits_night) > sum(waits_norm) / len(waits_norm) * 1.3)
# 完全随机：样本方差显著（固定周期的方差≈0）
import statistics as _st
check("A3: 间隔完全随机（方差 > 0.5，非固定周期）",
      _st.pvariance(waits_norm) > 0.5)

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
              for t in totals))
with patched(BLINK_PARTIAL_PROB=0.0, BLINK_SLOW_PROB=0.0):
    b = make_sched()
    d, t = force_trigger(b, 0.0)
    check("B3: 概率钉 0 → 全闭眨眼", d == 1.0
          and abs(t - MOTION.BLINK_TOTAL_S) < 1e-9)

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

# ===== D. 禁眨（suppressed） =====
print("\n[D] 拖拽/被端详/反应动画期间禁眨")
with patched(BLINK_PARTIAL_PROB=0.0, BLINK_SLOW_PROB=0.0):
    b = make_sched(suppressed=True)
    for i in range(40):
        b.tick(i * 0.5)
    check("D1: 长时间禁眨期不开新眨眼", b.blink_total == 0.0 and b.blink_t0 == 0.0)
    b = make_sched(suppressed=False)
    force_trigger(b, 0.0)
    t_mid = MOTION.BLINK_CLOSE_S * 0.5
    b.suppressed = True
    b.tick(t_mid)
    check("D2: 闭眼段被禁眨 → 跳到睁开段（不定格成半闭眼）",
          b.blink_phase(t_mid) == "open", f"phase={b.blink_phase(t_mid)}")
    b.suppressed = False
    b.tick(t_mid + 0.01)
    check("D3: 解除禁眨后不立刻补眨（间隔从当下重排）",
          b.next_at >= t_mid + MOTION.BLINK_MIN_S - 1e-9)

# ===== E. 应用层否决 _blink_vetoed =====
print("\n[E] WhalePet._blink_vetoed 逐项生效")
app = W.WhalePet(run_seconds=2)
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
app._emotion = MOTION.EMOTION_NEUTRAL

# ===== F. _update_motion 接线 =====
print("\n[F] _update_motion 每帧接线（attention/busy/night/suppressed/idle）")
app.active = [{"title": "t"}]
app._hovering = True
app._update_motion(now)
check("F1: 悬停 → attention=True（注视式）", app._blinker.attention is True)
check("F2: 会话运行中 → busy=True（专注态）", app._blinker.busy is True)
check("F3: 两者并存 → 注视带优先（8~12s）",
      app._blinker._interval_lo == MOTION.BLINK_ATTENTION_MIN_S)
app.active = []
app._hovering = False
app._update_motion(now)
check("F4: 恢复常态 → busy/attention 复位",
      app._blinker.busy is False and app._blinker.attention is False)
app.follow_on = True
app.linked_expected = True     # 避免守语语义干扰：这里只看标志传递
app.linked_on = getattr(app, "linked_on", True)
app._update_motion(now)
# 夜间接线
import time as _t
app._night_mode = lambda: True
app._update_motion(now)
check("F5: 夜间 → night=True", app._blinker.night is True)
app._night_mode = lambda: False
app._update_motion(now)
check("F6: 白天 → night=False", app._blinker.night is False)
app.linked_expected = getattr(app, "linked_expected", False)
app._last_interact = now - MOTION.IDLE_DOWNGRADE_S - 1.0
app._update_motion(now)
check("F7: 久无人交互 → idle=True（偶发慢眨）", app._blinker.idle is True)
app._last_interact = now
app._update_motion(now)
check("F8: 刚有交互 → idle=False", app._blinker.idle is False)

# ===== G. 受惊连眨（交互级 §三.2） =====
print("\n[G] 受惊连眨：startle → 立即双连眨（均全闭、间隔 <300ms）")
random.seed(41)
b = make_sched()
b.startle(now)
b.tick(now)
check("G1: startle → 立即开始第一次眨眼（全闭）",
      b.blink_total > 0 and b.close_depth == 1.0)
end1 = b.blink_t0 + b.blink_total
b.tick(end1 + MOTION.BLINK_STARTLE_GAP_S + 0.01)   # 主眨结束 → 第二眨调度
check("G2: 第二眨紧跟（主眨结束后 ≤80ms；start-to-start <300ms）",
      end1 <= b.blink_t0 <= end1 + 0.08 and (b.blink_t0 - now) < 0.30,
      f"t0={b.blink_t0:.3f} end1={end1:.3f}")
check("G3: 第二眨亦全闭", b.close_depth == 1.0)
end2 = b.blink_t0 + b.blink_total
b.tick(end2 + 0.01)
check("G4: 恰好两次（第二眨后不再追加）",
      b._startle_left == 0 and b.blink_total == 0.0)

# ===== H. 强制眨眼（状态回归待机先眨一次） =====
print("\n[H] blink_now：状态回归待机先自然眨一次（§三.3）")
random.seed(43)
b = make_sched()
b.next_at = now + 999                          # 本来很久后才眨
b.blink_now(now)
b.tick(now)
check("H1: blink_now → 立即开始眨眼", b.blink_total > 0)
b2 = make_sched(suppressed=True)
b2.blink_now(now)
b2.tick(now)
check("H2: 禁眨期 blink_now 被压制（不与拖拽叠加）",
      b2.blink_total == 0.0 and b2.next_at is not None)

# ===== I. 头部微点 nod（§一.2 眨眼不孤立） =====
print("\n[I] 头部微点 nod 包络")
b = make_sched()
b.next_at = 0.0
b.blink_t0 = 0.0
b.blink_total = MOTION.BLINK_TOTAL_S
mid = MOTION.BLINK_TOTAL_S * 0.5
nod_mid = b.nod(mid)
check("I1: 眨眼中段 nod 达峰（0~1 包络）", 0.9 <= nod_mid <= 1.0,
      f"nod={nod_mid:.2f}")
check("I2: 眨眼外 nod=0", b.nod(MOTION.BLINK_TOTAL_S + 1.0) == 0.0)
check("I3: 换算像素在 1~2px 标准（BLINK_NOD_PX）",
      MOTION.BLINK_NOD_PX * nod_mid <= 2.0)

# ===== J. 签名量化（部分眨眼也触发重绘） =====
print("\n[J] 动效签名对眨眼做 4 级量化")
base = 100.0
app._blinker.blink_t0 = 0.0
app._blinker.blink_total = 0.0
sig_open = app._motion_signature(base, "")
app._blinker.blink_t0 = base
app._blinker.blink_total = MOTION.BLINK_TOTAL_S
t_mid = base + MOTION.BLINK_CLOSE_S * 0.75
sig_mid = app._motion_signature(t_mid, "")
sig_closed = app._motion_signature(
    base + MOTION.BLINK_CLOSE_S + MOTION.BLINK_HOLD_S * 0.5, "")
check("J1: 全闭眨眼的中间态/闭合态与全开签名不同",
      sig_mid != sig_open and sig_closed != sig_open)
app._blinker.close_depth = 0.45
app._blinker.seg_hold = 0.0
app._blinker.blink_total = MOTION.BLINK_CLOSE_S + MOTION.BLINK_OPEN_S
sig_part = app._motion_signature(base + MOTION.BLINK_CLOSE_S, "")
check("J2: 部分眨眼最低点签名 ≠ 全开", sig_part != sig_open)
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
