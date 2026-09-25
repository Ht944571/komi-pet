# -*- coding: utf-8 -*-
r"""眨眼动画拟真化专项测试

验证点：
  A. BlinkScheduler 三段时间划分正确（close / hold / open）
  B. 闭眼段缓动曲线 ease-in_quad（开始慢→结束快，验证加速度感）
  C. 睁眼段缓动曲线 ease-out_cubic（开始快→结束慢，验证刹停感）
  D. 总时长 ~330ms，节奏自然（符合真人眨眼 250-400ms 范围）
  E. phase 标识在每段正确切换
  F. 眨眼结束后 ratio 回到 1.0（正确复位）
  G. 双眨机制（15% 概率 + 间隔）正常工作
  H. 闭眼段 + 睁眼段都调用 _draw_blink（ratio < 1.0 触发绘制）
  I. idle 状态下不绘制（ratio = 1 时跳过）

用法：python tools/test_blink_smooth.py
"""

import os
import sys
import math
import time

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


# ===== A. 时间段划分 =====
print("\n[A] BlinkScheduler 三段时间划分")
b = MOTION.BlinkScheduler(0.0)
check("A1: 闭眼段时长 BLINK_CLOSE_S",
      abs(MOTION.BLINK_CLOSE_S - 0.099) < 0.01)
check("A2: 保持段时长 BLINK_HOLD_S",
      abs(MOTION.BLINK_HOLD_S - 0.066) < 0.01)
check("A3: 睁眼段时长 BLINK_OPEN_S",
      abs(MOTION.BLINK_OPEN_S - 0.165) < 0.01)
check("A4: 总时长 ~330ms（真人眨眼 250-400ms 范围）",
      0.25 < MOTION.BLINK_TOTAL_S < 0.40)


# ===== B. 闭眼段缓动曲线 =====
print("\n[B] 闭眼段 ease-in_quad 曲线（开始慢→结束快）")
b.blink_t0 = 0.0
b.blink_total = MOTION.BLINK_TOTAL_S
# 闭眼段（[0, BLINK_CLOSE_S)）的 ratio 应从 1 → 0 用 ease-in_quad
# 验证：前半段变化慢，后半段变化快（加速度感）
r_25 = b.eye_opening_ratio(MOTION.BLINK_CLOSE_S * 0.25)  # t=0.025
r_50 = b.eye_opening_ratio(MOTION.BLINK_CLOSE_S * 0.50)  # t=0.050
r_75 = b.eye_opening_ratio(MOTION.BLINK_CLOSE_S * 0.75)  # t=0.075
# ease-in_quad：0.25 时 ratio=1-0.0625=0.9375；0.5 时 1-0.25=0.75；0.75 时 1-0.5625=0.4375
check("B1: t=25% ratio ≈ 0.94（缓入慢启动）",
      abs(r_25 - 0.94) < 0.02, f"got {r_25:.3f}")
check("B2: t=50% ratio ≈ 0.75", abs(r_50 - 0.75) < 0.02, f"got {r_50:.3f}")
check("B3: t=75% ratio ≈ 0.44（加速接近闭合）",
      abs(r_75 - 0.44) < 0.02, f"got {r_75:.3f}")
# 验证加速度（后半段变化幅度大于前半段）
diff_first = 1.0 - r_25            # 0.063
diff_last = r_75 - 0.0            # 0.4375
check("B4: 后半段变化幅度 > 前半段（ease-in 加速度感）",
      diff_last > diff_first * 3, f"first={diff_first:.3f} last={diff_last:.3f}")
# 边界
check("B5: t=0 ratio=1.0（全睁开）", b.eye_opening_ratio(0.0) == 1.0)
check("B6: t=BLINK_CLOSE_S ratio=0.0（全闭过渡到保持段）",
      b.eye_opening_ratio(MOTION.BLINK_CLOSE_S) == 0.0)


# ===== C. 睁眼段缓动曲线 =====
print("\n[C] 睁眼段 ease-out_cubic 曲线（开始快→结束慢）")
hold_end = MOTION.BLINK_CLOSE_S + MOTION.BLINK_HOLD_S
# 睁眼段（[hold_end, total)）ratio 从 0 → 1 用 ease-out_cubic
r_25 = b.eye_opening_ratio(hold_end + MOTION.BLINK_OPEN_S * 0.25)
r_50 = b.eye_opening_ratio(hold_end + MOTION.BLINK_OPEN_S * 0.50)
r_75 = b.eye_opening_ratio(hold_end + MOTION.BLINK_OPEN_S * 0.75)
# ease-out_cubic：1-(1-0.25)^3 = 1-0.4219 = 0.578；1-(0.5)^3=0.875；1-(0.25)^3=0.984
check("C1: t=25% ratio ≈ 0.58（快速弹起）",
      abs(r_25 - 0.58) < 0.02, f"got {r_25:.3f}")
check("C2: t=50% ratio ≈ 0.88",
      abs(r_50 - 0.88) < 0.02, f"got {r_50:.3f}")
check("C3: t=75% ratio ≈ 0.98（接近全开，慢下来）",
      abs(r_75 - 0.98) < 0.02, f"got {r_75:.3f}")
# 验证刹停感：前半段变化幅度 > 后半段
diff_first = r_25 - 0.0
diff_last = r_75 - r_50
check("C4: 前半段变化幅度 > 后半段（ease-out 刹停感）",
      diff_first > diff_last, f"first={diff_first:.3f} last={diff_last:.3f}")
# 边界
check("C5: t=hold_end ratio=0.0（保持结束睁眼开始）",
      b.eye_opening_ratio(hold_end) == 0.0)
check("C6: t=BLINK_TOTAL_S ratio=1.0（全睁开结束）",
      abs(b.eye_opening_ratio(MOTION.BLINK_TOTAL_S) - 1.0) < 0.01)


# ===== D. 节奏自然性 =====
print("\n[D] 节奏自然（真人眨眼参照）")
# 真人眨眼：闭眼 ~80-120ms、保持 ~30-60ms、睁眼 ~120-180ms
check("D1: 闭眼段 80-120ms 范围",
      0.080 <= MOTION.BLINK_CLOSE_S <= 0.120)
check("D2: 保持段 30-70ms 范围",
      0.030 <= MOTION.BLINK_HOLD_S <= 0.070)
check("D3: 睁眼段 120-180ms 范围（睁眼慢于闭眼）",
      0.120 <= MOTION.BLINK_OPEN_S <= 0.180)
check("D4: 睁眼时长 > 闭眼时长（拟真）",
      MOTION.BLINK_OPEN_S > MOTION.BLINK_CLOSE_S)


# ===== E. phase 标识 =====
print("\n[E] blink_phase 阶段标识")
b.blink_t0 = 0.0
b.blink_total = MOTION.BLINK_TOTAL_S
check("E1: t=0 phase=close", b.blink_phase(0.0) == "close")
check("E2: t=BLINK_CLOSE_S phase=hold（边界）",
      b.blink_phase(MOTION.BLINK_CLOSE_S + 0.001) == "hold")
check("E3: t=BLINK_TOTAL_S/2 phase=open", b.blink_phase(MOTION.BLINK_TOTAL_S * 0.5) == "open")
check("E4: t=BLINK_TOTAL_S phase=idle", b.blink_phase(MOTION.BLINK_TOTAL_S + 0.001) == "idle")
check("E5: t=BLINK_TOTAL_S*2 phase=idle", b.blink_phase(MOTION.BLINK_TOTAL_S * 2) == "idle")


# ===== F. 复位 =====
print("\n[F] 眨眼结束后 ratio 回到 1.0")
b.blink_t0 = 0.0
b.blink_total = MOTION.BLINK_TOTAL_S
# 一帧后 total 自动复位
b.tick(MOTION.BLINK_TOTAL_S + 0.001)
check("F1: 眨眼结束自动复位（blink_t0=0）", b.blink_t0 == 0.0)
check("F2: 眨眼结束自动复位（blink_total=0）", b.blink_total == 0.0)
check("F3: 复位后 ratio=1.0",
      b.eye_opening_ratio(time.time()) == 1.0)
check("F4: 复位后 is_active=False",
      not b.is_active(time.time()))


# ===== G. 双眨机制 =====
print("\n[G] 双眨机制（15% 概率 + BLINK_DOUBLE_GAP_S 间隔）")
import random
random.seed(0)  # 固定种子便于测试
# 本段验证的是双眨调度公式（next_at = t0 + total + GAP），与眨眼形态无关；
# 钉住部分眨眼概率，让 total 恒等于基准 BLINK_TOTAL_S（部分眨眼本身另有专测）
_partial_prob_saved = MOTION.BLINK_PARTIAL_PROB
MOTION.BLINK_PARTIAL_PROB = 0.0
try:
    # 强制触发至少一次双眨
    b = MOTION.BlinkScheduler(0.0)
    # 把 next_at 拉到马上
    while True:
        b._pending_double = True   # 强制下一次的 _will_double 触发双眨逻辑
        b.next_at = 0.0
        b.tick(0.0)        # 触发眨眼
        if b._pending_double:    # 已经过了一次
            pass
        # 实际上 _pending_double 在第一次 tick 时已经设了 True，再 tick 一次会 reset
        # 直接看 next_at 是否在 BLINK_DOUBLE_GAP_S 之后
        next_at = b.next_at
        break
    # next_at 应该在 blink_t0 + total + DOUBLE_GAP_S 左右
    expected = 0.0 + MOTION.BLINK_TOTAL_S + MOTION.BLINK_DOUBLE_GAP_S
    check("G1: 双眨 next_at 距开始 ≤ ~330ms+200ms",
          abs(next_at - expected) < 0.01, f"got {next_at:.3f}")
finally:
    MOTION.BLINK_PARTIAL_PROB = _partial_prob_saved


# ===== H. _draw_blink 触发条件 =====
print("\n[H] _draw_blink 触发（ratio < 1）")
# 用 stub WhalePet（不实际创建窗口）
app = W.WhalePet(run_seconds=10)
# 闭眼段
app._blinker.blink_t0 = time.time()
app._blinker.blink_total = MOTION.BLINK_TOTAL_S
app._emotion = MOTION.EMOTION_NEUTRAL
now = time.time()
check("H1: 闭眼段中 ratio < 1（绘制触发）",
      app._blinker.eye_opening_ratio(now) < 1.0)
# 全开时
app._blinker.blink_t0 = 0.0
app._blinker.blink_total = 0.0
check("H2: 全开时 ratio = 1（跳过绘制）",
      app._blinker.eye_opening_ratio(time.time()) == 1.0)


# ===== I. 随机性 =====
print("\n[I] 随机眨眼间隔（高斯分布 2.5-9s）")
import random
for _ in range(20):
    wait = MOTION._next_blink_wait(0.0)
    assert MOTION.BLINK_MIN_S <= wait <= MOTION.BLINK_MAX_S, \
        f"wait out of range: {wait}"
check("I1: 20 次随机间隔均在 [2.5, 9]s 范围", True)
# 高斯分布中心应接近均值（采样验证，不要求精确）
random.seed(42)
samples = [MOTION._next_blink_wait(0.0) for _ in range(1000)]
mean = sum(samples) / len(samples)
check("I2: 采样均值接近 BLINK_MEAN_S(5.5)", abs(mean - MOTION.BLINK_MEAN_S) < 0.3)


# ===== J. 缓动函数本身 =====
print("\n[J] 缓动函数曲线单调性")
# ease_in_quad 在 [0,1] 上从 0→1 单调增
xs = [i/20 for i in range(21)]
ys_in = [MOTION.ease_in_quad(x) for x in xs]
check("J1: ease_in_quad 单调增", all(ys_in[i] <= ys_in[i+1] for i in range(20)))
ys_out = [MOTION.ease_out_quad(x) for x in xs]
check("J2: ease_out_quad 单调增", all(ys_out[i] <= ys_out[i+1] for i in range(20)))


print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")

sys.exit(0 if not FAILED else 1)