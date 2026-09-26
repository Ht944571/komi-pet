# -*- coding: utf-8 -*-
r"""古见同学桌宠 · 动效设计系统（设计令牌 / 缓动 / 动画曲线 / 情绪状态常量）。

把所有"看起来怎么样"的数字集中在这里，主文件 wb_whale_win.py 只引用。
新增任何动效参数都必须在这里出现，不允许在主文件里散落魔法数。

约束：纯标准库，无外部依赖；可以独立 import 测试。
"""
import math
import random

# ============================================================
# 1. 时间常量（秒 / 毫秒）——  全部量化到 15fps（66ms）的整数倍
# ============================================================

# --- 周期类动效 ---
BREATH_PERIOD_S = 3.198               # 呼吸总周期（2 * 1.066 帧）
BREATH_INHALE_S = 1.398               # 吸气段（21 帧）
BREATH_EXHALE_S = 1.800               # 呼气段（27 帧）—— 非对称

TAIL_PERIOD_S = 1.602                 # 尾鳍摆动周期（与呼吸 1:2 错开）
FLOAT_PERIOD_S = 6.006                # 漂浮周期

BLINK_MIN_S = 3.0                      # 眨眼间隔下限（生理规律：常态 3~8s）
BLINK_MAX_S = 8.0                      # 眨眼间隔上限（禁止固定周期）
BLINK_MEAN_S = 3.6                     # 间隔均值 ≈ 16 次/分（友好感最佳 18 次/分附近）
BLINK_SIGMA_S = 1.1                    # 间隔标准差（完全随机，禁止节拍器）
# 注视/专注状态：间隔拉长到 8~12s（慢而轻的「注视式眨眼」）
BLINK_ATTENTION_MEAN_S = 9.5
BLINK_ATTENTION_MIN_S = 8.0
BLINK_ATTENTION_MAX_S = 12.0
BLINK_NIGHT_MEAN_FACTOR = 1.9          # 夜间：眨眼频率降低（间隔拉长）
BLINK_NIGHT_MAX_EXTRA = 4.0            # 夜间上限额外放宽（秒）
BLINK_ATTENTION_DUR = 1.4              # 注视式慢眨：单次时长 ×1.4（慢而轻）
# 快闭慢睁（单次 150~250ms）：闭眼快（~30%）/ 全闭仅 1 帧 / 睁眼慢（~60%）
BLINK_CLOSE_S = 0.065                  # 闭眼段（ease-in，快收）
BLINK_HOLD_S = 0.030                   # 全闭保持 ≈ 1 帧
BLINK_OPEN_S = 0.135                   # 睁眼段（ease-out，慢睁）
BLINK_TOTAL_S = BLINK_CLOSE_S + BLINK_HOLD_S + BLINK_OPEN_S  # ≈ 230ms
BLINK_DOUBLE_PROB = 0.12               # 随机双连眨 10~15%
BLINK_DOUBLE_GAP_S = 0.18              # 双连眨间隔 <300ms
BLINK_STARTLE_GAP_S = 0.05             # 受惊连眨：主眨结束 → 第二眨（start-to-start <300ms）
BLINK_LOWER_LID = 0.10                 # 下眼睑辅助上抬 10%（上睑主导，下睑轻辅助）
BLINK_NOD_PX = 1.5                     # 眨眼伴随的头部微点（1~2px，非孤立运动）
BLINK_NIGHT_DROOP = 0.30               # 夜间半眯眼：常驻眼睑覆盖 30%（慵懒态）
BLINK_BUFFER = 0.10                    # 参数两端缓冲（Live2D 规范：防极限位置抖动）

# --- 状态切换过渡（对齐参考视频的"交叉溶解"，2026-09-26 v3 补）---
# 参考视频里姿态切换是交叉溶解而非硬切；旧设计稿也要求"2 帧渐入/渐出"，
# 但当时以为「GDI 的 AlphaBlend 没有全局 alpha」而放弃 —— 那个结论不准确
# （SourceConstantAlpha 就在 BLENDFUNCTION 第 3 字节，见 Surface.blit_a）。
# 0.14s ≈ 2 帧 @15fps。
STATE_FADE_S = 0.14
# 状态过渡"交叉溶解"的门槛：两张立绘的**局部最大差异**（3×3 灰度块均差）超过它
# 就**硬切**，不做溶解。数据依据（2026-09-26 实测这套 8 态 Q 版）：
#   idle↔idle = 0.0（无变化）；idle↔surprise = 78.8；idle↔pout = 155；
#   idle↔happy/joy/blush/stone = 203~215
# 即：**任何真实的点击换态都远超阈值** → 全部硬切，不会出现"两张脸叠着"的双重曝光。
# 保留这套机制是为了将来出现"姿态近乎相同"的新态时，溶解能自动生效。
POSE_FADE_MAX_DIFF = 60.0

# --- 状态调制（眨眼跟着角色状态走，不是常数）---
# 工作中（会话 running）：眨眼率降 30–50%——"专注看着你的进度"。
# 间隔整体放大 1.6 倍 ≈ 率降 37.5%（落在区间中段）。
BLINK_WORK_RATE_SCALE = 1.6
# 长时间无人：偶发慢眨（时长 ×1.5–2）出"犯困感"。慢眨必定全闭——深而慢才像犯困。
BLINK_SLOW_PROB = 0.45
BLINK_SLOW_SCALE_MIN = 1.5
BLINK_SLOW_SCALE_MAX = 2.0

# --- 部分眨眼 ---
# 现实中 60–70% 的眨眼只闭到 40–70%，全闭反而少；"太整"正是眨眼被意识捕捉的原因之一。
# 部分眨眼上下睑不接触 → 没有保持段，也没有睫毛接触线（渲染层按 ratio 自行取舍）。
BLINK_PARTIAL_PROB = 0.65             # 部分眨眼占比
BLINK_PARTIAL_DEPTH_MIN = 0.40        # 闭合深度下限（盖住眼高的 40%）
BLINK_PARTIAL_DEPTH_MAX = 0.70        # 闭合深度上限（盖住眼高的 70%）

# --- 跟随模式（桌宠跨Agent体验设计.md P1）——时长/窗口/尺寸令牌 ---
FOLLOW_DEBOUNCE_S = 0.40        # 去抖窗口 300–500ms：Alt+Tab 快速扫过只认最终停留（§7#2）
FOLLOW_RETURN_S = 5.0           # 同一 agent 5s 内再聚焦 → 退化为纯颜色渐变（§3.3 高频往返）
FOLLOW_FADE_S = 0.24            # 跟随过渡时长 180–300ms：非用户发起，长了是噪音（§3.2）
FOLLOW_POLL_EVERY = 4           # 动画帧分频：66ms×4≈264ms 轮询兜底（钩子不可用时）
FOLLOW_BADGE_R_PX = 7.0         # 聚焦徽章半径（scale=1.0；领结位的身份色圆点）
FOLLOW_BADGE_ALPHA = 0.9
FOLLOW_UNKNOWN_ALPHA = 0.45     # 未知态：半透明 + 灰（§7#9 诚实 > 好看）

# --- 接续（Handoff，跟随 P4）——检测窗口 / 角标展示上限 / 角标尺寸 ---
HANDOFF_WINDOW_S = 120          # 最近一轮结束 2 分钟内且无后续活动 → "似乎未结束"（§5）
HANDOFF_SHOW_MAX_S = 600        # 角标最长展示 10 分钟：过时默默消失，无打扰
HANDOFF_DOT_R_PX = 2.6          # 角标点半径（scale=1.0；跟随徽章右上角的樱花点）
HANDOFF_FILENAME = "handoff.md"  # 注入降级第二档：接续摘要写到 agent 工作目录

# --- P3 全局热键（手动聚焦轮换）：Ctrl+Alt+F9（组合选择也是令牌，RegisterHotKey 消费）---
FOLLOW_HOTKEY_VK = 0x79         # F9 虚键码
FOLLOW_HOTKEY_MODS = 0x0003     # MOD_ALT(0x1) | MOD_CONTROL(0x2)

# --- 盖板渲染（渲染几何令牌，_draw_blink 消费）---
# 盖板静止位 = 眼缝框顶向下 28%：立绘自带的深色睫线/发影带保持可见、充当"闭眼
# 的上睑"，盖板从其下缘钻出——否则盖板盖进刘海，发丝被"擦掉"（真机 4x 验证）。
BLINK_LID_REST = 0.28
# 盖板色向眼线色收 10%：lid_skin 实测自眼底亮部，直接平涂比发影下的脸颊亮一档
# （"浅色胶带"感），收一档融进阴影。
BLINK_LID_SHADE = 0.10

# --- 微交互时长 ---
HOVER_ENTER_S = 0.198                 # hover 进场（3 帧）
HOVER_EXIT_S = 0.264                  # hover 退场（4 帧）

CLICK_SQUASH_S = 0.132                # 按压压缩（2 帧）
CLICK_RECOVER_S = 0.330               # 回弹过冲（5 帧）

LONGPRESS_MS = 500                    # 长按阈值
LONGPRESS_RELEASE_S = 0.330           # 长按释放反馈

DRAG_RECOVER_S = 0.264                # 拖拽松手回正（4 帧）

# --- 状态切换 ---
STATE_ENTER_S = 0.594                 # 新状态弹入（9 帧）
STATE_EXIT_S = 0.198                  # 旧状态淡出（3 帧）

# --- 气泡 OK 完成态（对话任务结束时切换，点 1 下即回到数据态）---
# CLICKS_NEEDED = 1：点一下就走。进度点仅在阈值 >1 时才画（1 下没有"进度"可言）。
OK_CLICKS_NEEDED = 1                  # 恢复数据态所需的点击次数
OK_CLICK_WINDOW_S = 2.0               # 连击窗口：相邻两次点击超过它 → 计数归零重来
OK_POP_IN_S = 0.594                   # 进入 OK：字形弹入（复用 9 帧）
OK_POP_OUT_S = 0.330                  # 退出 OK：回落到数据态（5 帧）
OK_TAP_PULSE_S = 0.264                # 每次点击的脉冲缩放（4 帧）
OK_RELEASE_HOLD_S = 0.462             # 点够后到真正切走的短暂保持（7 帧，让这一下被看见）
OK_GLYPH_MIN_SCALE = 0.55             # 弹入起点缩放（0.55 → 1.0 带过冲）
OK_TAP_BUMP = 0.14                    # 点击脉冲的额外缩放幅度

# --- OK 完成态「自动消失」（对话完成后用户回到对话窗口 + 窗口重新获得焦点 → 平滑淡出）---
# 触发口径：任务完成（active: >0 → 0）进 OK 后，用户把 WorkBuddy 对话窗口重新拉回前台，
#   视为"已读"，OK 泡泡平滑淡出（复用 OK_POP_OUT_S 退场动画，orange→blue + 字形收小）。
# 保留再显示机制：自动消失只是隐藏，不消费完成信号；下一轮对话再完成（>0 → 0）会重新进 OK。
OK_AUTODISMISS_ON = True               # 总开关（关掉则只靠点击恢复，行为退回上一版）
OK_AUTODISMISS_FOCUS_MIN_VISIBLE_S = 0.6   # OK 至少已显示这么久，才接受"焦点回归"触发（防弹入瞬间被误收）
OK_AUTODISMISS_FG_GRACE_S = 3.0        # 若 OK 出现时对话窗口本就在前台（用户没离开），宽限这么久后再收起
OK_WB_HWND_REFRESH_S = 10.0            # 重新探测 WorkBuddy 主窗口句柄的周期（句柄失效/重建时自愈）

# --- 联动关闭（WorkBuddy 应用退出 → 桌宠窗口与进程一并退出）---
# 触发：WorkBuddy 进程结束（正常退出 / 异常崩溃 / 托盘退出 都表现为进程结束）。
# 三级信号，从精确到兜底：进程句柄 WaitForSingleObject → 窗口探测 → 进程快照。
# 兜底：三级都"无法判定"时**不关**（宁可留着，也不误杀）；守望进程再做二次兜底。
LINKED_CLOSE_ON = True                 # 总开关（关掉则桌宠不随 WorkBuddy 退出）
LINKED_CLOSE_GRACE_S = 3.0             # 首次判定"已消失"后，宽限这么久再关（容忍瞬时抖动/进程切换）
LINKED_CLOSE_CONFIRM_CHECKS = 2        # 需连续 N 次判定"已消失"才确认（配合 1s 的 tick 节奏）
LINKED_CLOSE_PROBE_S = 2.0             # 拿不到进程句柄时，退化为窗口/进程探测的最小间隔（省开销）

# --- 配件净空（scale=1.0 时的像素值）---
# 为什么需要：Q 版立绘是"大头娃娃"，头发顶到图片最上沿，**头顶上方没有空间**，
# 贝雷帽的帽身（约占帽高的 83%）只能靠额外净空来放 —— 否则会被窗口裁掉。
# 净空插在「气泡区」与「立绘区」之间；气泡本来就比头高出很多，塞进那段间隙，
# 视觉上只是气泡到头顶的距离略增，不会显得空。
# 分层窗口按 alpha 命中测试，多出来的透明区域不挡鼠标，功能上零成本。
ACC_HEADROOM = 58.0        # 贝雷帽放大到「略小于头宽」后重算：
                           # 原来 48 是按小帽子定的，会让帽顶顶出窗口上沿

# 配件入退场时长（秒）。比切换 agent 的动效更慢一档：配件是"生活状态"，
# 忽然冒出/消失会显得廉价；但要可打断——连续变化时直接从当前进度切到新目标。
ACC_ENTER_S = 0.42
ACC_EXIT_S = 0.30

# ============================================================
# 2. 幅度常量（scale=1.0 时的像素值）
# ============================================================

BREATH_AMP_PX = 2.0                   # 呼吸上下幅度
BREATH_SCALE_AMP = 0.015              # 呼吸纵向缩放（轻微）
TAIL_AMP_PX = 3.0                     # 尾鳍末端位移
FLOAT_AMP_PX = 3.0                     # 漂浮幅度

GAZE_MAX_PX = 1.5                     # 头部朝光标偏移
GAZE_LERP = 0.18                      # 每帧向目标插值比例

DRAG_TILT_MAX_PX = 6.0                # 拖拽倾斜上限（提案 ≤8° 对应像素）
DRAG_TILT_HEAD_RATIO = 0.55           # 头部剪切在身体上方的占比

HOVER_SCALE = 1.03                    # 悬停放大（提案 §3：1.03）
CLICK_SQUASH_Y = 0.92                 # 按压压缩到底的纵向比例（下压 8%）

SHADOW_SCALE_MIN = 0.90               # 软阴影最小缩放（漂浮最高点）
SHADOW_SCALE_MAX = 1.05               # 软阴影最大缩放（漂浮最低点）

# ============================================================
# 3. 克制上限（绝对硬上限，任何情况下都不可突破）
# ============================================================

MAX_PARTICLE_CONCURRENCY = 12         # 粒子并发上限（原实现 24，已下调）
PARTICLE_CULL_OLDEST = 4               # 超出上限时一次性削减 N 个最老的

DISPLACEMENT_RATIO_MAX = 0.04         # 位移 / 角色身高（4%）
SCALE_MAX = 1.06                      # 缩放上限
LOOP_PERIOD_MIN_S = 2.5               # 循环周期下限
SINGLE_REACTION_MAX_S = 0.6           # 单次反馈时长上限
STATE_THROTTLE_S = 1.5                # 同一状态重复触发的节流

IDLE_DOWNGRADE_S = 30.0               # N 秒无交互后降频（呼吸+眨眼）

# ============================================================
# 4. 降级档位
# ============================================================

QUALITY_FULL = "full"                 # 全量
QUALITY_LITE = "lite"                 # 精简（去粒子去漂浮）
QUALITY_OFF = "off"                   # 关闭（纯静态表情帧切换）

QUALITY_AUTO_DEGRADE_MS = 8.0         # 单帧绘制 >8 ms 触发降级
QUALITY_AUTO_RECOVERY_S = 30.0        # 降级后多久尝试恢复
QUALITY_REPEAT_DEGRADE_FRAMES = 3     # 连续 N 帧超阈值才触发

# ============================================================
# 5. 配色常量（ARGB）
# ============================================================

COLOR_BUBBLE_OUTLINE = 0xCC5B8DEF     # 气泡描淡
COLOR_BUBBLE_FILL = 0x335B8DEF
COLOR_BUBBLE_TEXT = 0xFF1F2A4D
COLOR_PET_OUTLINE = 0xCC2E4A7D        # 角色描边（提案：不纯黑）
COLOR_BLINK_BG = 0xFF1A2042           # 闭眼眼睑
COLOR_BLINK_LID = 0xFF0F1626          # 眼睑内沿
COLOR_BLUSH_SOFT = 0x33E89AAE         # 常态腮红·樱花粉（透明度 0.20，古见主题 --sakura）
COLOR_BLUSH_LOUD = 0x73E89AAE         # 害羞态腮红·樱花粉（透明度 0.45）
COLOR_SHADOW = 0x5539406B             # 软阴影（制服蓝淡）
COLOR_HAPPY = 0xFFE89AAE              # 成功态强调·樱花粉（古见主题）
COLOR_FAIL = 0xFF8FA3BF               # 失败态（降饱和，不是红）
COLOR_HIGHLIGHT = 0x1FFFFFFF          # 身体高光（白色 12% 透明）
COLOR_HEART = 0xFFE89AAE              # 爱心·樱花粉
COLOR_DROP = 0xCCE89AAE               # 石化爆发粒子·樱花粉淡
COLOR_BUBBLE = 0x307A86C9             # 自主吐泡·淡制服蓝
COLOR_OK = 0xFFED6D2E                 # OK 完成态代表橙（取自参考字形 #ED6D2E）
COLOR_OK_SOFT = 0x2EED6D2E            # OK 完成态气泡淡填充（透明度 0.18）
COLOR_OK_SPARK = 0xFFF5A65B           # OK 弹入迸发粒子·浅橙

# ============================================================
# 6. 情绪状态
# ============================================================

EMOTION_NEUTRAL = "neutral"
EMOTION_SUCCESS = "success"
EMOTION_FAIL = "fail"
EMOTION_LOADING = "loading"
EMOTION_EMPTY = "empty"
EMOTION_WELCOME = "welcome"

# 各状态持续时长（s）。超过自动回落 neutral
EMOTION_DURATION = {
    EMOTION_SUCCESS: 2.5,
    EMOTION_FAIL: 3.5,
    EMOTION_LOADING: 0.0,             # 持续态（直到外部取消）
    EMOTION_EMPTY: 0.0,               # 持续态
    EMOTION_WELCOME: 4.0,
}

# 各状态的身体姿态偏置（px，正=上抬，负=下沉）
EMOTION_RISE_PX = {
    EMOTION_SUCCESS: 4.0,
    EMOTION_FAIL: -3.0,
    EMOTION_WELCOME: 0.0,             # 由入场动画控制，不静态偏置
}

# 各状态是否要禁循环动效（避免冲突）
EMOTION_SUPPRESS_LOOP = {
    EMOTION_LOADING: True,            # loading 期间不开漂浮/漂移
}

# ============================================================
# 7. 缓动函数（t ∈ [0, 1]）
# ============================================================

def ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def ease_in_quad(t):
    return t * t


def ease_out_quad(t):
    return 1 - (1 - t) ** 2


def ease_in_out_sine(t):
    return -(math.cos(math.pi * t) - 1) / 2


def ease_in_quart(t):
    return t * t * t * t


def ease_out_back(t, c1=1.70158):
    return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ease_out_overshoot(t, overshoot=0.02):
    """接近 1 的回弹过冲（不超过 SCALE_MAX）"""
    return 1.0 + overshoot * math.sin(t * math.pi)


def clamp01(v):
    if v < 0:
        return 0.0
    if v > 1:
        return 1.0
    return v


# ============================================================
# 8. 周期性曲线（返回位移 px）
# ============================================================

def breath_offset(now, phase):
    """非对称呼吸曲线：吸 1.4s / 呼 1.8s，0→AMP→0 平滑过渡。"""
    t = (now + phase) % BREATH_PERIOD_S
    if t < BREATH_INHALE_S:
        u = t / BREATH_INHALE_S
        return BREATH_AMP_PX * (1 - math.cos(u * math.pi)) / 2
    u = (t - BREATH_INHALE_S) / BREATH_EXHALE_S
    return BREATH_AMP_PX * (math.cos(u * math.pi) + 1) / 2


def tail_offset(now, phase):
    """尾鳍摆动（正弦）。"""
    return TAIL_AMP_PX * math.sin(2 * math.pi * now / TAIL_PERIOD_S + phase)


def float_offset(now, phase):
    """上下漂浮（正弦）。"""
    return FLOAT_AMP_PX * math.sin(2 * math.pi * now / FLOAT_PERIOD_S + phase)


def shadow_scale(now, phase):
    """软阴影随漂浮缩放：漂浮越高（接近顶端），阴影越小。"""
    f = math.sin(2 * math.pi * now / FLOAT_PERIOD_S + phase)
    # f ∈ [-1, 1]：f=1（漂浮最高）→ 阴影最小
    return (SHADOW_SCALE_MIN + SHADOW_SCALE_MAX) / 2 \
        - f * (SHADOW_SCALE_MAX - SHADOW_SCALE_MIN) / 2


# ============================================================
# 9. 眨眼状态机
# ============================================================

class BlinkScheduler:
    """眨眼状态机 v2 —— 三层最高标准的 2D 落地（节奏/时序/交互联动）。

    时间轴（眨眼开始 = blink_t0；快闭慢睁：闭 ~28% / 全闭 1 帧 / 睁 ~59%）：
      [t0, t0+close)            闭眼段：ratio 1 → (1-深度)（ease-in 快收）
      [t0+close, +hold)         保持段：全闭 ≈ 1 帧（部分眨眼无此段）
      [t0+.., t0+total)         睁眼段：(1-深度) → 1（ease-out 慢睁）

    节奏（完全随机，禁止固定周期）：
      · 常态 3~8s（均值 3.6s ≈ 16 次/分）；注视/工作中 8~12s；夜间再拉长
    交互联动（调用方每帧更新标志）：
      · attention=True  鼠标悬停 → 注视式慢眨（间隔 8~12s、时长 ×1.4）
      · busy=True       工作中 → 同注视带（专注时眨眼少）
      · night=True      夜间 → 频率降低 + 渲染层半眯眼（droop 由渲染层画）
      · startle()       受惊连眨：立即双连眨（点击反馈）
      · blink_now()     状态回归待机 → 先自然眨一次
    头部微点：nod(now) 返回 0~1 包络，渲染层换算成 1~2px 头部下压——
    眨眼不是孤立运动（通用核心标准 2）。
    """

    def __init__(self, now, quality=QUALITY_FULL):
        self.quality = quality
        self.rate_scale = 1.0        # 兼容保留（工作中的额外放大，一般 1.0）
        self.suppressed = False      # 禁眨（拖拽/被端详/反应动画期间）
        self.idle = False            # 长时间无人（偶发慢眨）
        self.attention = False       # 注视（鼠标悬停/专注）→ 8~12s 慢眨
        self.busy = False            # 工作中 → 同注视带
        self.night = False           # 夜间 → 频率降低
        self._interval_mean = BLINK_MEAN_S
        self._interval_lo = BLINK_MIN_S
        self._interval_hi = BLINK_MAX_S
        self._refresh_interval_band(now)
        self.next_at = now + _next_blink_wait(now, self._interval_mean,
                                              self._interval_lo, self._interval_hi)
        self.blink_t0 = 0.0
        self.blink_total = 0.0
        self._pending_double = False
        self._was_suppressed = False
        self._startle_left = 0       # 受惊连眨：剩余眨数（2 = 双连眨）
        self._startle_next = None    # 受惊第二眨的触发时刻
        # 当前眨眼的分段时长与闭合深度（触发时重采样；默认=基准值）
        self.seg_close = BLINK_CLOSE_S
        self.seg_hold = BLINK_HOLD_S
        self.seg_open = BLINK_OPEN_S
        self.close_depth = 1.0       # 本次眨眼闭到多深（1.0=全闭；部分眨眼 <1）
        self.dur_scale = 1.0         # 注视式慢眨的时长倍率

    def _refresh_interval_band(self, now):
        """按状态选间隔带：注视/工作中 8~12s；常态 3~8s；夜间整体拉长。"""
        if self.attention or self.busy:
            mean, lo, hi = (BLINK_ATTENTION_MEAN_S, BLINK_ATTENTION_MIN_S,
                            BLINK_ATTENTION_MAX_S)
        else:
            mean, lo, hi = BLINK_MEAN_S, BLINK_MIN_S, BLINK_MAX_S
        if self.night:
            mean *= BLINK_NIGHT_MEAN_FACTOR
            hi += BLINK_NIGHT_MAX_EXTRA
        self._interval_mean, self._interval_lo, self._interval_hi = mean, lo, hi

    def is_active(self, now):
        if self.quality == QUALITY_OFF:
            return False
        return now < self.blink_t0 + self.blink_total

    def _begin_blink(self, now, force_full=False):
        """采样本次眨眼的形态（慢眨 / 闭合深度 / 注视式时长），写入分段参数。
        force_full：受惊连眨用——必全闭、不慢化（惊吓反应是快的）。"""
        self.blink_t0 = now
        f = self.dur_scale
        if force_full:
            depth = 1.0
            f = 1.0
        elif self.idle and random.random() < BLINK_SLOW_PROB:
            f = random.uniform(BLINK_SLOW_SCALE_MIN, BLINK_SLOW_SCALE_MAX)
            depth = 1.0
        elif random.random() < BLINK_PARTIAL_PROB:
            depth = random.uniform(BLINK_PARTIAL_DEPTH_MIN, BLINK_PARTIAL_DEPTH_MAX)
        else:
            depth = 1.0
        self.seg_close = BLINK_CLOSE_S * f
        self.seg_hold = BLINK_HOLD_S * f if depth >= 1.0 else 0.0
        self.seg_open = BLINK_OPEN_S * f
        self.close_depth = depth
        self.blink_total = self.seg_close + self.seg_hold + self.seg_open

    def startle(self, now):
        """受惊连眨（交互级 §三.2）：立即双连眨（两次均全闭，间隔 <300ms）。
        与普通双眨的区别：**立即**触发、必定全闭（惊吓反应是快的）。"""
        self._startle_left = 2
        self.blink_t0 = 0.0
        self.blink_total = 0.0
        self.next_at = now

    def blink_now(self, now):
        """状态回归待机 → 先自然眨一次（交互级 §三.3）。"""
        self.next_at = now

    def nod(self, now):
        """眨眼头部微点包络：0~1（渲染层 × BLINK_NOD_PX 换算 1~2px 下压）。"""
        if not self.is_active(now):
            return 0.0
        t = now - self.blink_t0
        total = self.blink_total or 1e-6
        return max(0.0, math.sin(math.pi * min(1.0, t / total)))

    def tick(self, now):
        """每帧调用：推进状态机。"""
        # 眨眼已结束 → 重置（让 ratio 回到 1）
        if self.blink_total > 0 and now >= self.blink_t0 + self.blink_total:
            self.blink_t0 = 0.0
            self.blink_total = 0.0
        self._refresh_interval_band(now)   # 状态带随时刷新（attention/busy/night）
        # 禁眨期：不开新眨眼；进行中的跳到睁开段（从闭眼/保持段直接进入睁眼）
        # （受惊连眨不受禁眨影响——惊吓压不住；等禁眨解除后立即补发）
        if self.suppressed:
            if self.blink_total > 0 and self.blink_phase(now) != "open":
                self.blink_t0 = now - self.seg_close - self.seg_hold
            self._was_suppressed = True
            return
        # 刚解除禁眨：从当下重新调度（受惊连眨若在挂起，下一次调度即发出）
        if self._was_suppressed:
            self._was_suppressed = False
            self._pending_double = False
            self._refresh_interval_band(now)
            self.next_at = now + _next_blink_wait(now, self._interval_mean,
                                                  self._interval_lo, self._interval_hi)
        # 调度下一次眨眼：受惊连眨优先（全闭、紧跟），否则按当前间隔带
        if now >= self.next_at and self.blink_total == 0.0:
            if self._startle_left > 0:
                self._begin_blink(now, force_full=True)
                self._startle_left -= 1
                self.next_at = self.blink_t0 + self.blink_total + BLINK_STARTLE_GAP_S
            else:
                self._begin_blink(now)
                if self._pending_double:
                    self.next_at = self.blink_t0 + self.blink_total + BLINK_DOUBLE_GAP_S
                    self._pending_double = False
                else:
                    self.next_at = self.blink_t0 + self.blink_total \
                        + _next_blink_wait(now, self._interval_mean,
                                           self._interval_lo, self._interval_hi)
                    if _will_double(now):
                        self.next_at = self.blink_t0 + self.blink_total + BLINK_DOUBLE_GAP_S
                        self._pending_double = True

    def eye_opening_ratio(self, now):
        """返回眼睛睁开程度：1=完全睁开，0=完全闭上（全过程缓动曲线）。

        三段：
          闭眼段（[0, close)）：1 → (1-深度)（ease-in 慢→快）
          保持段：[close, close+hold)：固定 0（全闭；部分眨眼无此段）
          睁眼段：[close+hold, total)：(1-深度) → 1（ease-out 快→慢）
        """
        if not self.is_active(now):
            return 1.0
        t = now - self.blink_t0
        if t < self.seg_close:
            # 闭眼段：1 → (1-深度)（ease-in 慢→快）
            u = t / self.seg_close
            return clamp01(1.0 - self.close_depth * ease_in_quad(u))
        if t < self.seg_close + self.seg_hold:
            return 0.0
        # 睁眼段：(1-深度) → 1（ease-out 快→慢）
        u = (t - self.seg_close - self.seg_hold) / self.seg_open
        return clamp01((1.0 - self.close_depth)
                       + self.close_depth * ease_out_cubic(u))

    def blink_phase(self, now):
        """返回当前眨眼阶段标识：'close' / 'hold' / 'open' / 'idle'。"""
        if not self.is_active(now):
            return "idle"
        t = now - self.blink_t0
        if t < self.seg_close:
            return "close"
        if t < self.seg_close + self.seg_hold:
            return "hold"
        return "open"


def _next_blink_wait(now, mean=None, lo=None, hi=None):
    """高斯采样一次眨眼间隔（完全随机，禁止固定周期）。

    带宽三参由调用方按状态带传入（常态 3~8s / 注视·专注 8~12s / 夜间再拉长）。
    """
    import random
    mean = BLINK_MEAN_S if mean is None else mean
    lo = BLINK_MIN_S if lo is None else lo
    hi = BLINK_MAX_S if hi is None else hi
    v = random.gauss(mean, BLINK_SIGMA_S)
    return max(lo, min(hi, v))


def _will_double(now):
    import random
    return random.random() < BLINK_DOUBLE_PROB


# ============================================================
# 10. 缓动驱动小工具（一次性过渡动画）
# ============================================================

class Once:
    """一次性缓动动画驱动：enter→recover→done。"""

    def __init__(self, enter_s, fn):
        self.t0 = 0.0
        self.dur = enter_s
        self.fn = fn            # fn(t01) → value
        self.active = False

    def start(self, now):
        self.t0 = now
        self.dur = self.dur
        self.active = True

    def value(self, now):
        if not self.active:
            return 0.0
        t = (now - self.t0) / self.dur
        if t >= 1.0:
            self.active = False
            return self.fn(1.0)
        return self.fn(clamp01(t))


# ============================================================
# 11. 质量档位工具
# ============================================================

def quality_scale(quality, full=1.0, lite=0.4, off=0.0):
    if quality == QUALITY_OFF:
        return off
    if quality == QUALITY_LITE:
        return lite
    return full


def allow_particles(quality):
    return quality != QUALITY_OFF


def allow_float(quality):
    return quality == QUALITY_FULL


def allow_tail_sway(quality):
    return quality != QUALITY_OFF


# ============================================================
# 12. 命中区域约定（文档性，绘制时不依赖此函数）
# ============================================================

HIT_PAD_PX = 8          # 安全边距：粒子/气泡不得进入
HIT_BUBBLE_TOP_RATIO = 0.55   # 气泡区域占立绘上方的比例（粗略，与 _layout 一致）