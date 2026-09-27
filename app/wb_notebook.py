#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_notebook.py — 「写字本子」状态机（纯逻辑，无 GDI+，可直接单测）
================================================================================
参考视频 `Q版古见同学写字.mp4` 的三段：**写字 → 翻页 → 展示**。
（视频是 AI 生成的、画面带水印，所以只当**动作参考**；本子素材是自己画的，
见 `tools/make_notebook.py` —— 这是项目既定红线。）

## 触发条件：直接用现有的「运行中会话数」，不另立一套判据

| 事件 | 判据（与 `wb_whale_win._sync_bubble_mode` **同一个**） | 动作 |
|---|---|---|
| **任务开始** | `len(active) > 0`，且此前 hidden | `appear`（翻开放上膝头）→ `writing` |
| **进行中** | `len(active) > 0` 持续 | `writing`：笔尖逐行写；写满一页 → 翻页 |
| **任务完成** | `prev > 0 且 len(active) == 0` | `present`：抬起本子、转正对着用户展示 |
| **新一轮开始** | `len(active) > 0` 时正处于 `present` | 收起旧页 → 回 `writing`，行数清零（**换新一页**） |

> 为什么必须共用同一判据：气泡的 OK 完成态就是靠 `prev_active_n > 0 → 0` 触发的。
> 本子另算一套判据迟早会和它对不上（"气泡说完成了、本子还在写"），那比不做还糟。

## 阶段与时长（`wb_motion` 可调）

```
hidden ──任务开始──► appear(0.32s) ──► writing(持续) ──任务完成──► present(0.42s 翻页/转正)
   ▲                                                                     │
   └──────────────── dismiss(0.30s) ◄────────── 保持 PRESENT_HOLD_S ◄────┘
```

几何一律**按立绘内容框动态算**（`deck()`），不许假设画布边界 ——
v3 八态内容顶差别极大（0.041~0.489），写死会把本子画到脸上（项目踩过）。
"""

import math

# ---- 阶段 ----
PHASE_HIDDEN = "hidden"
PHASE_APPEAR = "appear"
PHASE_WRITING = "writing"
PHASE_PRESENT = "present"
PHASE_DISMISS = "dismiss"

PHASES = (PHASE_HIDDEN, PHASE_APPEAR, PHASE_WRITING, PHASE_PRESENT, PHASE_DISMISS)

# ---- 本子尺寸与位置（**按内容高度**定，随缩放自动跟随）----
# 为什么按高度而不是宽度：竖版小本子的"大小"是对着**身体**看的；
# 而且必须卡住一条硬线 —— **不能遮住眼睛**。凭 `_eye_config` 的眼位换算，
# idle 态的眼睛在内容高的 **0.468** 处，所以本子上沿一律 ≥0.50。
BOOK_H_RATIO = 0.38        # 写字态：本子高 = 内容高 × 0.38（≈ 躯干高度）
BOOK_TOP_RATIO = 0.58      # 写字态：本子上沿落在内容高的 58%（膝头/大腿上方）
BOOK_TILT_DEG = -9.0       # 写字态：本子微微向内倾（配合"低头看本子"）
PRESENT_SCALE = 1.25       # 展示态：放大到 1.25 倍
PRESENT_TOP_RATIO = 0.50   # 展示态：上沿升到 0.50 —— 刚好停在眼睛下面（"举到下巴"）
PRESENT_TILT_DEG = 0.0     # 展示态：转正、正对镜头（参考视频末段就是正对）

# ---- 宽度上限（相对**内容宽**）----
# 跨页素材是横版（宽高比 1.36），只按高度等比放大就会**宽过她的肩膀**；
# 这两条上限让本子永远在她身体范围内，且与素材宽高比无关。
BOOK_W_RATIO = 0.46        # 写字态：本子宽 ≤ 内容宽 × 0.46
PRESENT_W_RATIO = 0.62     # 展示态：本子宽 ≤ 内容宽 × 0.62

# ---- 写字节奏 ----
LINE_S = 0.62              # 平均每行耗时（含行内书写）
LINE_INK_S = 0.34          # 单行墨迹从左写到右的时长
LINES_PER_PAGE = 9         # 一页几行（写满翻页）
PAGE_FLIP_S = 0.30         # 翻页动画时长

# ---- 呼吸/抖动（让"持续写字"不僵）----
WRITE_BOB_PX = 1.1         # 本子随手腕起伏的幅度（px，scale=1 基准）
WRITE_BOB_HZ = 1.9         # 起伏频率
WRITE_SWAY_DEG = 0.8       # 本子轻微左右摆

# ---- 墨水/纸张配色（与 assets/notebook 的两张图同一套色调）----
# ⚠️ 必须是 **ARGB（8 位十六进制，带 Alpha）**！写成 6 位（如 0x746556）就是
#    alpha=0 → 画出来完全透明，而且不报错（GDI+ 照画）。实测踩过：
#    页面上只有爱心中能看到，就是因为爱心写的是 0xFFE58AA0、其余是 6 位。
INK = 0xFF746556              # 手写墨迹（暖灰，不用纯黑：纯黑像印刷体）
INK_SOFT = 0xFFB8ABA0
PEN_DOT = 0xFF5E5348          # 笔尖
HEART = 0xFFE58AA0          # 页脚小爱心（呼应参考视频末帧）


def _ease_out_cubic(u):
    u = min(1.0, max(0.0, u))
    return 1.0 - (1.0 - u) ** 3


def _ease_in_out(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def line_span(i):
    """第 i 行墨迹写到页宽的百分之多少。

    确定性伪随机（不用 random，**测试才能逐帧复现**）：长短交替，像真在写字。
    """
    h = (i * 2654435761) & 0xFFFFFFFF
    r = (h % 1000) / 1000.0
    return 0.52 + 0.42 * r          # 0.52 ~ 0.94


def line_indent(i):
    """段首缩进（每 4 行来一次），让页面像有段落。"""
    return 0.10 if (i % 4 == 0) else 0.02


class NotebookState:
    """本子的一整套状态。调用方只管：喂 active 数 + 喂统计数据 + 每帧 tick。"""

    def __init__(self):
        self.phase = PHASE_HIDDEN
        self.t_phase = 0.0          # 当前阶段起点（秒）
        self.lines = 0              # 本页已写行数
        self.total_lines = 0        # 累计行数（跨页）
        self.page = 1
        self.flip_t0 = 0.0          # 上一次翻页起点（0 = 没在翻）
        self.stats = {}             # 展示用统计（写字过程中不断刷新成最新）
        self._last_active_n = None  # 第一次取样只建基线，不触发（同气泡 OK 态的做法）
        self.reason = ""            # 最近一次迁移的原因（事件上报用）

    # ---------- 驱动 ----------

    def feed(self, n_active, now, stats=None):
        """每 tick 喂一次。返回**本次发生的迁移名**（没迁移返回 ""）。

        迁移名：task_start / task_done / new_task / timeout / returned
        """
        if stats:
            # 只在"有任务"时刷新；任务已结束的那一帧保留最后一次数据（否则展示页会空）
            if n_active > 0 or not self.stats:
                self.stats = dict(stats)
        prev = self._last_active_n
        self._last_active_n = n_active
        moved = ""
        if prev is None:
            # 启动瞬间先建基线：已经在跑的任务也当"进行中"（不播 appear，直接写）
            if n_active > 0:
                self._to(PHASE_WRITING, now)
                moved = "task_start"
            return moved

        if prev == 0 and n_active > 0:
            # 有任务了。分两种读法（**都从 _reset_page 开始**：新任务 = 新的一页）：
            #   · 从 hidden 进来 → 播 appear（拿出本子翻开）
            #   · 从 present 进来（刚展示完就开了新任务）→ 直接接着写，别重播拿出动作
            was = self.phase
            self._reset_page()
            if was == PHASE_HIDDEN:
                self._to(PHASE_APPEAR, now)
                moved = "task_start"
            else:
                self._to(PHASE_WRITING, now)
                moved = "new_task"
        elif prev > 0 and n_active == 0:
            # ★ 与气泡 OK 态**同一判据**：一轮对话结束
            if self.phase in (PHASE_WRITING, PHASE_APPEAR):
                self._to(PHASE_PRESENT, now)
                moved = "task_done"
        return moved

    def tick(self, now):
        """推进时间轴：阶段自动流转 + 逐行落墨 + 翻页。返回是否还在动（决定要不要重绘）。"""
        u = now - self.t_phase
        if self.phase == PHASE_APPEAR:
            if u >= _dur("NOTE_APPEAR_S", 0.32):
                self._to(PHASE_WRITING, now)
        elif self.phase == PHASE_WRITING:
            self._write_tick(now)
        elif self.phase == PHASE_PRESENT:
            if u >= _dur("NOTE_PRESENT_S", 0.42) + _dur("NOTE_HOLD_S", 6.0):
                self._to(PHASE_DISMISS, now)
        elif self.phase == PHASE_DISMISS:
            if u >= _dur("NOTE_DISMISS_S", 0.30):
                self._to(PHASE_HIDDEN, now)
        return self.animating(now)

    def dismiss_now(self, now):
        """用户点了本子/数据回来了 → 立刻收起。"""
        if self.phase not in (PHASE_HIDDEN, PHASE_DISMISS):
            self._to(PHASE_DISMISS, now)

    # ---------- 内部 ----------

    def _to(self, phase, now):
        self.phase = phase
        self.t_phase = now
        if phase == PHASE_WRITING:
            self.flip_t0 = 0.0

    def _reset_page(self):
        self.lines = 0
        self.total_lines = 0
        self.page = 1
        self.flip_t0 = 0.0

    def _write_tick(self, now):
        """逐行落墨：写满 LINES_PER_PAGE 行 → 翻页。"""
        per_line = LINE_S * max(0.6, 1.0 / max(0.7, _speed()))
        # 应累计写到第几行（从 1 起算）
        want_total = int((now - self.t_phase) / per_line) + 1
        # ⚠️ 推进判据必须用 **total_lines**（单调递增），不能用 self.lines ——
        #    后者每满一页就归 1，`while want > self.lines` 会**永远追不上**（死循环，
        #    直接卡死桌宠的消息循环）。这是实测抓到的，别再改回去。
        while self.total_lines < want_total:
            self.total_lines += 1
            self.lines += 1
            if self.lines > LINES_PER_PAGE:
                self.lines = 1                 # 翻页：本页清零、页数 +1
                self.page += 1
                self.flip_t0 = now

    # ---------- 查询 ----------

    def visible(self):
        return self.phase != PHASE_HIDDEN

    def animating(self, now):
        return self.phase != PHASE_HIDDEN

    def phase_u(self, now):
        """当前阶段进度 0..1（appear/present/dismiss 用；writing 恒为 1）。"""
        u = now - self.t_phase
        if self.phase == PHASE_APPEAR:
            return min(1.0, u / max(1e-6, _dur("NOTE_APPEAR_S", 0.32)))
        asset = "nb_open.png"
        if self.phase == PHASE_PRESENT:
            return min(1.0, u / max(1e-6, _dur("NOTE_PRESENT_S", 0.42)))
        if self.phase == PHASE_DISMISS:
            return min(1.0, u / max(1e-6, _dur("NOTE_DISMISS_S", 0.30)))
        return 1.0

    def flip_u(self, now):
        """翻页进度 0..1（不在翻页返回 1）。"""
        if not self.flip_t0:
            return 1.0
        u = (now - self.flip_t0) / PAGE_FLIP_S
        return min(1.0, max(0.0, u))

    def ink_lines(self, now):
        """本页要画的墨迹：[{i, span, indent, ink(0..1)}]，含**正在写的那一行**。

        `ink` < 1 的那一行就是笔尖所在行 —— 绘制层据此画笔尖小点（"正在写"的读感）。
        """
        out = []
        for i in range(self.lines):
            out.append({"i": i, "span": line_span(i + (self.page - 1) * 100),
                        "indent": line_indent(i),
                        "ink": 1.0 if i < self.lines - 1 else self._cur_ink(now)})
        return out

    def _cur_ink(self, now):
        """正在写的那一行写到哪了（0..1）：该行起点 + LINE_INK_S 内写完。"""
        if self.flip_t0:
            return 1.0
        per_line = LINE_S * max(0.6, 1.0 / max(0.7, _speed()))
        started = self.t_phase + max(0, self.lines - 1) * per_line
        return min(1.0, max(0.0, (now - started) / LINE_INK_S))

    def present_text(self):
        """展示页底部的**可读**一行（真数据；本子上其余是手写波浪线）。"""
        s = self.stats or {}
        bits = []
        if s.get("turns"):
            bits.append(f'{s["turns"]} 轮')
        if s.get("dur"):
            bits.append(str(s["dur"]))
        if s.get("tokens"):
            bits.append(f'{s["tokens"]} tokens')
        if s.get("credit"):
            bits.append(f'{s["credit"]} 积分')
        return " · ".join(bits)

    def title(self):
        return (self.stats or {}).get("title") or ""

    # ---------- 几何（单一来源：绘制与测试都走这里）----------

    def deck(self, spr_rect, cbox, scale=1.0, now=0.0, eye_y=None):
        """算出本子该画在哪、多大、转多少度。

        `spr_rect` = 上一帧立绘矩形 (x, y, w, h)（窗口局部坐标）
        `cbox`     = 该形态的内容框 [x0, y0, x1, y1]（归一化，相对**立绘矩形**）
        `eye_y`    = 该形态**眼睛在内容高里的相对位置**（来自 _eye_config，可 None）。
                     传了就保证本子上沿**永远落在眼睛下方** —— 八态眼位差很大
                     （idle 0.468 / stone 0.510 / pout 0.533 / blush 0.577），
                     写死一个比例必然遮住某几个态的脸。
        返回 dict：book 矩形 + tilt + alpha + 素材名 + 页面矩形。
        """
        x, y, w, h = spr_rect
        x0, y0, x1, y1 = cbox
        cx0 = x + x0 * w                    # 内容左
        cy0 = y + y0 * h                    # 内容顶
        cw = (x1 - x0) * w                  # 内容宽
        ch = (y1 - y0) * h                  # 内容高

        AR_CLOSED = 300 / 380            # nb_closed：竖版
        AR_OPEN = 340 / 250              # nb_open：横版跨页

        asset = "nb_open.png"
        if self.phase == PHASE_PRESENT:
            u = self.phase_u(now)
            pop = 1.0 + 0.05 * math.sin(math.pi * min(1.0, u * 1.6))   # 举起时的轻微过冲
            # 上沿从写字位升到展示位（0.58 → 0.50），高度按 PRESENT_SCALE 放大
            top_r = _lerp(BOOK_TOP_RATIO, PRESENT_TOP_RATIO, _ease_in_out(u))
            book_h = ch * BOOK_H_RATIO * PRESENT_SCALE * pop
            book_w = book_h * AR_OPEN
            tilt = 0.0
            top = cy0 + ch * top_r
            alpha = 1.0
            showing = True
        elif self.phase == PHASE_APPEAR:
            u = _ease_out_cubic(self.phase_u(now))
            book_h = ch * BOOK_H_RATIO * (0.74 + 0.26 * u)
            book_w = book_h * AR_CLOSED
            tilt = BOOK_TILT_DEG
            top = cy0 + ch * (BOOK_TOP_RATIO + 0.05 * (1 - u))
            alpha = u
            showing = False
            asset = "nb_closed.png"
            asset = "nb_closed.png"          # 出场：合上（读作"把本子收起来"）
        elif self.phase == PHASE_DISMISS:
            u = self.phase_u(now)
            book_h = ch * BOOK_H_RATIO * PRESENT_SCALE * (1.0 - 0.28 * u)
            book_w = book_h * (AR_OPEN if u < 0.5 else AR_CLOSED)
            tilt = BOOK_TILT_DEG
            top = cy0 + ch * (PRESENT_TOP_RATIO + 0.06 * u)
            alpha = 1.0 - u
            showing = u < 0.5
            asset = "nb_closed.png"          # 收起：合上
        else:                                # writing（含 hidden 的兜底）
            bob = math.sin(now * math.tau * WRITE_BOB_HZ) * WRITE_BOB_PX * scale
            sway = math.sin(now * math.tau * WRITE_BOB_HZ * 0.5) * WRITE_SWAY_DEG
            book_h = ch * BOOK_H_RATIO * 0.86     # 摊开是横版，高度压一点才不显大
            book_w = book_h * AR_OPEN
            tilt = BOOK_TILT_DEG + sway
            top = cy0 + ch * BOOK_TOP_RATIO + bob
            alpha = 1.0
            showing = False

        # 翻页动画：横向压扁一下再弹回（2D 里读作"纸翻过去"）
        if self.flip_t0:
            fu = self.flip_u(now)
            if fu < 1.0:
                book_w *= (1.0 - 0.22 * math.sin(math.pi * fu))

        # 宽度上限：**按内容宽卡住**。跨页素材是横版（宽高比 1.36），只按高度放大
        # 会宽过她的肩膀（第一版就是这样，像举了块白板）。与素材宽高比无关，稳。
        # 上沿按该态**实际眼位**兜底钳制：宁可举低一点，也不能挡住眼睛
        if eye_y is not None:
            top = max(top, cy0 + ch * (float(eye_y) + 0.015))

        ar = AR_OPEN if asset == "nb_open.png" else AR_CLOSED
        w_cap = cw * (PRESENT_W_RATIO if showing else BOOK_W_RATIO)
        if book_w > w_cap:
            book_w = w_cap
            book_h = book_w / ar
        # 底部钳制：上面按眼位可能把本子压低，这里再收一次高度，别越过角色脚下。
        # （宁可小一点，也不能有一半挂到窗口外 —— 分层窗口外就是"被切掉"。）
        bottom_limit = cy0 + ch + 2
        if top + book_h > bottom_limit:
            book_h = max(ch * 0.16, bottom_limit - top)
            book_w = book_h * ar
        # 水平居中；垂直用**上沿**定位（top 已算好，不再用中心推导 ——
        # 中心定位时"放大"会同时把上沿顶到脸上，正是第一版遮住眼睛的原因）
        bx = cx0 + cw / 2 - book_w / 2
        by = top
        return {
            "phase": self.phase,
            "asset": asset,
            "showing": showing,
            "x": bx, "y": by, "w": book_w, "h": book_h,
            "tilt": tilt, "alpha": max(0.0, min(1.0, alpha)),
            "content": (cx0, cy0, cw, ch),
            "page": self._page_rect(bx, by, book_w, book_h, showing),
        }

    @staticmethod
    def _page_rect(bx, by, bw, bh, showing):
        """内页矩形 + 中缝区间（墨迹与页脚都画在这里）。

        摊开的跨页：左右各留纸边，中缝给一段"别写"的区间（跨中缝写字物理上不对）。
        """
        x = bx + bw * 0.085
        w = bw * 0.83
        y = by + bh * 0.14
        h = bh * 0.70
        gut = bw * 0.055
        return {"x": x, "y": y, "w": w, "h": h,
                "gut0": bx + bw / 2 - gut / 2, "gut1": bx + bw / 2 + gut / 2}


def _lerp(a, b, u):
    return a + (b - a) * u


def _dur(name, default):
    """从 wb_motion 读时长（允许单独调，不硬耦合）。"""
    try:
        import wb_motion as M
        return float(getattr(M, name, default))
    except Exception:
        return default


def _speed():
    try:
        import wb_motion as M
        return float(getattr(M, "NOTE_WRITE_SPEED", 1.0))
    except Exception:
        return 1.0
