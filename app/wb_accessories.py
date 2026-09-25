# -*- coding: utf-8 -*-
"""
wb_accessories.py — 配件层：按「哪些 agent 活跃」决定古见佩戴什么
====================================================================

需求对应
--------
  使用 WorkBuddy        → 身边出现一只小黑猫溜达玩耍
  使用 ZCode            → 自己戴上复古小纽扣贝雷帽
  使用 DeepSeek Harness → 抱着鲸鱼玩偶
  多个 agent 同时使用   → 上述配件**同时**佩戴

三条设计原则（照 docs 里的方案落地，别走样）
--------------------------------------------
1. **声明式**：「哪个 agent 配什么」写在 `assets/_acc_persona.json`，
   不写进代码。彻底避免 `if agent == "zcode"` 这类硬编码——新增 agent 只加一行 json。
2. **槽位化**：head / arms / companion 三个槽位互不冲突，
   于是「多 agent 同时活跃 → 配件并集」是自然结果，不需要任何特判。
3. **几何与绘图分离**：`compute_placement()` 只算坐标、不依赖任何绘图后端。
   桌宠（GDI+ Surface）与预览工具（PIL）**共用同一份定位逻辑**，杜绝两处漂移
   ——这是本项目反复吃过的亏（同一套几何写两遍，改一处漏一处）。

本模块只消费动效令牌，**不定义时长**——时长仍归 wb_motion.py（单一事实来源）。
"""

import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(_HERE, "assets")

# ---------- 槽位 ----------
SLOT_HEAD = "head"            # 戴在头上的
SLOT_ARMS = "arms"            # 抱在怀里的
SLOT_COMPANION = "companion"  # 身边的活物（独立行走，不是"穿戴"）

# 槽位冲突时的优先顺序（同槽位多个配件时保留靠前的）
SLOT_PRIORITY = {SLOT_HEAD: 0, SLOT_ARMS: 1, SLOT_COMPANION: 2}

# ---------- 配件类型表 ----------
# basis     : 尺寸基准 —— head=按实测头宽 / sprite=按立绘宽
#             （小黑猫必须用 sprite：Q 版是个大头娃娃，头宽≈整幅宽，按头宽算会把猫
#               画得比她脑袋还大；用立绘宽的比例才恒为"一只小猫"）
# ratio     : 相对基准的缩放（标定值，改动请连 tools/make_accessories.py --preview 一起看）
# pivot_y   : 素材高度中，对齐到锚点的那一行占整高的比例
# anchor    : head_top / arms_bottom / ground
# enter     : 入场动效类型
KINDS = {
    "beret": {
        "slot": SLOT_HEAD,
        "files": ("acc_beret.png", "acc_beret_f.png"),
        "basis": "head",
        "ratio": 0.90,               # 默认（高冷版：head_w≈头骨宽，直接可用）
        # **Q 版必须按风格修正**：chibi 头发厚，实测 head_w（眼高行的不透明跨度）
        # 量到的是**头发**（0.945 立绘宽）而非头骨（约 0.56）——按头发算帽子会大得压住整个头。
        "ratio_by_style": {"q": 0.82, "alt": 0.90},   # Q 版按头骨而非头发修正
        "anchor": "head_top",
        # 生成式素材（3/4 视角、自带向右耷拉）的帽檐落在高度的 62% 处 —— 目视标定值。
        # **换素材必须重标**：这个值无法从资产自动推导，只能看戴上后的效果定。
        "pivot_y": 0.62,
        # 帽檐再往下压一点（按**立绘高**的比例，不用窗口高——窗口高会随气泡开关变，
        # 用窗口高做基准会导致开/关气泡时帽子位置漂移）
        "dy_sprite": 0.12,          # 往下压：帽子要"戴进去"，不是浮在发顶
        "enter": "drop",             # 从上方落下 → 到位回弹（自然流畅）
        "min_w": 40.0,
    },
    "whale_plush": {
        "slot": SLOT_ARMS,
        "files": ("acc_whale_plush.png", "acc_whale_plush_f.png"),
        "basis": "head",
        "ratio": 0.56,               # 目视标定：0.46 偏小、0.66 盖住躯干
        "anchor": "arms_bottom",     # 锚点是**底边**（玩偶下沿），不是顶边
        "pivot_y": 1.0,
        "dy_ratio": 0.0,
        "enter": "hug",              # 轻微下沉 + 放大到 1（像被抱住）
        "min_w": 28.0,
    },
    "cat": {
        "slot": SLOT_COMPANION,
        "files": None,               # 帧由 _CAT_FRAMES 提供
        "basis": "sprite",
        "ratio": 0.34,
        "anchor": "ground",
        "pivot_y": 1.0,              # 脚底贴地
        "dy_ratio": 0.0,
        "enter": "walk_in",
        "min_w": 24.0,
    },
}

_CAT_FRAMES = {
    ("walk", 0, ""): "acc_cat_walk0.png",
    ("walk", 1, ""): "acc_cat_walk1.png",
    ("walk", 2, ""): "acc_cat_walk2.png",
    ("walk", 3, ""): "acc_cat_walk3.png",
    ("walk", 0, "_f"): "acc_cat_walk0_f.png",
    ("walk", 1, "_f"): "acc_cat_walk1_f.png",
    ("walk", 2, "_f"): "acc_cat_walk2_f.png",
    ("walk", 3, "_f"): "acc_cat_walk3_f.png",
    ("sit", 0, ""): "acc_cat_sit.png",
    ("sit", 0, "_f"): "acc_cat_sit_f.png",
    ("play", 0, ""): "acc_cat_play0.png",
    ("play", 1, ""): "acc_cat_play1.png",
    ("play", 0, "_f"): "acc_cat_play0_f.png",
    ("play", 1, "_f"): "acc_cat_play1_f.png",
}


# ===========================================================================
# persona：agent → 配件（声明式）
# ===========================================================================

def load_persona():
    """从 **agent 登记册**读 {agent_key: {accessory, label, accent}}。

    登记册是唯一来源（`assets/_agents.json`，缺失时回退 `_acc_persona.json`），
    见 `wb_agent_registry`。**只返回 enabled=true 的项** —— 用户关掉的 agent 不佩戴配件。
    失败返回空表（配件层整体降级，不影响桌宠本体）。
    """
    try:
        import wb_agent_registry as REG
    except ImportError:
        import sys
        sys.path.insert(0, _HERE)
        import wb_agent_registry as REG
    try:
        return REG.accessories()
    except Exception:
        return {}


def accessories_for(active_agents, persona=None):
    """活跃 agent 集合 → 要显示的配件类型列表（按槽位去重）。

    这就是「多 agent 同时使用 → 配件同时佩戴」的实现：取并集 + 槽位去重。
    返回按 SLOT_PRIORITY 排序的 kind 列表。
    """
    persona = load_persona() if persona is None else persona
    picked = {}
    for key in active_agents or ():
        spec = persona.get(key)
        if not spec:
            continue
        kind = spec.get("accessory")
        meta = KINDS.get(kind)
        if not meta:
            continue
        slot = meta["slot"]
        if slot not in picked:
            picked[slot] = kind
    return [k for k, _ in sorted(
        ((kind, SLOT_PRIORITY.get(KINDS[kind]["slot"], 99)) for kind in picked.values()),
        key=lambda kv: kv[1])]


# ===========================================================================
# 锚点
# ===========================================================================

def load_anchors():
    """读 assets/_acc_config.json（由 tools/make_accessories.py 实测生成）。"""
    p = os.path.join(ASSETS, "_acc_config.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    # 资产契约（pivot_y 等）由生成器写出 → 覆盖 KINDS 里的默认值。
    # 只在有值时覆盖：手工维护的 KINDS 仍是兜底，配置缺失时不会崩。
    for kind, spec in (data.get("assets") or {}).items():
        if kind in KINDS and isinstance(spec, dict):
            for k in ("pivot_y", "ratio", "basis", "min_w"):
                if k in spec and spec[k] is not None:
                    KINDS[kind][k] = spec[k]
    return (data.get("sprites") or {})


def anchor_for(anchors, state=None, facing="", style="q"):
    """按当前立绘状态取锚点；缺失时逐级回退（同 _face_cfg 的回退策略）。"""
    if not anchors:
        return None
    key = f"{style}.{state or 'idle'}{facing or ''}"
    for cand in (key,
                 f"{style}.{state or 'idle'}",
                 f"q.{state or 'idle'}{facing or ''}",
                 "q.idle"):
        if cand in anchors:
            return anchors[cand]
    return None


# ===========================================================================
# 纯几何：算配件该画在哪（无绘图后端依赖）
# ===========================================================================

def compute_placement(kind, face, sprite_rect, asset_ar, lay,
                      cat_x=None, cat_frame_ar=None, scale=1.0, style=None):
    """返回 (dst_x, dst_y, dst_w, dst_h)。

    kind         配件类型（KINDS 的键）
    face         锚点 dict（head_top / head_cx / head_w / eye_cy / chest_y，均已归一化）
    sprite_rect  立绘在窗口中的矩形 (x, y, w, h)
    asset_ar     素材宽高比 = 宽/高
    lay          布局 dict（含 W/H/sc）
    cat_x        仅 cat：黑猫当前 x（窗口坐标，由行为状态机驱动）
    scale        桌宠缩放
    """
    spec = KINDS.get(kind)
    if not spec or not face:
        return None
    sx, sy, sw, sh = sprite_rect
    ar = asset_ar if asset_ar else 1.0

    if spec.get("basis") == "sprite":
        base_px = sw
    else:
        base_px = max(1.0, face.get("head_w", 0.5) * sw)
    ratio = (spec.get("ratio_by_style") or {}).get(style, spec["ratio"])
    tw = max(spec.get("min_w", 12.0) * scale, base_px * ratio)
    th = tw / ar

    if spec["anchor"] == "head_top":
        head_top_px = sy + face.get("head_top", 0.05) * sh
        x = sx + face.get("head_cx", 0.5) * sw - tw / 2.0
        y = head_top_px + sh * spec.get("dy_sprite", 0.0) - th * spec["pivot_y"]
        # 兜底：帽子绝不许画到窗口上沿之外。极端缩放下宁可「戴低一点」，
        # 也不要被窗口裁掉半个帽顶——那种瑕疵在桌面上非常显眼。
        y = max(0.0, y)
    elif spec["anchor"] == "arms_bottom":
        # 锚点是玩偶**底边**：否则"胸口 y"会被当成顶边，玩偶直接捅出窗口下沿
        x = sx + face.get("head_cx", 0.5) * sw - tw / 2.0
        bottom = sy + face.get("chest_y", 0.80) * sh
        y = bottom - th
        y = max(sy, min(y, sy + sh - th))       # 夹在立绘区间内，绝不越界
    else:                                   # ground：脚底贴地
        ground = sy + sh
        x = (cat_x if cat_x is not None else sx + face.get("head_cx", 0.5) * sw) - tw / 2.0
        y = ground - th
    return (x, y, tw, th)


# ===========================================================================
# 小黑猫：溜达 / 停下 / 玩耍 的行为状态机
# ===========================================================================
CAT_WALK = "walk"
CAT_SIT = "sit"
CAT_PLAY = "play"


class CatBehavior:
    """小黑猫的自主行为。

    约束：**永远待在窗口内**（左右留边距），走到目标后停一会儿（坐着或玩耍）再挑新目标。
    与桌宠本体的自主行为（`_behavior`）同构，但不共用调度——猫是独立实体。
    """

    EDGE = 2.0          # 距窗口边缘的最小留白（会被 scale 放大）

    def __init__(self, rng=None, scale=1.0):
        self.rng = rng or random.Random()
        self.scale = max(0.2, scale)
        self.reset()

    def reset(self):
        self.x = 0.0            # 窗口坐标（猫的**中心** x）
        self.target = 0.0
        self.state = CAT_SIT
        self.facing = ""        # "" 朝右 / "_f" 朝左
        self.frame_i = 0.0
        self.until = 0.0        # 当前动作持续到
        self.speed = 0.0
        self.inited = False

    @staticmethod
    def _intervals(bounds):
        """bounds 允许是 (lo,hi) 或 [(lo,hi), ...]（侧廊）。

        为什么要多区间：古见身后被立绘挡得严严实实，猫走到那儿就"消失"了——
        而需求是"在身边溜达"。所以把可行域拆成**立绘左右两条侧廊**，
        猫在侧廊之间穿行，永远看得见。
        """
        if not bounds:
            return [(0.0, 1.0)]
        if isinstance(bounds[0], (tuple, list)):
            return [tuple(b) for b in bounds if b[1] > b[0]]
        return [tuple(bounds)]

    def _pick_target(self, ivs, now):
        # 按区间宽度加权选一条侧廊（宽的更常去，但窄的也去）
        widths = [max(1.0, hi - lo) for lo, hi in ivs]
        total = sum(widths)
        r = self.rng.random() * total
        acc = 0.0
        lo, hi = ivs[-1]
        for (l, h), wd in zip(ivs, widths):
            acc += wd
            if r <= acc:
                lo, hi = l, h
                break
        span = max(1.0, hi - lo)
        # 六成概率走到该侧廊的另一头（"溜达"），四成概率就近小挪（"在附近转"）
        if self.rng.random() < 0.6:
            self.target = lo + self.rng.random() * span
        else:
            base = min(hi, max(lo, self.x))
            self.target = min(hi, max(lo, base + (self.rng.random() - 0.5) * span * 0.45))

    def update(self, dt, bounds, now):
        ivs = self._intervals(bounds)
        lo, hi = ivs[0]
        if not self.inited:
            self.x = lo + (hi - lo) * 0.25
            self._pick_target(ivs, now)
            self.inited = True

        if self.state == CAT_WALK:
            dx = self.target - self.x
            step = self.speed * dt
            if abs(dx) <= max(step, 0.5):
                self.x = self.target
                # 到站：随机坐下或玩耍一会儿
                self.state = CAT_SIT if self.rng.random() < 0.55 else CAT_PLAY
                self.until = now + self.rng.uniform(1.6, 3.6)
                self.frame_i = 0.0
            else:
                self.x += step if dx > 0 else -step
                self.facing = "" if dx > 0 else "_f"
                self.frame_i += dt * 7.0        # 步行帧率
        else:
            if now >= self.until:
                self.state = CAT_WALK
                self.speed = self.scale * self.rng.uniform(26.0, 46.0)
                self._pick_target(ivs, now)
            elif self.state == CAT_PLAY:
                self.frame_i += dt * 5.0        # 玩耍帧率

        self.x = min(hi, max(lo, self.x))

    def sprite_key(self):
        """当前该用哪一帧（行为 → 素材名）。"""
        if self.state == CAT_WALK:
            return ("walk", int(self.frame_i) % 4, self.facing)
        if self.state == CAT_PLAY:
            return ("play", int(self.frame_i) % 2, self.facing)
        return ("sit", 0, self.facing)

    def sprite_file(self):
        return _CAT_FRAMES.get(self.sprite_key())

    def bounds(self, lay, cat_w, avoid=None):
        """猫中心 x 的可行区间（保证整只猫在窗口内）。

        avoid=(x0,x1) 给出立绘的水平占位；给了就返回**左右两条侧廊**，
        让猫在看得见的地方溜达，而不是钻到她身后消失。
        """
        e = self.EDGE * self.scale + cat_w / 2.0
        full = (e, max(e + 1.0, lay["W"] - e))
        if not avoid:
            return full
        gap = 3.0 * self.scale
        left = (full[0], avoid[0] - cat_w / 2.0 - gap)
        right = (avoid[1] + cat_w / 2.0 + gap, full[1])
        ivs = [b for b in (left, right) if b[1] - b[0] >= cat_w * 0.6]
        return ivs or [full]


# ===========================================================================
# 入退场动效
# ===========================================================================

def ease_out_back(t, s=1.70158):
    t = max(0.0, min(1.0, t))
    t -= 1.0
    return t * t * ((s + 1) * t + s) + 1.0


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class Transition:
    """单个配件的出现/消失进度（0=不可见，1=完全到位）。"""

    def __init__(self, kind):
        self.kind = kind
        self.p = 0.0
        self.target = 0.0

    def update(self, dt, duration, present):
        self.target = 1.0 if present else 0.0
        if duration <= 0:
            self.p = self.target
            return
        step = dt / float(duration)
        if self.p < self.target:
            self.p = min(self.target, self.p + step)
        elif self.p > self.target:
            self.p = max(self.target, self.p - step)

    @property
    def visible(self):
        return self.p > 0.001

    def transform(self, kind):
        """按配件类型把进度映射成 (dx, dy, scale, alpha)。

        戴帽（drop）：从上方落下 + ease-out-back 过冲 → 手感"自然流畅"
        抱玩偶（hug）：轻微下沉 + 从 0.86 放大到 1 → 像"抱起来"
        猫（walk_in）：从左侧走入，位移 + 淡入
        """
        p = self.p
        if kind == "beret":
            e = ease_out_back(p)
            return (0.0, (1.0 - e) * -22.0, 0.92 + 0.08 * e, min(1.0, p * 2.5))
        if kind == "whale_plush":
            e = ease_out_cubic(p)
            return (0.0, (1.0 - e) * 5.0, 0.86 + 0.14 * e, min(1.0, p * 2.2))
        if kind == "cat":
            e = ease_out_cubic(p)
            return ((e - 1.0) * 18.0, 0.0, 0.94 + 0.06 * e, min(1.0, p * 2.0))
        e = ease_out_cubic(p)
        return (0.0, (1.0 - e) * 6.0, 0.9 + 0.1 * e, min(1.0, p * 2.0))


class AccessoryState:
    """配件层的总状态：当前该显示哪些、各自的进度、猫的行为。

    桌宠每帧调 `update()`，绘制时按 `active_kinds()` 取即可。
    """

    def __init__(self, scale=1.0, rng=None):
        self.persona = load_persona()
        self.anchors = load_anchors()
        self.scale = scale
        self.cat = CatBehavior(rng=rng, scale=scale)
        self._trans = {}          # kind -> Transition
        self.active = []          # 当前 active agent keys
        self.sprite_span = None   # 立绘水平占位 (x0,x1)，由绘制方回写 → 猫避开它

    def set_active_agents(self, keys):
        self.active = list(keys or [])

    def kinds(self):
        return accessories_for(self.active, self.persona)

    def update(self, dt, lay, now, enter_s=0.42, exit_s=0.30):
        want = set(self.kinds())
        for kind in list(self._trans.keys()) + list(want):
            if kind not in self._trans:
                self._trans[kind] = Transition(kind)
        for kind, tr in self._trans.items():
            present = kind in want
            dur = enter_s if present else exit_s
            tr.update(dt, dur, present)
        # 猫只在它自己的配件可见时活动
        self.cat.update(dt, self.cat_bounds(lay), now)

    def cat_bounds(self, lay):
        spec = KINDS["cat"]
        # 猫宽由立绘宽决定，但立绘矩形此刻未必已知 → 用安全估计
        est = max(spec.get("min_w", 24.0) * self.scale, lay["W"] * 0.22)
        return self.cat.bounds(lay, est, avoid=self.sprite_span)

    def transition(self, kind):
        return self._trans.get(kind)

    def active_kinds(self):
        return [k for k in self.kinds() if self._trans.get(k, Transition(k)).visible]
