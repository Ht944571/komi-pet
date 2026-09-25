# -*- coding: utf-8 -*-
"""
artkit.py — 配件插画渲染工具（让配件与立绘同一套视觉语言）
================================================================

为什么需要它（诊断结论）
------------------------
第一版配件是"用椭圆/三角拼出来的纯色剪影"，因此看起来像剪贴画，而桌宠立绘是
**粗细分明的曲线描边 + 多层 cel 明暗 + 沿轮廓亮边 + 低饱和配色**。
差的不是形状，是**渲染语言**。所以先把渲染语言做成工具，再重画形体。

四条渲染规则（与立绘对齐，改这个文件等于改全局风格）
----------------------------------------------------
  ① **描边**：线色是深暖紫黑（不是纯黑），宽度 ≈ 长边的 2%。
     实现用"描边压在填充下"——同样形体先按描边色描一遍，再把填充盖上去，
     于是**只有外轮廓的描边露出来**，内部接缝被填充盖住。比 alpha 膨胀便宜且精确。
  ② **cel 明暗**：固定光源在左上 45°。暗部 = 形体与"自身右下沉降 k"的交集
     （`overlap`），硬边、无渐变——立绘就是硬边 cel。
  ③ **亮边 rim**：形体减去"自身右下偏移 k" = 左上内侧一条带（`band`）。
     立绘发丝上就有这条亮边，是"有体积感"的关键。
  ④ **接地影**：给站立物一条柔和的椭圆投影（模糊），否则像悬空贴纸。

坐标系
------
所有绘制 API 都用**逻辑坐标（1×）**，内部按 `SS` 超采样后再降采样，
得到干净抗锯齿边缘。调用方不需要关心倍数。
"""

import math
from PIL import Image, ImageChops, ImageDraw, ImageFilter

SS = 6                      # 超采样倍数
LINE = (42, 32, 40, 255)    # 线色：深暖紫黑（取自立绘，非纯黑）

# ---------- 设计令牌（与立绘同源；改这里 = 改全部配件风格）----------
TOK = {
    "line_w_ratio": 0.020,          # 描边宽 = 形体长边 × 该比例
    "rim_k_ratio": 0.045,           # 亮边/暗部的偏移量 = 长边 × 该比例
    "shadow_blur": 0.030,           # 接地影模糊半径 = 画布宽 × 该比例

    # 藕紫系（立绘发色同族，用于"黑"的形体——纯黑会死板）
    "ink":        (46, 42, 54, 255),      # #2E2A36
    "ink_dark":   (31, 28, 38, 255),      # #1F1C26
    "ink_rim":    (122, 114, 136, 255),   # #7A7288 左上亮边

    # 制服蓝（降饱和后与立绘并列不打架）
    "navy":       (54, 60, 99, 255),      # #363C63
    "navy_dark":  (38, 42, 69, 255),      # #262A45
    "navy_rim":   (118, 128, 175, 255),   # #7680AF

    # 领结红
    "crimson":    (180, 54, 79, 255),     # #B4364F
    "crimson_dk": (142, 42, 62, 255),
    "crimson_lt": (210, 96, 122, 255),

    # 鲸鱼（玩具可以比立绘鲜一点，但仍收着——原版太亮像气球）
    "sea":        (76, 99, 184, 255),     # #4C63B8
    "sea_dark":   (51, 63, 126, 255),     # #333F7E
    "sea_rim":    (126, 147, 214, 255),   # #7E93D6
    "sea_belly":  (228, 234, 248, 255),   # #E4EAF8

    # 中性
    "cream":      (251, 243, 236, 255),   # #FBF3EC
    "skin":       (246, 228, 222, 255),
    "blush":      (238, 160, 174, 255),
    "blush_soft": (240, 186, 196, 255),
}


def rgba(c):
    return c if len(c) == 4 else (c[0], c[1], c[2], 255)


def shift(c, d):
    """明暗调整：d>0 变亮，d<0 变暗（保留 alpha）。"""
    return tuple(max(0, min(255, int(v + d))) for v in c[:3]) + (c[3] if len(c) == 4 else 255,)


# ===========================================================================
# Figure：记录形体，便于用多种方式反复渲染
# ===========================================================================

class Figure:
    """把形体按**逻辑坐标**记录下来，再以不同方式渲染。

    为什么要"记录"而不是直接画：同一批形体要出 4 个版本——
    描边层、填充层、左上亮边、右下暗部。直接画的话得把形状代码抄四遍，
    改一处漏三处（本项目吃过这种亏）。记录下来只写一次。
    """

    def __init__(self, w, h, ss=SS):
        self.w, self.h, self.ss = w, h, ss
        self.solid = []          # 主体形体 [(kind, args)]
        self.cel = []            # 暗部形体（可空，默认可由 solid 自动推导）
        self._target = self.solid

    # ---- 录制 ----

    def group(self, name):
        """后续形体归入 'solid'（主体）或 'cel'（暗部）。"""
        self._target = self.solid if name == "solid" else self.cel

    def ellipse(self, box, **kw):
        self._target.append(("ellipse", box, kw))

    def poly(self, pts, **kw):
        self._target.append(("poly", pts, kw))

    def rounded(self, box, r, **kw):
        # 统一记录格式为 (kind, 几何, 属性)；半径塞进属性里，避免解包分支
        self._target.append(("rounded", box, {**kw, "_r": r}))

    def pie(self, box, start, end, **kw):
        """扇形/半圆。**画"戴在头上"的帽子必需它**：

        帽子的底边必须是一条**平线**（会被头发遮住），如果用整椭圆，
        底边的弧线就露在外面 → 无论怎么调都读作"盘子/UFO"。
        start=180,end=360 得到上半个椭圆：拱顶 + 平底。
        """
        self._target.append(("pie", box, {**kw, "_a0": start, "_a1": end}))

    # ---- 渲染 ----

    def _geom(self, kind, geom, kw, dx=0.0, dy=0.0):
        """几何 + 偏移 + 超采样 → 目标坐标系。返回 (几何, 半径或None)。"""
        s = self.ss
        if kind == "poly":
            return [((x + dx) * s, (y + dy) * s) for (x, y) in geom], None
        box = [(geom[0] + dx) * s, (geom[1] + dy) * s,
               (geom[2] + dx) * s, (geom[3] + dy) * s]
        return box, (kw.get("_r", 0) * s if kind == "rounded" else None)

    def mask(self, group="solid", dx=0.0, dy=0.0):
        """形体 → L 蒙版。"""
        m = Image.new("L", (self.w * self.ss, self.h * self.ss), 0)
        d = ImageDraw.Draw(m)
        for (kind, geom, kw) in self._target_of(group):
            g, r = self._geom(kind, geom, kw, dx, dy)
            if kind == "ellipse":
                d.ellipse(g, fill=255)
            elif kind == "rounded":
                d.rounded_rectangle(g, radius=r, fill=255)
            elif kind == "pie":
                d.pieslice(g, kw["_a0"], kw["_a1"], fill=255)
            else:
                d.polygon(g, fill=255)
        return m

    def _target_of(self, group):
        return self.solid if group == "solid" else self.cel


# ===========================================================================
# 组合原语
# ===========================================================================

def _empty(fig):
    return Image.new("RGBA", (fig.w * fig.ss, fig.h * fig.ss), (0, 0, 0, 0))


def paste_mask(base, color, mask, alpha=1.0):
    """把颜色按蒙版贴到 base 上（alpha 可整体缩放）。"""
    if alpha < 1.0:
        mask = mask.point(lambda v: int(v * alpha))
    layer = Image.new("RGBA", base.size, rgba(color))
    layer.putalpha(mask)
    base.alpha_composite(layer)
    return base


def overlap(mask, dx, dy):
    """mask ∩ (mask 偏移 dx,dy) —— 偏移后仍有 mask 的区域。

    用于**右下暗部**：把形体与"自身向右下沉降 k"求交，得到右下侧的一条暗部，
    硬边、无渐变。这是 cel 上色的标准做法。
    """
    moved = ImageChops.offset(mask, int(dx), int(dy))
    return ImageChops.multiply(mask, moved)


def band(mask, dx, dy):
    """mask − (mask 偏移 dx,dy) —— 偏移后露出来的那条边带。

    用于**左上亮边**：向右下偏移后，原形体的左上内侧会露出来。
    """
    moved = ImageChops.offset(mask, int(dx), int(dy))
    return ImageChops.subtract(mask, moved)


def contact_shadow(fig, box, alpha=54, blur_ratio=None):
    """接地影：柔和椭圆，坐在主体下方。没有它，配件会像悬空贴纸。"""
    s = fig.ss
    m = Image.new("L", (fig.w * s, fig.h * s), 0)
    ImageDraw.Draw(m).ellipse([box[0] * s, box[1] * s, box[2] * s, box[3] * s], fill=alpha)
    r = (fig.w * (blur_ratio or TOK["shadow_blur"])) * s * 0.6
    return m.filter(ImageFilter.GaussianBlur(max(0.5, r)))


def draw_ellipse(canvas_draw, box, fill=None, outline=None, width=0, ss=1):
    b = [v * ss for v in box]
    canvas_draw.ellipse(b, fill=fill, outline=outline,
                        width=max(1, int(round(width * ss))) if outline else 0)


def draw_poly(canvas_draw, pts, fill=None, outline=None, width=0, ss=1):
    p = [(x * ss, y * ss) for x, y in pts]
    canvas_draw.polygon(p, fill=fill, outline=outline,
                        width=max(1, int(round(width * ss))) if outline else 0)


def draw_line(canvas_draw, pts, color, width=1.0, ss=1, joint="curve"):
    p = [(x * ss, y * ss) for x, y in pts]
    canvas_draw.line(p, fill=rgba(color), width=max(1, int(round(width * ss))), joint=joint)


def draw_arc(canvas_draw, box, start, end, color, width=1.0, ss=1):
    b = [v * ss for v in box]
    canvas_draw.arc(b, start, end, fill=rgba(color),
                    width=max(1, int(round(width * ss))))


def curve_points(p0, p1, p2, p3, n=16):
    """三次贝塞尔采样点——用于曲线描边（立绘的线是曲线，不是直线段）。"""
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def taper_line(draw, pts, color, w0, w1, ss=1, n_seg=None):
    """粗细渐变的曲线描边（立绘的线是两端细、中间粗）。

    直接调用方传已采样的点列，按索引线性插值宽度。
    """
    n = len(pts) - 1
    if n <= 0:
        return
    for i in range(n):
        t = i / max(1, n - 1)
        w = w0 + (w1 - w0) * t
        draw_line(draw, [pts[i], pts[i + 1]], color, width=max(0.4, w), ss=ss)


# ===========================================================================
# 一键渲染：描边 + 填充 + 暗部 + 亮边
# ===========================================================================

def render(fig, base_color, *, base_group="solid",
           rim_color=None, rim_alpha=0.55,
           cel_color=None, cel_alpha=0.85,
           line_w=None, rim_k=None, line_color=LINE,
           cel_from="auto", bg_shadow=None):
    """把 Figure 渲染成成品 RGBA。

    base_color  主体色
    rim_color   左上亮边色（None 则跳过）
    cel_color   右下暗部色（None 则跳过）
    cel_from    'auto' = 用主体形体推导右下暗部；'cel' = 用 fig.cel 里录制的暗部形体
    bg_shadow   (box, alpha) 接地影，画在所有内容之下
    """
    ss = fig.ss
    long_edge = max(fig.w, fig.h)
    lw = line_w if line_w is not None else long_edge * TOK["line_w_ratio"]
    rk = rim_k if rim_k is not None else long_edge * TOK["rim_k_ratio"]

    out = _empty(fig)

    # ① 接地影（最底）
    if bg_shadow:
        box, alpha = bg_shadow
        sm = contact_shadow(fig, box, alpha=alpha)
        paste_mask(out, (30, 24, 34, 255), sm, 1.0)

    m_solid = fig.mask(base_group)

    # ② 描边层：同形体按线色"放大描边"，后面被填充盖住内部，只留外轮廓
    stroke = Image.new("L", out.size, 0)
    sd = ImageDraw.Draw(stroke)
    sw = max(1, int(round(lw * 2 * ss)))          # 描边居中，露在外面的是 lw
    for (kind, geom, kw) in fig._target_of(base_group):
        g, r = fig._geom(kind, geom, kw)
        if kind == "ellipse":
            sd.ellipse(g, outline=255, width=sw)
        elif kind == "rounded":
            sd.rounded_rectangle(g, radius=r, outline=255, width=sw)
        elif kind == "pie":
            sd.pieslice(g, kw["_a0"], kw["_a1"], outline=255, width=sw)
        else:
            sd.polygon(g, outline=255, width=sw)
    paste_mask(out, line_color, stroke, 1.0)

    # ③ 主体填充
    paste_mask(out, base_color, m_solid, 1.0)

    # ④ 右下暗部（cel）
    if cel_color is not None:
        if cel_from == "cel" and fig.cel:
            cm = fig.mask("cel")
            cm = ImageChops.multiply(cm, m_solid)      # 裁进形体内
        else:
            cm = overlap(m_solid, rk, rk)
            cm = ImageChops.multiply(cm, m_solid)
        paste_mask(out, cel_color, cm, cel_alpha)

    # ⑤ 左上亮边（rim）——"有体积"的关键
    if rim_color is not None:
        rm = band(m_solid, -rk, -rk)
        paste_mask(out, rim_color, rm, rim_alpha)

    return out


def save_down(img, w, h):
    return img.resize((w, h), Image.LANCZOS)
