# -*- coding: utf-8 -*-
"""
acc_art.py — 三个配件的形体设计与渲染（用 artkit 的渲染语言）
================================================================

第一版被判定"不好看"，根因不是形状而是**渲染语言**（纯色剪影 vs 立绘的
描边+多层cel+亮边）。本模块用 artkit 重画，四条规则见 artkit 的 docstring。

设计上的关键决定（每条都对应第一版的具体缺陷）
----------------------------------------------
**小黑猫**
  · 头占比加大（Q 版比例），用"圆头 + 小身体 + 细尾"的可爱比例
  · **尾巴改成贝塞尔链**：一串半径递减的圆沿曲线排布 → 自然并入轮廓，
    不再是"一根断开的细线"
  · 眼睛加 **上眼睑线 + 外眼角小勾 + 高光点**——这是"设计过的眼睛"与
    "白圆+黑点"的分水岭
  · **去掉胡须**：46px 下胡须只有 1px，读作划痕（第一版就是这个问题）
  · 耳朵加内耳浅色三角；脚下加接地影

**复古小纽扣贝雷帽**
  · 第一版是"扁椭圆 + 侧向椭圆"= 飞碟剖面。重画为**三段结构**：
    汗带（明显更窄、平） + 帽冠（向左上起拱、向右下耷拉） + 顶部小揪
  · 加 **5 道褶**（从汗带向上散开）与 **绒面长弧线**（借用立绘发丝的语言）
    —— 这两样是"读作织物"的关键
  · 纽扣做成**布包扣**：小、有聚拢底座、领结红 + 米白十字缝线，
    不再是"顶上一颗红球（天线）"

**鲸鱼玩偶**
  · 第一版读作气球/鱼。重画为**真尾鳍**（两片叶，不是平三角）
  · **肚皮补丁内缩 + 缝线**：外圈留出本体色，才读作"缝上去的布片"而非"挖了个洞"
  · 加 **背脊中缝**（布偶是两片拼的）与 **侧鳍**（贴体，不浮在肚皮上）
  · 去**喷水点**（读作烟囱）；腮红改小、靠眼下方（不是在肚皮上贴两块）
"""

import math

from PIL import Image, ImageDraw

import artkit as AK
from artkit import Figure, TOK, rgba

# ===========================================================================
# 1) 小黑猫
# ===========================================================================
CAT_W, CAT_H = 58, 46
CAT_GROUND = 44.0

# 黑猫专用的色阶：身体必须比线色**亮一档**，否则描边看不见（v2 的致命问题——
# 线色与填充都是深色，等于没有线稿）。线色走近黑，身体走偏亮的藕紫灰。
CAT_BASE = (60, 54, 72, 255)        # #3C3648
CAT_CEL = (40, 36, 50, 255)         # 右下沉降
CAT_RIM = (116, 108, 132, 255)      # 左上亮边
CAT_LINE = (30, 25, 34, 255)        # 近黑线


def _cat_solid(fig, body_dy=0.0, pose="walk"):
    """主体形体。尾巴用贝塞尔链（半径递减）→ 自然并入轮廓。"""
    dy = body_dy
    # --- 尾巴：短、柔和的 S 形（v2 太长像猴尾） ---
    p0, p1, p2, p3 = (13.0, 27.0 + dy), (5.0, 25.0 + dy), (3.0, 17.5 + dy), (9.5, 15.0 + dy)
    for i, (x, y) in enumerate(AK.curve_points(p0, p1, p2, p3, n=10)):
        t = i / 9.0
        r = 3.3 * (1 - t) + 1.1
        fig.ellipse((x - r, y - r, x + r, y + r))

    # --- 身体（略放大，头身比更协调） ---
    if pose == "sit":
        fig.ellipse((13, 23 + dy, 41, 43 + dy))
    elif pose == "play":
        fig.ellipse((11, 21 + dy, 41, 36 + dy))
    else:
        fig.ellipse((9, 22 + dy, 41, 40 + dy))

    # --- 腿 ---
    if pose == "sit":
        fig.ellipse((18, 32 + dy, 32, 44 + dy))
        fig.rounded((33, 30 + dy, 38.5, 44 + dy), 2.6)
        fig.rounded((39.5, 30 + dy, 45, 44 + dy), 2.6)
    else:
        for (bx, w) in ((12.0, 5.2), (18.5, 5.2), (31.0, 5.2), (37.0, 5.2)):
            fig.rounded((bx, 32 + dy, bx + w, CAT_GROUND), 2.6)

    # --- 头（大） ---
    fig.ellipse((23, 5 + dy, 55, 36 + dy))
    fig.poly([(26.5, 14 + dy), (29.5, 2.0 + dy), (37.5, 10 + dy)])
    fig.poly([(42.5, 10 + dy), (50.0, 2.0 + dy), (53.0, 14 + dy)])


def draw_cat(body_dy=0.0, leg_phase=0.0, sway=0.0, tilt=0.0, pose="walk"):
    """一帧小黑猫（面朝右，逻辑 58x46）。"""
    fig = Figure(CAT_W, CAT_H)
    _cat_solid(fig, body_dy=body_dy, pose=pose)

    img = AK.render(
        fig, CAT_BASE,
        rim_color=CAT_RIM, rim_alpha=0.38,
        cel_color=CAT_CEL, cel_alpha=0.55,
        line_color=CAT_LINE,
        bg_shadow=((16, CAT_GROUND - 1.6, 48, CAT_GROUND + 2.6), 44),
    )
    d = ImageDraw.Draw(img)
    S = fig.ss
    hy = 20.5 + body_dy

    # --- 耳内浅色 ---
    AK.draw_poly(d, [(28.8, 12.5 + body_dy), (30.4, 6.2 + body_dy), (34.6, 11.2 + body_dy)],
                 fill=CAT_RIM, ss=S)
    AK.draw_poly(d, [(45.2, 11.2 + body_dy), (49.2, 6.2 + body_dy), (51.0, 12.5 + body_dy)],
                 fill=CAT_RIM, ss=S)

    # --- 眼睛：**深色大眼 + 大高光**（v2 的"白眼底小瞳"会瞪眼；
    #     猫用实心大眼才可爱，且 30px 下可读性最好） ---
    for (ex, flip) in ((33.6 + tilt * 0.5, -1), (45.6 + tilt * 0.5, 1)):
        AK.draw_ellipse(d, (ex - 3.9, hy - 5.0, ex + 3.9, hy + 3.6),
                        fill=(34, 29, 40, 255), ss=S)
        AK.draw_ellipse(d, (ex - 2.2, hy - 3.5, ex + 0.3, hy - 1.0),
                        fill=(255, 255, 255, 240), ss=S)          # 主高光
        AK.draw_ellipse(d, (ex + 1.0, hy + 0.8, ex + 2.4, hy + 2.2),
                        fill=(255, 255, 255, 130), ss=S)          # 副高光
        # 上眼睑：压在眼上沿的一道深线（不是"框住"眼睛）
        lid = AK.curve_points((ex - 4.4, hy - 3.0), (ex - 1.4, hy - 6.4),
                              (ex + 1.6, hy - 6.4), (ex + 4.4, hy - 2.6), n=10)
        AK.taper_line(d, lid, CAT_LINE, 0.6, 2.0, ss=S)
        # 外眼角上挑的小勾
        c = AK.curve_points((ex + flip * 3.6, hy - 2.6), (ex + flip * 5.0, hy - 2.2),
                            (ex + flip * 5.4, hy - 1.0), (ex + flip * 5.0, hy + 0.2), n=6)
        AK.taper_line(d, c, CAT_LINE, 1.4, 0.5, ss=S)

    # --- 鼻 + ω 嘴 ---
    nx, ny = 39.4 + tilt * 0.45, hy + 5.4
    AK.draw_poly(d, [(nx - 1.6, ny), (nx + 1.6, ny), (nx, ny + 1.7)],
                 fill=(228, 146, 162, 255), ss=S)
    AK.draw_arc(d, (nx - 3.2, ny + 0.8, nx + 0.1, ny + 3.8), 350, 170, CAT_LINE, 0.8, ss=S)
    AK.draw_arc(d, (nx - 0.1, ny + 0.8, nx + 3.2, ny + 3.8), 10, 190, CAT_LINE, 0.8, ss=S)

    # --- 腮红（小、靠眼下） ---
    for ex in (30.6, 48.6):
        AK.draw_ellipse(d, (ex - 2.8, hy + 2.8, ex + 2.8, hy + 5.4),
                        fill=(236, 152, 168, 88), ss=S)

    # --- 前爪趾线 ---
    if pose != "sit":
        for bx in (32.8, 38.8):
            AK.draw_line(d, [(bx, CAT_GROUND - 2.8), (bx, CAT_GROUND - 0.6)],
                         (168, 158, 180, 120), width=0.55, ss=S)
    # **必须降采样**：artkit 在 6× 画布上作画以获得抗锯齿，成品要缩回逻辑尺寸。
    # 忘了这一步会落盘 348x276（逻辑 58x46）→ 体积膨胀 36 倍，且只能靠 GDI 在绘制时缩，
    # 既耗性能又损画质。踩过一次。
    return AK.save_down(img, CAT_W, CAT_H)


def build_cat():
    out = []
    walk = [dict(body_dy=0.0, leg_phase=0.00, sway=+1.0),
            dict(body_dy=-0.9, leg_phase=0.25, sway=+0.2),
            dict(body_dy=0.0, leg_phase=0.50, sway=-1.0),
            dict(body_dy=-0.9, leg_phase=0.75, sway=-0.2)]
    for i, kw in enumerate(walk):
        out.append((f"acc_cat_walk{i}", draw_cat(pose="walk", **kw)))
    out.append(("acc_cat_sit", draw_cat(pose="sit", sway=0.5)))
    out.append(("acc_cat_play0", draw_cat(pose="play", tilt=-1.6, sway=1.4, leg_phase=0.25)))
    out.append(("acc_cat_play1", draw_cat(pose="play", body_dy=-1.4, tilt=0.6,
                                          sway=-1.6, leg_phase=0.75)))
    return out


# ===========================================================================
# 2) 复古小纽扣贝雷帽
# ===========================================================================
BERET_W, BERET_H = 60, 34
BERET_BOTTOM_CY = 27.5

BERET_BASE = (78, 86, 132, 255)      # #4E5684 帽冠
BERET_CEL = (52, 58, 96, 255)
BERET_RIM = (146, 156, 200, 255)
BERET_BAND = (44, 48, 78, 255)        # 汗带（平的一条，不是椭圆）

BERET_W, BERET_H = 60, 30
BERET_BOTTOM_CY = 26.0                # 汗带下沿（会被头发盖住）


def draw_beret():
    """**第 6 版才想明白的关键**：帽子的底边必须是**平的**。

    前五版都画成"完整的椭圆盘"，底边是弧线且露在外面 → 无论加多少褶/绒面/纽扣，
    都只会在"盘子/飞碟"的框架里打转。
    真实的贝雷帽戴在头上时底边被头发遮住，所以正确画法是：
        **上半个椭圆（拱顶） + 一条平的底边 + 底下一条更暗的汗带**
    → 轮廓是"盖"不是"盘"，再配绒面与偏心纽扣，才读作帽子。
    """
    fig = Figure(BERET_W, BERET_H)
    # 拱顶：上半个椭圆（底边平）。右侧耷拉 + 左侧饱满 = 不对称。
    # 第 7 版把 bbox 压扁（高度 40 → 36）：帽冠更扁，才不像军用帽。
    fig.pie((4.0, 4.0, 50.0, 40.0), 180, 360)
    fig.pie((20.0, 6.0, 56.0, 38.0), 180, 360)
    fig.pie((8.0, 6.5, 40.0, 38.0), 180, 360)
    # **帽檐外翻的唇边**：真贝雷帽的织物底缘会超出帽冠、往外翻一点。
    # 这是"软布帽"与"硬质帽"的关键区别——没有它，轮廓再对也读作军帽/飞碟。
    fig.ellipse((1.5, 15.5, 54.5, 23.0))

    img = AK.render(fig, BERET_BASE,
                    rim_color=BERET_RIM, rim_alpha=0.42,
                    cel_color=BERET_CEL, cel_alpha=0.55)
    d = ImageDraw.Draw(img)
    S = fig.ss

    # --- 汗带：**平的**一条（拱顶底边在 y=23.5，汗带从 21 到 26 露出一条） ---
    AK.draw_ellipse(d, (14.0, 20.5, 46.0, 26.0), fill=BERET_BAND, ss=S)
    AK.draw_line(d, [(16.5, 23.6), (43.5, 23.6)], (30, 34, 58, 190), width=0.5, ss=S)

    # --- 绒面：3 条短弧，只在左上（低对比，不给图案感） ---
    for (a, b, c, e) in (((10.0, 13.0), (16.0, 8.0), (24.0, 7.0), (31.0, 9.0)),
                         ((12.5, 17.0), (19.0, 12.0), (27.0, 11.0), (33.0, 13.0)),
                         ((17.0, 20.0), (23.0, 16.0), (30.0, 15.5), (35.0, 17.5))):
        pts = AK.curve_points(a, b, c, e, n=12)
        AK.taper_line(d, pts, BERET_RIM, 0.25, 0.5, ss=S)

    # --- 骨白小纽扣：偏心（36% 处），实心亮圆 + 暗描边 ---
    cx, cy = 20.5, 5.4
    AK.draw_ellipse(d, (cx - 2.7, cy - 2.2, cx + 2.7, cy + 2.6),
                    fill=(54, 60, 96, 255), ss=S)
    AK.draw_ellipse(d, (cx - 2.3, cy - 1.9, cx + 2.3, cy + 2.1),
                    fill=TOK["cream"], ss=S)
    AK.draw_arc(d, (cx - 2.3, cy - 1.9, cx + 2.3, cy + 2.1), 25, 205,
                (176, 164, 150, 255), 0.5, ss=S)
    AK.draw_ellipse(d, (cx - 1.5, cy - 1.2, cx - 0.4, cy - 0.1),
                    fill=(255, 255, 255, 235), ss=S)
    return AK.save_down(img, BERET_W, BERET_H)      # 必须缩回逻辑尺寸（原因见 draw_cat）


# ===========================================================================
# 3) 鲸鱼玩偶
# ===========================================================================
WHALE_W, WHALE_H = 78, 56

WHALE_BASE = (86, 108, 194, 255)     # #566CC2
WHALE_CEL = (54, 68, 138, 255)
WHALE_RIM = (140, 160, 224, 255)
WHALE_BELLY = (232, 238, 250, 255)


def draw_whale_plush():
    """v4 关键改动：尾鳍、侧鳍、肚皮三处重构。

    ① 尾鳍：v3 用两个独立椭圆 → 读作"两个球"。改为**两片带圆角的叶 + 尾柄连成 V**。
    ② 侧鳍：v3 画成体侧方块 → 读作"一条腿"。改为**贴体、向后掠的圆角小鳍**。
    ③ 肚皮：v3 又大又靠上 → 读作"嘴/下巴"。改为**更窄更低 + 有明显缝线环**。
    """
    fig = Figure(WHALE_W, WHALE_H)

    # --- 尾鳍：两片叶 = **两串半径递减的圆**（与猫尾同一手法）。
    #     踩过的坑：第 4 版用"多边形 + 端点圆"→ 出来一根带球的尖刺，像炸弹引线。
    #     圆链天然平滑、无尖角，且会自然并进轮廓。 ---
    fig.ellipse((14.0, 23.0, 28.0, 34.0))                # 尾柄
    for (p0, p1, p2, p3) in (
            ((22.0, 27.5), (15.0, 22.5), (10.5, 19.0), (8.0, 17.5)),     # 上叶
            ((22.0, 30.0), (15.0, 35.0), (10.5, 38.5), (8.0, 40.0))):    # 下叶
        pts = AK.curve_points(p0, p1, p2, p3, n=9)
        for i, (x, y) in enumerate(pts):
            t = i / 8.0
            r = 5.0 * (1 - t) + 2.6
            fig.ellipse((x - r, y - r, x + r, y + r))

    # --- 背鳍：**鲸鱼最强的识别信号之一**（第 7 版新加）。
    #     没有它，圆身子 + 尾鳍很容易读成"鱼"或"气球"。 ---
    fig.poly([(43.0, 15.5), (48.5, 7.0), (54.5, 14.0)])
    # --- 身体：头在右、向尾收 ---
    fig.ellipse((19, 14, 74, 47))
    fig.ellipse((43, 11.5, 74, 44))                      # 头部饱满
    fig.ellipse((21, 21, 48, 43))                        # 后段收窄

    img = AK.render(fig, WHALE_BASE,
                    rim_color=WHALE_RIM, rim_alpha=0.42,
                    cel_color=WHALE_CEL, cel_alpha=0.5,
                    bg_shadow=((28, 44.0, 68, 50.0), 38))
    d = ImageDraw.Draw(img)
    S = fig.ss

    # --- 侧鳍：贴体、向后掠的圆角小鳍（在轮廓**内部**，才读作鳍不是腿） ---
    fin = AK.curve_points((50.0, 32.0), (56.0, 40.0), (64.0, 42.0), (68.0, 33.0), n=14)
    back = AK.curve_points((68.0, 33.0), (62.0, 37.5), (55.0, 36.0), (50.0, 32.0), n=12)
    AK.draw_poly(d, fin + back, fill=WHALE_CEL, ss=S)
    AK.draw_poly(d, AK.curve_points((52.0, 33.5), (57.0, 39.0), (63.0, 40.0), (66.0, 34.5), n=12)
                 + AK.curve_points((66.0, 34.5), (61.0, 37.0), (56.0, 35.5), (52.0, 33.5), n=10),
                 fill=WHALE_BASE, ss=S)

    # --- 肚皮补丁：更窄更低（避免读作嘴）+ 缝线环 ---
    belly = (34.0, 34.5, 62.0, 44.5)
    AK.draw_ellipse(d, belly, fill=WHALE_BELLY, ss=S)
    cxb, cyb = (belly[0] + belly[2]) / 2, (belly[1] + belly[3]) / 2
    for i in range(14):
        a0 = math.radians(196 + (i / 14.0) * 148)
        x = cxb + (belly[2] - belly[0]) / 2 * math.cos(a0) * 1.05
        y = cyb + (belly[3] - belly[1]) / 2 * math.sin(a0) * 1.05
        AK.draw_line(d, [(x, y), (x + 0.75, y + 0.45)], (255, 255, 255, 150), width=0.38, ss=S)

    # --- 背脊柔和暗边 ---
    back2 = AK.curve_points((26.0, 27.0), (38.0, 15.5), (56.0, 12.0), (72.0, 23.0), n=18)
    AK.taper_line(d, back2, WHALE_CEL, 0.25, 1.0, ss=S)

    # --- 嘴线：**鲸鱼第二强识别信号**（第 7 版新加）。
    #     从吻端向后拉一条长而柔和的口裂线——鲸鱼/海豚的标志，一条线就把"鱼"变成"鲸"。 ---
    mouth = AK.curve_points((72.5, 28.5), (66.0, 31.5), (58.0, 32.5), (50.0, 31.0), n=16)
    AK.taper_line(d, mouth, (44, 38, 92, 235), 0.3, 1.05, ss=S)

    # --- 脸：眼睛略收小靠拢 + 两眼同一侧高光（一致才不像"斗鸡眼"） ---
    for ex in (54.0, 63.5):
        AK.draw_ellipse(d, (ex - 3.2, 19.5, ex + 3.2, 27.0), fill=(34, 29, 40, 255), ss=S)
        AK.draw_ellipse(d, (ex - 2.0, 20.8, ex - 0.3, 22.8),
                        fill=(255, 255, 255, 235), ss=S)
        AK.draw_ellipse(d, (ex + 0.9, 23.8, ex + 2.1, 25.2),
                        fill=(255, 255, 255, 105), ss=S)
    AK.draw_arc(d, (55.0, 27.5, 62.5, 33.5), 25, 155, (34, 29, 40, 255), 0.8, ss=S)
    for ex in (49.0, 67.5):
        AK.draw_ellipse(d, (ex - 2.5, 27.5, ex + 2.5, 30.4),
                        fill=(236, 152, 168, 80), ss=S)
    return AK.save_down(img, WHALE_W, WHALE_H)      # 必须缩回逻辑尺寸（原因见 draw_cat）
