#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_anim.py — 桌宠动作：帧序列素材 + 动作状态机（纯逻辑，可直接单测）。

## 动作表

| 动作 | 素材目录 | 播法 | 什么时候 |
|---|---|---|---|
| `idle`    | `anim/idle/`   | **循环**整段 | 常态（没有任务在跑） |
| `doze`    | `anim/doze/`   | 一次性 | 空闲久了 → 犯困 |
| `sleepy`  | `anim/sleepy/` | **循环**整段 | 困了之后一直挂着 |
| `wake`    | 复用 `doze`    | 一次性（**倒放**） | 来任务了 → 醒过来 |
| `pickup`  | `anim/pickup/` | 一次性 | 醒了/坐着 → 拿起本子 |
| `write`   | `anim/write/`  | **循环**写字段 | 任务进行中 |
| `present` | 复用 `write`   | 一次性（后段） | 任务完成 → 翻页 + 举本子展示 |

`wake` 不单独做素材：把 `doze` **倒放**就是「从托腮变回端坐」，观感正好是醒过来，
而且首尾帧天然与 `sleepy` / `idle` 对齐 —— 省一半素材还更准。

## 状态流转

```
idle ──空闲超时──► doze ──播完──► sleepy
 ▲                                    │
 │                             来任务  │
 └────────────────────────────────────┘
        wake ──► pickup ──► write ──任务完成──► present ──► idle
                            （有任务才拿本子）
```

## 触发判据

与气泡 OK 态**同一个**：`_sync_bubble_mode` 里的「运行中会话数」。
两者是不同变量但同源于同一个 n —— **改任一边都要想到另一边**，
否则会出现「气泡说完成了、她还在写」这种自相矛盾的画面。

## 素材怎么来

`tools/video_to_pet_frames.py`：把一段 AI 视频（白底）逐帧抠图、按统一画布缩放，
输出一串真透明 PNG + `_anim_meta.json`。
**为什么是帧序列而不是运行时播 WebM**：播放效果一帧不差（所有播放器内部都是逐帧位图），
但运行时不需要任何视频解码器 —— 项目零第三方依赖的红线保住了，打包也照旧。
"""

import json
import os

# ---- 动作常量 ----
ACT_IDLE = "idle"          # 坐着待机（循环）
ACT_DOZE = "doze"          # 变困（一次性）
ACT_SLEEPY = "sleepy"      # 困了待机（循环）
ACT_WAKE = "wake"          # 醒来（一次性，= doze 倒放）
ACT_PICKUP = "pickup"      # 拿本子（一次性）
ACT_WRITE = "write"        # 写字（循环写字段）
ACT_PRESENT = "present"    # 举本子展示（一次性；有自己的素材就用，没有则 = write 后段）

#: 需要**独立素材**的动作（wake 永远复用 doze；present 有素材就用、没有就复用 write）
ACTIONS = (ACT_IDLE, ACT_DOZE, ACT_SLEEPY, ACT_PICKUP, ACT_WRITE, ACT_PRESENT)

#: 动作 → 实际读哪份素材（**优先同名素材**，只有缺素材时才回退到这里）
CLIP_OF = {ACT_WAKE: ACT_DOZE, ACT_PRESENT: ACT_WRITE}

#: 一次性动作的兜底时长（秒）—— 只在素材缺失时用
FALLBACK_DUR = {ACT_DOZE: 4.5, ACT_WAKE: 4.5, ACT_PICKUP: 4.5, ACT_PRESENT: 6.4}

#: 写字段 / 展示段的分界（源片 97 帧里翻页发生在 ~80 帧；抽帧后 40/49 ≈ 0.816）
SPLIT_RATIO = 0.816

#: **按头大小对齐**用的缩放倍数（2026-09-28）。
#:
#: 两批素材的取景距离不一样：待机四段是**近景**（头高约占画布 33~35%），
#: 写字那段是**全身**（头高约占 27~28%）。直接播的话，切到写字的瞬间她的头会
#: 明显缩一下。这里把**近景那几个整体缩小**，让「头」在屏幕上一样大 ——
#: 视觉上等于「切到写字时镜头拉远了」，比头突然变小自然。
#:
#: 数值来源：把五段首帧（×1.0）裁「头顶往下 45% 画布」并排比对（`_head_raw.png`）——
#: 待机四段的头高约 **34% 画布**，写字那段约 **43%**，比值 1.27。
#: ⚠️ 中途踩过一次坑：先用 30% 条带读，把写字的**脖子/衣领**当成下巴，得出"待机头更大"的
#: 反向结论，白调了一轮。**读下巴要留足够的条带高度**，别让条带把下巴切在外面。
#: ⚠️ 这是**取景对齐**，不是「让两套素材变成同一个人」——
#: 它们头身比本来就不同，只能做到切换时头不跳。
HEAD_SCALE = {
    ACT_IDLE: 1.27,
    ACT_DOZE: 1.27,
    ACT_SLEEPY: 1.27,
    ACT_PICKUP: 1.27,
    ACT_WRITE: 1.00,
}


def head_scale_of(name):
    """取某动作的头部对齐倍数（没有的按 1.0）。"""
    return float(HEAD_SCALE.get(name, 1.0))


def _cfg(name, default):
    """从 wb_motion 读参数（允许单独调，不硬耦合）。"""
    try:
        import wb_motion as M
        return getattr(M, name, default)
    except Exception:
        return default


class AnimClip:
    """一段帧序列的元数据 + 「动作 → 帧号」。"""

    def __init__(self, meta, frames_dir):
        self.name = meta["name"]
        self.frames_dir = frames_dir
        self.count = int(meta["count"])
        self.fps = float(meta.get("fps") or 12.0)
        self.canvas = tuple(meta.get("canvas") or (420, 565))
        self.content_box = tuple(meta.get("content_box") or (0, 0, 1, 1))
        # 循环段起点（帧号，可缺省）：写字段素材可以带「进入写字」的前摇 ——
        # 首轮从头播完（含前摇），之后只循环 `loop_start..count-1`（2026-09-30 新版写字素材）。
        _ls = meta.get("loop_start")
        self.loop_start = int(_ls) if _ls else None
        # 分界帧（present 段的起点）：素材没写就按 SPLIT_RATIO 推（老素材的算法）
        self.split = (int(meta["split"]) if "split" in meta
                      else int(self.count * SPLIT_RATIO))
        self.prefix = self.name
        # 按头大小对齐的缩放倍数（见 HEAD_SCALE 的说明）
        self.head_scale = head_scale_of(self.name)

    # ---- 动作 → 帧号 ----

    def index(self, act, t0, now):
        """当前该显示第几帧。

        - `write`：**首轮从头播**（含「进入写字」前摇，条件是素材带 `loop_start`），
          之后只循环 `loop_start..count-1`；老素材（无 loop_start）仍按 `0..split-1` 循环
        - `idle` / `sleepy` 整段循环
        - `present` 从分界帧播到末帧并停住；`wake` 是倒放
        """
        dt = max(0.0, now - t0) * self.fps
        i = int(dt)
        if act == ACT_PRESENT:
            return min(self.count - 1, max(0, self.split) + i)
        if act == ACT_WRITE:
            if self.loop_start:
                if i < self.count:                    # 首轮：0..count-1 一路播下来
                    return i
                n = max(1, self.count - self.loop_start)
                return self.loop_start + (i - self.count) % n
            return i % max(1, self.split)
        if act in (ACT_IDLE, ACT_SLEEPY):
            return i % max(1, self.count)
        if act == ACT_WAKE:                       # 倒放：从末帧往回落
            i = self.count - 1 - i
        return min(self.count - 1, max(0, i))

    def duration(self):
        """整段素材播一遍的时长（秒）。"""
        return self.count / max(1e-6, self.fps)

    def act_is_loop(self, act):
        """该动作是否**循环播放**（`index` 里取模的那三个）。

        调用方（`wb_whale_win._draw_anim_frame`）靠它决定这一帧是"固定步进 +1"
        还是按墙钟取帧 —— 一次性动作（pickup/doze/wake/present）必须按时间走，
        否则会在状态切换前播不到末帧。
        """
        return act in (ACT_IDLE, ACT_SLEEPY, ACT_WRITE)

    def loop_range(self, act):
        """循环动作的循环区间 `(起, 止含)`。

        `wb_whale_win._draw_anim_frame` 的「每 tick 恰好 +1 帧」与 `index()` 共用它 ——
        两处各写一份迟早对不上（写字段带前摇时尤其容易）。
        """
        if act == ACT_WRITE:
            if self.loop_start:
                return self.loop_start, self.count - 1
            return 0, max(0, self.split - 1)          # 老素材：展示段留给 present
        return 0, self.count - 1

    def loop_len(self, act):
        """循环动作的一圈有多少帧（write 只循环「写字段」段，见 `index`）。"""
        lo, hi = self.loop_range(act)
        return max(1, hi - lo + 1)

    def frame_path(self, i):
        return os.path.join(self.frames_dir, f"{self.prefix}_{int(i):03d}.png")


class PetPhase:
    """桌宠动作状态机。调用方只管每 tick 喂一次「运行中会话数」+ 推进时间。

    ```
    idle ──空闲超时──► doze ──► sleepy ──(来任务)──► wake ──► pickup ──► write
     ▲                                                                      │
     └──────────────────── present ◄─────────────(任务完成)─────────────────┘
    ```

    `feed(n)` 处理「任务开始 / 结束」，`tick(now)` 处理「一次性动作播完」与「空闲超时」。
    """

    def __init__(self, sleepy_after=None):
        self.act = ACT_IDLE
        self.t_phase = 0.0
        self._last_n = None            # 第一次取样只建基线，不触发（同气泡 OK 态做法）
        self._idle_since = None        # 进入 idle 的时刻（算「空闲多久了」）
        self.reason = ""               # 最近一次迁移的原因（事件上报用）
        self.sleepy_after = float(
            sleepy_after if sleepy_after is not None
            else _cfg("IDLE_DOZE_AFTER_S", 180.0))

    # ---------- 驱动 ----------

    def feed(self, n_active, now):
        """喂「运行中会话数」。返回**本次发生的迁移名**（没迁移返回 ""）。"""
        prev = self._last_n
        self._last_n = n_active
        moved = ""
        if prev is None:
            # 启动瞬间先建基线：已经在跑的任务直接进写字（不播拿本子）
            if n_active > 0:
                self._to(ACT_WRITE, now)
                moved = "task_start"
            return moved

        if prev == 0 and n_active > 0:
            was = self.act
            if was == ACT_IDLE:
                self._to(ACT_PICKUP, now)          # 坐着 → 先拿本子
                moved = "task_start"
            elif was == ACT_SLEEPY:
                self._to(ACT_WAKE, now)            # 睡着 → 先醒过来（wake 播完再拿本子）
                moved = "wake_up"
            elif was in (ACT_DOZE, ACT_WAKE):
                moved = ""                          # 正在犯困/正在醒：让它演完，tick 里接
            else:
                self._to(ACT_WRITE, now)            # present 之后又开新任务：直接写
                moved = "new_task"
        elif prev > 0 and n_active == 0:
            # ★ 与气泡 OK 态**同一判据**：一轮对话结束
            if self.act in (ACT_WRITE, ACT_PICKUP):
                self._to(ACT_PRESENT, now)
                moved = "task_done"
        return moved

    def tick(self, now):
        """推进时间轴。返回迁移名（没迁移返回 ""）。"""
        u = now - self.t_phase
        a = self.act

        if a == ACT_DOZE:
            if u >= self._dur(ACT_DOZE):
                self._to(ACT_SLEEPY, now)
                return "to_sleepy"
        elif a == ACT_WAKE:
            if u >= self._dur(ACT_WAKE):
                # 醒了：有任务就接着拿本子，没有就回坐着
                self._to(ACT_PICKUP if (self._last_n or 0) > 0 else ACT_IDLE, now)
                return "to_pickup"
        elif a == ACT_PICKUP:
            if u >= self._dur(ACT_PICKUP):
                self._to(ACT_WRITE, now)
                return "to_write"
        elif a == ACT_PRESENT:
            if u >= self._dur(ACT_PRESENT):
                self._to(ACT_IDLE, now)
                return "to_idle"
        elif a == ACT_IDLE:
            # 没人搭理很久了 → 开始犯困
            if self._idle_since is not None and (now - self._idle_since) >= self.sleepy_after:
                self._to(ACT_DOZE, now)
                return "to_doze"
        return ""

    def note_activity(self, now):
        """用户动了桌宠（拖拽/点击）→ 重新计时「多久没搭理我」。"""
        if self.act in (ACT_IDLE, ACT_SLEEPY, ACT_DOZE):
            self._idle_since = now

    # ---------- 内部 ----------

    def _to(self, act, now):
        self.act = act
        self.t_phase = now
        # 「空闲计时」只在待在待机侧时有效
        if act == ACT_IDLE:
            self._idle_since = now
        elif act not in (ACT_SLEEPY, ACT_DOZE):
            self._idle_since = None

    def _dur(self, act):
        return FALLBACK_DUR.get(act, 4.0)

    # ---------- 查询 ----------

    def clip_name(self, clips):
        """当前动作该读哪份素材（优先同名素材；wake→doze、present→write）。返回 None 表示没素材。"""
        want = self.act if self.act in clips else CLIP_OF.get(self.act, self.act)
        if want in clips:
            return want
        # 素材缺失时逐级回退：写字缺 → 待机；待机缺 → 写字
        for alt in (ACT_IDLE, ACT_WRITE, ACT_SLEEPY, ACT_PICKUP, ACT_DOZE):
            if alt in clips:
                return alt
        return None

    def phase_u(self, now):
        """一次性动作的进度 0..1（循环动作为 1）。"""
        if self.act in (ACT_DOZE, ACT_WAKE, ACT_PICKUP, ACT_PRESENT):
            return min(1.0, (now - self.t_phase) / max(1e-6, self._dur(self.act)))
        return 1.0

    def animating(self, now):
        """是否还在动（决定要不要重绘）。循环动作恒为真。"""
        return True


def load(name, assets_dir=None):
    """读 `assets/anim/<name>/_anim_meta.json`。没有素材返回 None。"""
    here = os.path.dirname(os.path.abspath(__file__))
    assets = assets_dir or os.path.join(here, "assets")
    frames_dir = os.path.join(assets, "anim", name)
    mp = os.path.join(frames_dir, "_anim_meta.json")
    if not os.path.isfile(mp):
        return None
    try:
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
        clip = AnimClip(meta, frames_dir)
        # 首帧必须存在，否则视为素材不完整
        if not os.path.isfile(clip.frame_path(0)):
            return None
        return clip
    except Exception:
        return None


def load_all(assets_dir=None):
    """把 `ACTIONS` 里**存在**的动作都加载进来。返回 {动作名: AnimClip}。"""
    out = {}
    for name in ACTIONS:
        clip = load(name, assets_dir)
        if clip:
            out[name] = clip
    return out
