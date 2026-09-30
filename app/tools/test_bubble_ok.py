# -*- coding: utf-8 -*-
r"""桌宠气泡「OK 完成态」测试：状态机断言 + 真实渲染快照。

状态机迁移：
  default → ok      ：运行中会话数 由 >0 跌到 0（一轮对话任务结束）
  ok      → default ：气泡被连续点击 3 次（窗口 2s，超时计数归零）
  ok      → default ：新对话开始（会话数重新 >0）

渲染快照直接读 Surface 的 DIB 像素（BGRA 预乘 alpha），不依赖截屏，
所以不受桌面遮挡 / DPI / 窗口位置影响。

用法：python tools/test_bubble_ok.py [--png 输出路径]
"""
import argparse
import os
import sys
import time

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_whale_win as W                                  # noqa: E402
import wb_motion as M                                     # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name if cond else f"{name}  {detail}")
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))


# ============================================================
# Part A：状态机（不建窗口，纯逻辑）
# ============================================================
class Stub:
    ok_autodismiss_on = True      # 新状态契约：行为开关是实例标志（Stub 默认出厂值）
    linked_close_on = True
    # 活跃数防抖（2026-09-29 加）：_sync_bubble_mode 会调它，用真实现（纯逻辑无副作用）
    _stable_active_n = W.WhalePet._stable_active_n
    """只保留状态机需要的那几个入口，_layout / 音效 / 粒子 / 事件上报全部 stub 掉。"""

    def __init__(self):
        self._bub_mode = W.BUBBLE_DEFAULT
        self._prev_active_n = None
        self._n_stable = 0            # 防抖的"已生效活跃数"
        self._n_hold_since = 0.0
        self._fall_debounce_s = 0.0   # 本文件验状态机迁移 → 关掉下降防抖窗口
        # 写字动作相位：_sync_bubble_mode 用**同一个判据**顺带驱动它（见 wb_anim 模块头），
        # 所以 Stub 也要有。给它真状态机，相位迁移才能被一起回归到。
        self._wr = W.ANIM.PetPhase()
        self._ok_clicks = 0
        self._ok_last_click = 0.0
        self._ok_anim_t0 = 0.0
        self._ok_anim_dur = 0.0
        self._ok_anim_from = 1.0
        self._ok_pulse_t0 = 0.0
        self._ok_release_at = 0.0
        # 自动消失相关（焦点状态由测试手动翻转，避免依赖真实窗口）
        self._ok_entered_at = 0.0
        self._ok_fg_at_entry = False
        self._ok_auto_dismissed = False
        self._wb_hwnd = None
        self._wb_hwnd_checked_at = 0.0
        self._wb_fg_prev = False
        self._wb_focused = False
        self.active = []
        self._drawn_sig = None
        self._motion_sig = None
        self.played = []
        self.spawned = []
        self.events = []

    def _layout(self):
        return {"W": 300, "H": 372, "bub_h": 128, "bubble_h": 162,
                "pet_h": 210, "sc": 1.0}

    def _bubble_box(self, lay):
        """新契约（2026-09-27）：线上气泡改为「锚定立绘实际头顶」，_set_bubble_mode
        的 OK 迸发粒子会调用它。Stub 没有立绘 → 退化成旧的「贴窗口顶部」几何即可，
        本用例只关心状态机迁移，不校验气泡像素位置。"""
        sc = lay["sc"]
        return 6 * sc, 4 * sc, lay["W"] - 12 * sc, lay["bub_h"]

    def _play(self, key):
        self.played.append(key)

    def _spawn_particles(self, *a):
        self.spawned.append(a)

    def _report_event(self, *a, **k):
        self.events.append((a, k))

    # 状态机方法内部会 self.xxx() 调用彼此 → 必须把真实现绑到 stub 上
    def _nb_stats(self):
        """Stub 没有真实会话数据 → 空统计（本子只做迁移，不显示内容）。"""
        return {}

    def _set_bubble_mode(self, mode, now=None, sound=True):
        return W.WhalePet._set_bubble_mode(self, mode, now, sound)

    def _sync_bubble_mode(self):
        return W.WhalePet._sync_bubble_mode(self)

    def _bubble_ok_click(self):
        return W.WhalePet._bubble_ok_click(self)

    def _is_wb_focused(self, now):
        return self._wb_focused

    def _ok_auto_dismiss(self, now=None):
        return W.WhalePet._ok_auto_dismiss(self, now)


def sync(s):
    s._sync_bubble_mode()


def click(s, now):
    """按给定时刻模拟一次气泡点击（_bubble_ok_click 内部取 time.time()）。"""
    real = time.time
    time.time = lambda: now
    try:
        s._bubble_ok_click()
    finally:
        time.time = real


print("\n[A] 状态机迁移")
s = Stub()

# A1 首次取样只建基线，不触发
s.active = [{"title": "对话A"}]
sync(s)
check("A1 首次取样不触发（仅建基线）",
      s._bub_mode == W.BUBBLE_DEFAULT and s._prev_active_n == 1,
      f"mode={s._bub_mode} prev={s._prev_active_n}")

# A2 任务结束（1 → 0）进入 OK
s.active = []
sync(s)
check("A2 任务结束 1→0 进入 OK", s._bub_mode == W.BUBBLE_OK, f"mode={s._bub_mode}")
check("A2 进场动画/音效/粒子已触发",
      s._ok_anim_dur == M.OK_POP_IN_S and "chirp" in s.played and s.spawned,
      f"dur={s._ok_anim_dur} played={s.played} spawned={len(s.spawned)}")

# A3 持续为 0 不重复触发（幂等）
ev_before = len(s.events)
s.active = []
sync(s)
check("A3 持续空态不重复切换（幂等）", len(s.events) == ev_before)

# A4 连击：未点够时不切换，计数逐次累加（阈值取自 OK_CLICKS_NEEDED，线上现为 1）
NEED = M.OK_CLICKS_NEEDED
t0 = 1000.0
check("A4 起点：计数 0、仍是 OK",
      s._ok_clicks == 0 and s._bub_mode == W.BUBBLE_OK,
      f"clicks={s._ok_clicks} mode={s._bub_mode}")
for i in range(1, NEED):
    click(s, t0 + 0.4 * (i - 1))
    check(f"A4 第 {i} 次点击：计数 {i}、仍是 OK",
          s._ok_clicks == i and s._bub_mode == W.BUBBLE_OK,
          f"clicks={s._ok_clicks} mode={s._bub_mode}")
if NEED > 1:
    check("A4 未点够时不设释放时刻", s._ok_release_at == 0.0)

# A5 点够 → 设保持段（不立刻切，让这一下被看见）
t_last = t0 + 0.4 * (NEED - 1)
click(s, t_last)
check(f"A5 第 {NEED} 次点击：计数 {NEED}、进入'完成'保持段",
      s._ok_clicks == NEED and s._ok_release_at > 0
      and s._bub_mode == W.BUBBLE_OK,
      f"clicks={s._ok_clicks} release={s._ok_release_at} mode={s._bub_mode}")
click(s, t_last + 0.1)
check("A5 保持段内多余点击被忽略（计数不涨）", s._ok_clicks == NEED,
      f"clicks={s._ok_clicks}")
check(f"A5 释放时刻 = 第 {NEED} 次点击 + OK_RELEASE_HOLD_S",
      abs(s._ok_release_at - (t_last + M.OK_RELEASE_HOLD_S)) < 1e-6,
      f"release={s._ok_release_at}")

# A6 保持段结束 → 回默认态
W.WhalePet._set_bubble_mode(s, W.BUBBLE_DEFAULT, s._ok_release_at)
check("A6 保持段结束回到 default",
      s._bub_mode == W.BUBBLE_DEFAULT and s._ok_clicks == 0,
      f"mode={s._bub_mode} clicks={s._ok_clicks}")

# A7 连击窗口超时 → 计数归零重来。
# 线上阈值为 1 时第一次点击即完成，窗口逻辑走不到；这里临时把阈值调到 3，
# 保证"滑窗重置"这段代码不被阈值变化掏空覆盖。
_need_real = M.OK_CLICKS_NEEDED
M.OK_CLICKS_NEEDED = 3
try:
    s2 = Stub()
    s2.active = [{"title": "x"}]
    sync(s2)
    s2.active = []
    sync(s2)
    click(s2, 2000.0)
    click(s2, 2000.0 + M.OK_CLICK_WINDOW_S + 0.5)          # 超窗口
    check("A7 两次点击超过连击窗口 → 计数从 1 重来",
          s2._ok_clicks == 1, f"clicks={s2._ok_clicks}")
    click(s2, 2000.0 + M.OK_CLICK_WINDOW_S + 0.9)
    check("A7 超时后重新累计到 2", s2._ok_clicks == 2, f"clicks={s2._ok_clicks}")
finally:
    M.OK_CLICKS_NEEDED = _need_real

# A8 新对话开始 → 强制回 default（避免任务在跑却挂"完成"）
s3 = Stub()
s3.active = [{"title": "x"}]
sync(s3)
s3.active = []
sync(s3)
check("A8 前置：已进入 OK", s3._bub_mode == W.BUBBLE_OK)
s3.active = [{"title": "新任务"}]
sync(s3)
check("A8 新对话开始 → 回到 default", s3._bub_mode == W.BUBBLE_DEFAULT,
      f"mode={s3._bub_mode}")

# A9 动画曲线（单独建一个停在"进场中"的 stub，避免复用上面已回默认态的 s）
s9 = Stub()
s9._bub_mode = W.BUBBLE_OK
T0 = 100.0
s9._ok_anim_t0, s9._ok_anim_dur = T0, M.OK_POP_IN_S
s9._ok_anim_from = M.OK_GLYPH_MIN_SCALE
gs0, k0, anim0 = W.WhalePet._ok_visual(s9, T0)
check("A9 进场起点：字形小、橙度 0、动画中",
      anim0 and gs0 < 0.7 and k0 < 0.05, f"gs={gs0:.3f} k={k0:.3f}")
t = T0 + M.OK_POP_IN_S * 0.6
gsm, km, am = W.WhalePet._ok_visual(s9, t)
check("A9 进场中途：字形放大、橙度上升", am and gsm > gs0 and km > k0,
      f"gs={gsm:.3f} k={km:.3f}")
gse, ke, ae = W.WhalePet._ok_visual(s9, T0 + M.OK_POP_IN_S + 0.01)
check("A9 进场结束：稳定在 1.0 / 全橙 / 无动画",
      (not ae) and abs(gse - 1.0) < 1e-6 and abs(ke - 1.0) < 1e-6,
      f"gs={gse:.3f} k={ke:.3f} anim={ae}")
over = max(W.WhalePet._ok_visual(s9, T0 + M.OK_POP_IN_S * i / 200)[0]
           for i in range(201))
check("A9 有过冲但不突破克制上限 SCALE_MAX",
      over > 1.0 and over <= M.SCALE_MAX + 1e-9,
      f"max={over:.4f} cap={M.SCALE_MAX}")

# A9b 退场曲线：字形收小、橙度回落
s9b = Stub()
s9b._bub_mode = W.BUBBLE_DEFAULT
T0b = 200.0
s9b._ok_anim_t0, s9b._ok_anim_dur = T0b, M.OK_POP_OUT_S
s9b._ok_anim_from = 1.0
g_a, k_a, a_a = W.WhalePet._ok_visual(s9b, T0b)
g_b, k_b, a_b = W.WhalePet._ok_visual(s9b, T0b + M.OK_POP_OUT_S * 0.5)
g_c, k_c, a_c = W.WhalePet._ok_visual(s9b, T0b + M.OK_POP_OUT_S + 0.01)
check("A9b 退场：字形 1→中途变小→0，橙度同步回落",
      a_a and abs(g_a - 1.0) < 1e-6 and g_c < 1e-9 and 0 < g_b < g_a
      and k_c < k_b < k_a,
      f"g=({g_a:.3f},{g_b:.3f},{g_c:.3f}) k=({k_a:.3f},{k_b:.3f},{k_c:.3f})")
check("A9b 退场结束无动画", not a_c)

# A10 点击脉冲：起止归零、中间为正
s._ok_pulse_t0 = 500.0
check("A10 脉冲起点为 0", abs(W.WhalePet._ok_pulse(s, 500.0)) < 1e-9)
check("A10 脉冲中点为峰", abs(W.WhalePet._ok_pulse(s, 500.0 + M.OK_TAP_PULSE_S / 2)
                              - M.OK_TAP_BUMP) < 1e-6)
check("A10 脉冲结束后为 0", W.WhalePet._ok_pulse(s, 500.0 + M.OK_TAP_PULSE_S) == 0.0)

# A11 _ok_animating 覆盖三种动画
s._ok_anim_t0, s._ok_anim_dur = 600.0, M.OK_POP_IN_S
check("A11 切换动画期间视为 busy", W.WhalePet._ok_animating(s, 600.1))
s._ok_anim_t0, s._ok_anim_dur = 0.0, 0.0
s._ok_pulse_t0 = 700.0
check("A11 点击脉冲期间视为 busy", W.WhalePet._ok_animating(s, 700.05))
s._ok_pulse_t0 = 0.0
s._ok_release_at = 800.0
check("A11 完成保持期间视为 busy", W.WhalePet._ok_animating(s, 799.0))
s._ok_release_at = 0.0
check("A11 全静止时不 busy", not W.WhalePet._ok_animating(s, 900.0))


# A12 布局回归：每种方案、各级缩放下，「图形 / 文案 / 进度点」互不重叠且不越椭圆内接区
print("\n[A12] OK 态布局（图形 / 文案 / 进度点）")
from math import sqrt                                 # noqa: E402


def _cap_half_width(cap_y, cap_font, bub_w, sc):
    """文案半宽估算：取各分支文案里**最宽**的一条（CJK 按 1em/字、ASCII 按 0.55em/字），
    再受 ctext 的 maxw 钳制。按最宽估才不会漏掉某个分支的越界。"""
    n = M.OK_CLICKS_NEEDED
    cands = ["任务完成 · 点我恢复", f"任务完成 · 点我 {n} 次",
             f"还差 {max(0, n - 1)} 次回到数据…", "完成 · 正在恢复…"]
    ems = max(sum(0.55 if ord(c) < 128 else 1.0 for c in t) for t in cands)
    return min(ems * cap_font / 2.0, (bub_w - 56 * sc) / 2.0)


for style in sorted(W.OK_STYLES):
    for sc in (0.6, 0.8, 1.0, 1.5, 2.0, 2.5):
        lay = {"sc": sc, "W": 300 * sc, "bub_h": 128 * sc}
        sp = W.ok_spec(lay, style)
        by, bh = sp["bub_y"], sp["bub_h"]
        a, b = sp["bub_w"] / 2.0, bh / 2.0            # 椭圆半轴
        cy_ell = by + bh / 2.0

        def half_w(y):
            dy = (y - cy_ell) / b
            if abs(dy) >= 1.0:
                return 0.0
            return a * sqrt(1.0 - dy * dy)

        # 进度点只在阈值 >1 时才真的画（只点 1 下无所谓"进度"）——没画的就不参与验算
        dots_drawn = (sp["dots_cy"] is not None and M.OK_CLICKS_NEEDED > 1)

        # 各段（有的方案没有文案/进度点）按纵向顺序排列
        g_top, g_bot = sp["g_cy"] - sp["g_h"] / 2, sp["g_cy"] + sp["g_h"] / 2
        bands = [("图形", g_top, g_bot)]
        if sp["cap_y"] is not None:
            bands.append(("文案", sp["cap_y"],
                          sp["cap_y"] + sp["cap_font"] * W.OK_CAP_LINE))
        if dots_drawn:
            bands.append(("进度点", sp["dots_cy"] - sp["dot_r"],
                          sp["dots_cy"] + sp["dot_r"]))
        for (n1, _, b1), (n2, t2, _) in zip(bands, bands[1:]):
            check(f"A12 {style} sc={sc} {n1}与{n2}不重叠", b1 < t2,
                  f"{n1}_bot={b1:.1f} {n2}_top={t2:.1f}")
        check(f"A12 {style} sc={sc} 内容都在气泡内",
              bands[0][1] > by and bands[-1][2] < by + bh,
              f"top={bands[0][1]:.1f} bot={bands[-1][2]:.1f} bub={by:.1f}..{by + bh:.1f}")

        # 横向：图形取**最宽处**（垂直中心行）——图形的顶/底是头环顶部与脚尖，
        # 宽度趋近 0，拿那里去要求"整幅宽度放得下"会得到假失败；
        # 文案与进度点则按各自所在行验算。
        ys = [sp["g_cy"]]
        if sp["cap_y"] is not None:
            ys.append(sp["cap_y"])
        if dots_drawn:
            ys += [sp["dots_cy"] - sp["dot_r"], sp["dots_cy"] + sp["dot_r"]]
        worst = min(half_w(y) for y in ys)
        need = sp["g_w"] / 2.0
        if sp["cap_y"] is not None:
            need = max(need, _cap_half_width(sp["cap_y"], sp["cap_font"], sp["bub_w"], sc))
        if dots_drawn:
            need = max(need, sp["dot_gap"] * (M.OK_CLICKS_NEEDED - 1) / 2.0 + sp["dot_r"])
        check(f"A12 {style} sc={sc} 内容不越椭圆曲线", need < worst,
              f"need={need:.1f} avail={worst:.1f}")

        # 主视觉够不够"整颗气泡"：图形高至少要占到气泡高的 55%
        check(f"A12 {style} 图形够大（占气泡高 ≥55%）", sp["g_h"] >= bh * 0.55,
              f"glyph_h={sp['g_h']:.0f} bub_h={bh:.0f}")

# A13 完成态文案不含拟人台词（古见几乎不说话，人设红线）
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "wb_whale_win.py"), encoding="utf-8").read()
for bad in ("好，回来了", "太棒了", "完成啦"):
    check(f"A13 完成文案不含拟人台词「{bad}」", bad not in src)


# A14-A18 OK 态「自动消失」：对话完成后用户回到对话窗口 + 窗口重新获得焦点 → 平滑淡出
print("\n[A14-A18] OK 自动消失（焦点回归触发）")


def enter_ok(stub):
    """摆到 OK 完成态（前置：先有一轮运行中会话，再结束）。"""
    stub.active = [{"title": "对话A"}]
    stub._sync_bubble_mode()
    stub.active = []
    stub._sync_bubble_mode()
    return stub


# A14 回到对话窗口（焦点 False→True 上升沿）→ 自动消失 + 走退场动画
s14 = Stub()
enter_ok(s14)
s14._ok_entered_at = 0.0             # 起点时刻固定，便于算"已显示时长"
s14._ok_fg_at_entry = False
s14._wb_fg_prev = False
s14._wb_focused = True               # 用户切回 WorkBuddy
W.WhalePet._ok_focus_check(s14, 10.0)
check("A14 回到对话窗口(上升沿)→自动消失",
      s14._bub_mode == W.BUBBLE_DEFAULT and s14._ok_auto_dismissed,
      f"mode={s14._bub_mode} dismissed={s14._ok_auto_dismissed}")
check("A14 自动消失上报事件",
      ("ok_auto_dismiss",) in [e[0] for e in s14.events])
check("A14 自动消失走平滑淡出动画",
      W.WhalePet._ok_animating(s14, 10.0)
      and s14._ok_anim_dur == M.OK_POP_OUT_S,
      f"anim_dur={s14._ok_anim_dur}")

# A15 出现时对话窗口本就在前台（用户没离开）→ 超宽限后自动收起
s15 = Stub()
enter_ok(s15)
s15._ok_entered_at = 0.0
s15._ok_fg_at_entry = True
s15._wb_fg_prev = True
s15._wb_focused = True
W.WhalePet._ok_focus_check(s15, 0.5)   # 宽限期内
check("A15 宽限期内不消失", s15._bub_mode == W.BUBBLE_OK, f"mode={s15._bub_mode}")
W.WhalePet._ok_focus_check(s15, M.OK_AUTODISMISS_FG_GRACE_S + 0.5)  # 超宽限
check("A15 一直前台超宽限→自动消失",
      s15._bub_mode == W.BUBBLE_DEFAULT and s15._ok_auto_dismissed,
      f"mode={s15._bub_mode}")

# A16 最短显示时长保护：弹入瞬间焦点回归也不触发（防一闪即逝）。
# 真实场景：OK 出现时焦点还没回来（False）→ 之后才 alt-tab 回来（True，上升沿）。
s16 = Stub()
enter_ok(s16)
s16._ok_entered_at = 100.0
s16._ok_fg_at_entry = False
s16._wb_fg_prev = False
s16._wb_focused = False                     # 用户还没回来
W.WhalePet._ok_focus_check(s16, 100.0 + M.OK_AUTODISMISS_FOCUS_MIN_VISIBLE_S - 0.1)
check("A16 显示不足最短时长不触发",
      s16._bub_mode == W.BUBBLE_OK, f"mode={s16._bub_mode}")
s16._wb_focused = True                      # 现在用户切回 WorkBuddy（上升沿）
W.WhalePet._ok_focus_check(s16, 100.0 + M.OK_AUTODISMISS_FOCUS_MIN_VISIBLE_S + 0.2)
check("A16 超过最短时长+上升沿后触发",
      s16._bub_mode == W.BUBBLE_DEFAULT, f"mode={s16._bub_mode}")

# A17 焦点始终未回归 → 不自动消失（保持"任务完成"提示，直到用户点击/回窗口）
s17 = Stub()
enter_ok(s17)
s17._ok_entered_at = 0.0
s17._ok_fg_at_entry = False
s17._wb_fg_prev = False
s17._wb_focused = False
W.WhalePet._ok_focus_check(s17, 100.0)
check("A17 焦点未回归不消失", s17._bub_mode == W.BUBBLE_OK, f"mode={s17._bub_mode}")

# A18 保留再显示机制：自动消失后，下一轮对话再完成（>0→0）应重新进 OK
s18 = Stub()
enter_ok(s18)
s18._ok_entered_at = 0.0
s18._ok_fg_at_entry = False
s18._wb_fg_prev = False
s18._wb_focused = True
W.WhalePet._ok_focus_check(s18, 10.0)   # 自动消失
check("A18 前置：已被焦点自动收起", s18._bub_mode == W.BUBBLE_DEFAULT and s18._ok_auto_dismissed)
s18.active = [{"title": "新对话"}]
s18._sync_bubble_mode()                  # 1→0 之外：新任务开始（prev 0→1），不应误触发
check("A18 新对话开始不误触发", s18._bub_mode == W.BUBBLE_DEFAULT, f"mode={s18._bub_mode}")
s18.active = []
s18._sync_bubble_mode()                  # 1→0：新任务完成 → OK 重新出现
check("A18 新任务完成→OK 重新显示（保留再显示机制）",
      s18._bub_mode == W.BUBBLE_OK, f"mode={s18._bub_mode}")


# ============================================================
# Part B：真实渲染快照（读分层窗口 DIB）
# ============================================================
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))    # tools/ 自身
from _shotutil import surf_to_image, label_font as _label_font   # noqa: E402


def shot(app, label, mode, anim_t=None, clicks=0, pulse_t=None):
    """把 app 摆到指定形态 → draw() → 读回气泡区域。"""
    app._bub_mode = mode
    app._ok_clicks = clicks
    app._ok_release_at = 0.0
    now = time.time()
    app._ok_pulse_t0 = pulse_t if pulse_t else 0.0
    if anim_t is None:
        app._ok_anim_t0, app._ok_anim_dur = 0.0, 0.0
    else:
        app._ok_anim_t0, app._ok_anim_dur = now - anim_t, M.OK_POP_IN_S
        if mode != W.BUBBLE_OK:
            app._ok_anim_dur = M.OK_POP_OUT_S
    app._ok_anim_from = M.OK_GLYPH_MIN_SCALE if mode == W.BUBBLE_OK else 1.0
    app.draw()
    img = surf_to_image(app.surf)
    lay = app._layout()
    # ⚠️ 2026-09-27：气泡改为「锚定立绘实际头顶」（WhalePet._bubble_box），
    #    不再固定贴窗口顶部 —— 快照必须按气泡椭圆的实际位置裁，
    #    否则 idle/stone 等态下气泡下移，图会被从中间切掉。
    _bb = app._bubble_box(lay)
    top = max(0, int(_bb[1] - 8))
    return img.crop((0, top, lay["W"], top + int(_bb[3] + 16))), label


def render_sheet(out_path):
    print("\n[B] 真实渲染快照")
    app = W.WhalePet(run_seconds=0)
    try:
        if not app._ok_glyph_cache:
            check("B0 OK 字形资源已加载", False,
                  f"未加载到 {os.path.join(W.ASSETS_DIR, 'ok_glyph.png')}")
            return
        check("B0 OK 字形资源已加载并预缩放", bool(app._ok_glyph_cache),
              f"cache={app._ok_glyph_cache[1:] if app._ok_glyph_cache else None}")
        now = time.time()
        frames = [
            shot(app, "数据态（对照）", W.BUBBLE_DEFAULT),
            shot(app, "OK 弹入 t=0.10", W.BUBBLE_OK, anim_t=M.OK_POP_IN_S * 0.10),
            shot(app, "OK 弹入 t=0.35", W.BUBBLE_OK, anim_t=M.OK_POP_IN_S * 0.35),
            shot(app, "OK 稳定（待点击）", W.BUBBLE_OK),
        ]
        # 点击过程逐次出一帧（阈值改了这里自动跟着变）
        for i in range(1, M.OK_CLICKS_NEEDED + 1):
            lab = f"点击 {i} 次（完成保持）" if i == M.OK_CLICKS_NEEDED else f"点击 {i} 次"
            frames.append(shot(app, lab, W.BUBBLE_OK, clicks=i))
        frames += [
            # 点击脉冲单独出一帧（叠在稳定态上）
            shot(app, "点击脉冲峰值", W.BUBBLE_OK,
                 pulse_t=now - M.OK_TAP_PULSE_S / 2),
            # 自动消失（焦点回归）与点击恢复走同一条平滑淡出路径
            shot(app, "自动消失淡出（回到对话窗口）", W.BUBBLE_DEFAULT,
                 anim_t=M.OK_POP_OUT_S * 0.30),
            shot(app, "退出动画 t=0.15（点击恢复同款）", W.BUBBLE_DEFAULT,
                 anim_t=M.OK_POP_OUT_S * 0.5),
        ]

        cols, scale = 2, 1.6
        cw = int(app._layout()["W"] * scale)
        # 与 shot() 的裁剪高度一致：气泡高 + 上下各 8px 余量
        chh = int((app._layout()["bub_h"] + 16) * scale)
        rows = (len(frames) + cols - 1) // cols
        fnt = _label_font(17)
        sheet = Image.new("RGB", (cols * cw + (cols + 1) * 10,
                                  rows * (chh + 30) + 10), (38, 34, 48))
        d = ImageDraw.Draw(sheet)
        for i, (im, label) in enumerate(frames):
            r, c = divmod(i, cols)
            x = 10 + c * (cw + 10)
            y = 10 + r * (chh + 30)
            bg = Image.new("RGBA", im.size, (247, 244, 238, 255))
            bg.alpha_composite(im)
            sheet.paste(bg.convert("RGB").resize((cw, chh), Image.LANCZOS), (x, y))
            d.text((x + 2, y + chh + 5), label, fill=(240, 236, 250), font=fnt)
        sheet.save(out_path)
        print(f"  快照已保存：{out_path}  ({sheet.width}x{sheet.height})")
    finally:
        try:
            W._user32.DestroyWindow(app.hwnd)
        except Exception:
            pass
        app.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "_bubble_ok_sheet.png"))
    a = ap.parse_args()
    if os.name != "nt":
        print("仅 Windows 可跑（需要 GDI+ 分层窗口）")
        return 2
    render_sheet(a.png)
    print(f"\n=== 总结 ===\nPASS: {len(PASSED)}\nFAIL: {len(FAILED)}")
    for f in FAILED:
        print(f"  - {f}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
