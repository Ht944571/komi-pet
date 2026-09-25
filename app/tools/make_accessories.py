# -*- coding: utf-8 -*-
r"""生成桌宠配件素材：小黑猫 / 复古纽扣贝雷帽 / 鲸鱼玩偶。

设计说明（重要）
----------------
用户给的参考是「罗小黑」与「DeepSeek 图标」。这两个分别受**版权**与**商标**保护，
所以本脚本**不复制**它们，只取「风格气质」做**原创绘制**：

  · 小黑猫   —— 取「圆头 + 大眼睛 + 纯黑 + 细尾」的可爱比例，造型为自己设计
  · 鲸鱼玩偶 —— 取「圆润蓝色小鲸鱼」的意象，做成**布偶**（带缝线/腮红），非品牌标志复刻
  · 贝雷帽   —— 无参考方，按古见同学的制服蓝/领结红配色原创

输出：assets/acc_*.png（真透明底，分层窗口 ULW_ALPHA 要求）

绘制手法：逻辑坐标 → 4 倍超采样绘制 → LANCZOS 缩回，得到抗锯齿边缘。

用法：
  python tools/make_accessories.py            # 生成全部
  python tools/make_accessories.py --preview  # 额外拼一张预览大图便于目视核对
"""

import argparse
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")

S = 4                       # 超采样倍数

# ---------- 配色（贴合古见同学展示页 / 现有立绘）----------
NAVY      = "#39406B"       # 制服蓝（气泡描边同色）
NAVY_DK   = "#2A2F52"
NAVY_LT   = "#4C5488"
CRIMSON   = "#B4364F"       # 领结红
CRIMSON_D = "#8E2A3E"
CREAM     = "#FDFBF6"
INK       = "#2A2438"       # 深紫黑
CAT_BLACK = "#232028"       # 猫身（比纯黑柔，避免死黑）
CAT_LT    = "#3A3644"       # 猫身亮面

WHALE_BLUE   = "#5B7CFA"
WHALE_BLUE_D = "#3D5BE0"
WHALE_BELLY  = "#DCE6FF"
BLUSH        = "#F2A0AE"


# ===========================================================================
# 基础绘制工具
# ===========================================================================

def new_canvas(w, h):
    """4 倍超采样画布（RGBA 全透明）。"""
    return Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))


def box(x0, y0, x1, y1):
    return [x0 * S, y0 * S, x1 * S, y1 * S]


def ellipse(d, b, fill=None, outline=None, width=1):
    d.ellipse(box(*b), fill=fill, outline=outline,
              width=max(1, int(width * S)) if outline else 0)


def rounded(d, b, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box(*b), radius=r * S, fill=fill, outline=outline,
                        width=max(1, int(width * S)) if outline else 0)


def polygon(d, pts, fill=None, outline=None, width=1):
    d.polygon([(x * S, y * S) for x, y in pts], fill=fill, outline=outline,
              width=max(1, int(width * S)) if outline else 0)


def line(d, pts, fill, width=1, joint="curve"):
    d.line([(x * S, y * S) for x, y in pts], fill=fill,
           width=max(1, int(width * S)), joint=joint)


def arc(d, b, start, end, fill, width=1):
    d.arc(box(*b), start, end, fill=fill, width=max(1, int(width * S)))


def downsample(img, w, h):
    return img.resize((w, h), Image.LANCZOS)


def save(img, name):
    os.makedirs(ASSETS, exist_ok=True)
    p = os.path.join(ASSETS, name)
    img.save(p)
    return p


def mirror(img):
    return img.transpose(Image.FLIP_LEFT_RIGHT)


# ===========================================================================
# 1) 小黑猫 —— 圆头 · 大眼 · 纯黑 · 细尾（原创，非罗小黑复刻）
# ===========================================================================
# 逻辑画布 56 x 46，脚底落在 y=44
CAT_W, CAT_H = 56, 46
CAT_GROUND = 44


def _cat_tail(d, sway=0.0, lift=0.0):
    """细尾：从身尾向右上弯出，带 sway（左右摆）与 lift（抬高）。"""
    x0, y0 = 12.0, 29.0
    pts = []
    for i in range(9):
        t = i / 8.0
        x = x0 - t * 9.5
        y = y0 - t * (13.0 + lift) + (t ** 1.6) * sway * 4.0
        pts.append((x, y))
    # 由粗到细的多段线
    for i in range(len(pts) - 1):
        wdt = 3.6 * (1 - i / (len(pts) - 1.0)) + 1.0
        line(d, [pts[i], pts[i + 1]], CAT_BLACK, width=wdt)
    # 尾尖小圆
    ellipse(d, (pts[-1][0] - 1.0, pts[-1][1] - 1.0, pts[-1][0] + 1.0, pts[-1][1] + 1.0),
            fill=CAT_BLACK)


def _cat_legs(d, phase, pose):
    """四条小腿。phase ∈ [0,1) 驱动前后交替；pose='sit' 时后腿收起。"""
    if pose == "sit":
        # 坐姿：后腿折叠在身下，前腿并拢伸直
        ellipse(d, (17, 34, 27, 41), fill=CAT_BLACK)
        rounded(d, (33, 32, 37.5, 42), 2.2, fill=CAT_BLACK)
        rounded(d, (38.5, 32, 43.0, 42), 2.2, fill=CAT_BLACK)
        return
    import math
    a = math.sin(phase * math.tau)
    b = math.sin((phase + 0.5) * math.tau)
    # 后腿（左）/ 前腿（右），对角交替更像四足行走
    for (bx, sw) in ((15.5, a), (21.0, b), (32.0, b), (37.5, a)):
        dx = sw * 2.0
        rounded(d, (bx + dx, 33, bx + 4.5 + dx, 42), 2.2, fill=CAT_BLACK)


def _cat_head(d, tilt=0.0):
    """圆头 + 三角耳 + 大眼睛。tilt 让头轻微歪（玩耍时更灵）。"""
    hx, hy, r = 39.0, 19.5, 13.2
    # 耳朵（先画，压在头下面）
    polygon(d, [(hx - 9.5 + tilt * 0.3, 9.0), (hx - 6.0 + tilt * 0.5, 1.5),
                (hx - 1.0 + tilt * 0.4, 7.5)], fill=CAT_BLACK)
    polygon(d, [(hx + 1.0 + tilt * 0.4, 7.5), (hx + 5.5 + tilt * 0.5, 1.5),
                (hx + 9.5 + tilt * 0.3, 9.0)], fill=CAT_BLACK)
    # 耳内浅色
    polygon(d, [(hx - 7.8, 8.2), (hx - 6.0, 4.0), (hx - 3.0, 7.8)], fill=CAT_LT)
    polygon(d, [(hx + 3.0, 7.8), (hx + 5.5, 4.0), (hx + 7.7, 8.2)], fill=CAT_LT)
    # 头
    ellipse(d, (hx - r, hy - r, hx + r, hy + r), fill=CAT_BLACK)
    # 大眼睛（黑猫用浅色眼底才看得见）+ 深色瞳
    for ex in (hx - 5.4, hx + 5.0):
        ellipse(d, (ex - 3.3, hy - 4.3, ex + 3.3, hy + 3.1), fill=CREAM)
        ellipse(d, (ex - 1.7, hy - 2.2, ex + 1.5, hy + 1.9), fill=INK)
        ellipse(d, (ex - 0.9, hy - 3.3, ex + 0.4, hy - 1.7), fill="#FFFFFF")
    # 小鼻子
    polygon(d, [(hx - 1.5, hy + 5.0), (hx + 1.5, hy + 5.0), (hx, hy + 6.6)],
            fill=BLUSH)
    # 胡须（两缕，轻）
    line(d, [(hx - 10.0, hy + 4.0), (hx - 15.5, hy + 2.4)], "#6A6284", width=0.7)
    line(d, [(hx - 10.0, hy + 6.4), (hx - 15.5, hy + 7.4)], "#6A6284", width=0.7)
    line(d, [(hx + 10.0, hy + 4.0), (hx + 15.5, hy + 2.4)], "#6A6284", width=0.7)
    line(d, [(hx + 10.0, hy + 6.4), (hx + 15.5, hy + 7.4)], "#6A6284", width=0.7)


def draw_cat(body_dy=0.0, leg_phase=0.0, sway=0.0, tilt=0.0, pose="walk"):
    """绘制一帧小黑猫（面朝右，逻辑 56x46）。"""
    img = new_canvas(CAT_W, CAT_H)
    d = ImageDraw.Draw(img)
    dy = body_dy

    _cat_tail(d, sway=sway, lift=1.0 if pose == "play" else 0.0)

    if pose == "sit":
        ellipse(d, (15, 25 + dy, 37, 40 + dy), fill=CAT_BLACK)      # 坐姿身体更圆
    elif pose == "play":
        ellipse(d, (14, 22 + dy, 38, 36 + dy), fill=CAT_BLACK)      # 伏低扑击
    else:
        ellipse(d, (12, 24 + dy, 38, 39 + dy), fill=CAT_BLACK)

    # 身体亮面（左上一点，避免死黑一块）
    ellipse(d, (17, 27 + dy, 30, 35 + dy), fill=CAT_LT)

    _cat_legs(d, leg_phase, pose)

    if pose == "play":
        # 前爪抬起（扑）
        rounded(d, (34, 26 + dy, 39, 34 + dy), 2.4, fill=CAT_BLACK)
        rounded(d, (39, 29 + dy, 44, 36 + dy), 2.4, fill=CAT_BLACK)

    _cat_head(d, tilt=tilt)
    return downsample(img, CAT_W, CAT_H)


def _build_cat_legacy():
    out = []
    # 行走 4 帧（对角步态 + 尾摆 + 身体起伏）
    walk = [dict(body_dy=0.0, leg_phase=0.00, sway=+1.0),
            dict(body_dy=-0.8, leg_phase=0.25, sway=+0.2),
            dict(body_dy=0.0, leg_phase=0.50, sway=-1.0),
            dict(body_dy=-0.8, leg_phase=0.75, sway=-0.2)]
    for i, kw in enumerate(walk):
        out.append((f"acc_cat_walk{i}", draw_cat(pose="walk", **kw)))
    # 坐
    out.append(("acc_cat_sit", draw_cat(pose="sit", sway=0.5)))
    # 玩耍 2 帧（扑击 + 歪头）
    out.append(("acc_cat_play0", draw_cat(pose="play", tilt=-1.6, sway=1.4, leg_phase=0.25)))
    out.append(("acc_cat_play1", draw_cat(pose="play", body_dy=-1.2, tilt=0.6, sway=-1.6,
                                          leg_phase=0.75)))
    return out


# ===========================================================================
# 2) 复古小纽扣贝雷帽（原创；制服蓝 + 领结红纽扣）
# ===========================================================================
# 踩过的坑：第一版画成了"海军蓝飞碟"。原因是——
#   · 用「大扁椭圆 + 内部一个浅色椭圆」→ 浅色椭圆在外沿形成一圈"边缘环"，
#     这正是飞碟的特征读数（碟身 + 环状舷窗带）
#   · 轮廓左右对称 → 更像人造飞行器；贝雷帽一定是**不对称**的（往一侧耷拉）
# 修正：① 去掉内部填充椭圆，高光/暗边一律用**细弧线**；
#       ② 用两个同色椭圆叠出不对称轮廓；③ 顶部小揪做成"布包扣 + 聚拢底座"。
#
# 另一个认知：**孤立看任何帽子都像漂浮的盘子** —— 它是否"读作贝雷帽"取决于
# 戴在头上的语境。所以预览图必须合成到立绘头部来看（--preview 已这么做）。
# 贝雷帽的形状要点：**扁**（宽高比 ≈1.8）——第一版做成 1.45 太"高"，
# 戴上后像顶了个海军蓝头盔；真贝雷帽是压扁的一团。
BERET_W, BERET_H = 58, 33
BERET_BOTTOM_CY = 27.5     # 帽檐下沿所在 y（绘制时对齐发顶）


def draw_beret():
    """贝雷帽：扁平不对称帽冠（向右耷拉）+ 顶部领结红布包扣。

    画布 58x33。绘制时按实测头宽缩放，并把 y=BERET_BOTTOM_CY 对齐到发顶。
    """
    import math
    img = new_canvas(BERET_W, BERET_H)
    d = ImageDraw.Draw(img)

    # ---- 帽冠：两个同色椭圆叠出不对称（右重左轻），整体压扁 ----
    ellipse(d, (5, 5, 51, 28), fill=NAVY)
    ellipse(d, (24, 8, 55, 27), fill=NAVY)

    # ---- 只画细弧，不填充（填充内圈会形成"飞碟环"）----
    arc(d, (5, 5, 51, 28), 25, 155, NAVY_DK, width=1.4)      # 下沿收口暗示
    arc(d, (8, 7, 46, 26), 195, 285, NAVY_LT, width=1.2)     # 左上羊毛高光

    # ---- 顶部小揪：布包扣 + 聚拢褶线（比第一版小一圈）----
    ellipse(d, (23.6, 1.8, 31.6, 9.8), fill=NAVY_DK)
    for ang in (200, 245, 295, 340):
        r, cx, cy = 4.2, 27.6, 5.8
        x1 = cx + math.cos(math.radians(ang)) * (r - 0.5)
        y1 = cy + math.sin(math.radians(ang)) * (r - 0.5)
        x2 = cx + math.cos(math.radians(ang)) * (r + 1.6)
        y2 = cy + math.sin(math.radians(ang)) * (r + 1.6)
        line(d, [(x1, y1), (x2, y2)], NAVY, width=0.7)
    ellipse(d, (24.6, 2.8, 30.6, 8.8), fill=CRIMSON)
    ellipse(d, (25.3, 3.5, 27.9, 6.1), fill="#D2607A")
    ellipse(d, (25.9, 4.1, 26.9, 5.1), fill=CREAM)
    for (px, py) in ((26.8, 6.4), (28.8, 6.4), (26.8, 7.8), (28.8, 7.8)):
        ellipse(d, (px - 0.32, py - 0.32, px + 0.32, py + 0.32), fill=CRIMSON_D)

    return downsample(img, BERET_W, BERET_H)


# ===========================================================================
# 3) 鲸鱼玩偶（原创布偶；非品牌标志复刻）
# ===========================================================================
WHALE_W, WHALE_H = 76, 58


def draw_whale_plush():
    """圆润蓝色小鲸鱼布偶：浅色肚皮 + 尾鳍 + 玩偶缝线 + 腮红。"""
    img = new_canvas(WHALE_W, WHALE_H)
    d = ImageDraw.Draw(img)

    # 尾鳍（先画，压在身后）
    polygon(d, [(17, 27), (5, 16), (9, 27), (5, 38)], fill=WHALE_BLUE_D)
    polygon(d, [(17, 27), (4, 16), (8, 27), (4, 38)], fill=WHALE_BLUE)

    # 身体（圆润水滴形）
    ellipse(d, (14, 10, 70, 50), fill=WHALE_BLUE)
    ellipse(d, (20, 13, 66, 44), fill=WHALE_BLUE)       # 上半更圆，形成水滴感

    # 肚皮（浅蓝，占下半）
    ellipse(d, (22, 28, 66, 48), fill=WHALE_BELLY)

    # 侧鳍（小、靠下，别抢肚皮的形）
    ellipse(d, (40, 36, 54, 45), fill=WHALE_BLUE_D)
    ellipse(d, (42, 37, 52, 43), fill=WHALE_BLUE)

    # 玩偶侧缝（沿肚皮上沿的一道弧线，虚线感 —— 读作"这是只布偶"）
    import math
    for i in range(11):
        t = i / 10.0
        x = 24 + t * 40
        y = 28 + math.sin(t * math.pi) * -3.4
        line(d, [(x, y), (x + 1.6, y - 1.2)], "#EAF0FF", width=0.8)

    # 眼睛 + 笑意（眼睛放大，玩偶要更"幼"）
    for ex in (34.0, 50.0):
        ellipse(d, (ex - 3.3, 19.0, ex + 3.3, 26.4), fill=INK)
        ellipse(d, (ex - 2.0, 20.4, ex - 0.3, 22.4), fill="#FFFFFF")
    arc(d, (36, 25, 48, 34), 20, 160, INK, width=1.3)

    # 腮红
    ellipse(d, (24, 26, 33, 31), BLUSH)
    ellipse(d, (53, 26, 62, 31), BLUSH)

    # 头顶喷水（小水花，三点）—— 点出"鲸鱼"
    for (cx, cy, r) in ((42, 8, 1.7), (46, 4.5, 1.4), (50, 2.6, 1.0)):
        ellipse(d, (cx - r, cy - r, cx + r, cy + r), "#9FC4F5")

    return downsample(img, WHALE_W, WHALE_H)


# ===========================================================================
# 主流程
# ===========================================================================

def _face_metrics(pet_img, state="idle"):
    """实测「发顶 y / 头心 x / 头宽 / 眼高 y」——配件定位的地基。

    第一版用「alpha 顶部 20% 内最宽的一行」估头宽，结果量到的是**头发散开的宽度**，
    贝雷帽因此大得盖住整张脸。改用 `_eye_config.json` 的**实测眼位**：
      · 眼高行上的不透明跨度 = 头部宽度（含发，帽子的正确基准）
      · 头心 x = 两眼中点 x
      · 发顶 y = 最上方不透明行
    """
    import json
    w, h = pet_img.size
    px = pet_img.split()[3].load()
    TH = 30

    y_top = 0
    for y in range(h):
        if any(px[x, y] > TH for x in range(0, w, 2)):
            y_top = y
            break

    cfg_path = os.path.join(ASSETS, "_eye_config.json")
    eyes = {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        st = (cfg.get("states") or {}).get(state) or {}
        eyes = st.get("eyes") or {}
    except Exception:
        pass

    if eyes.get("left") and eyes.get("right"):
        eye_cy = (eyes["left"]["cy"] + eyes["right"]["cy"]) / 2.0 * h
        cx = (eyes["left"]["cx"] + eyes["right"]["cx"]) / 2.0 * w
    else:
        eye_cy, cx = y_top + h * 0.16, w / 2.0

    row = int(max(y_top + 1, min(h - 1, eye_cy)))
    xs = [x for x in range(w) if px[x, row] > TH]
    head_w = (xs[-1] - xs[0]) if xs else int(w * 0.5)
    return y_top, cx, head_w, eye_cy


def _composite_on_pet(pet_img, items, scales, offsets, state="idle"):
    """把配件合成到立绘上，返回合成图。"""
    pet = pet_img.copy()
    y_top, cx, head_w, _eye_cy = _face_metrics(pet, state)
    h = pet.height
    ground = h - 2

    for name in ("cat", "plush", "beret"):
        if name not in items:
            continue
        acc = items[name]
        tw = max(1.0, head_w * scales[name])
        th = tw * acc.height / acc.width
        a = acc.resize((int(round(tw)), int(round(th))), Image.LANCZOS)

        if name == "beret":
            # 帽檐下沿（BERET_BOTTOM_CY/BERET_H）对齐发顶略下，压住头发
            bottom_ratio = BERET_BOTTOM_CY / BERET_H
            x = cx - a.width / 2 + offsets.get("beret_dx", 0)
            y = y_top + h * offsets.get("beret_ty", 0.030) - a.height * bottom_ratio
        elif name == "plush":
            x = cx - a.width / 2
            y = y_top + h * offsets.get("plush_ty", 0.52)
        else:                                   # cat：贴地平线，站在她左侧
            x = cx - head_w * offsets.get("cat_dx", 0.78) - a.width / 2
            y = ground - a.height

        # dest 必须取整：Pillow 的原地 alpha_composite 对浮点坐标不保证可用
        pos = (max(0, int(round(x))), max(0, int(round(y))))
        if a.mode == "RGBA" and pet.mode == "RGBA":
            pet.alpha_composite(a, pos)
        else:                                   # 兜底：走 alpha 掩膜 paste
            pet.paste(a, pos, a)
    return pet


def build_preview():
    """预览：**用线上同一套几何**（wb_accessories.compute_placement）合成到
    **忠实尺寸的窗口画布**上（300x210，含两侧空余），而不是画在立绘裁图上。

    为什么必须这样：画在立绘裁图上会看不到窗口留给小黑猫的余量，
    会误判"猫没地方溜达"。预览必须复现真实排版才可信。
    """
    import sys
    sys.path.insert(0, os.path.dirname(HERE))
    import importlib
    import wb_accessories as A
    importlib.reload(A)

    BASE_W, BASE_PET_H = 300, 210
    SC = 1.0
    import wb_motion as MOTION
    HR = int(MOTION.ACC_HEADROOM * SC)          # 配件净空（与线上一致）
    anchors = A.load_anchors()
    persona = A.load_persona()

    def window_canvas(pet_png, kinds, state_key, cat_x=None):
        """按桌宠真实排版铺一张窗口画布，并放上配件。"""
        lay = {"W": int(BASE_W * SC), "H": int((BASE_PET_H + MOTION.ACC_HEADROOM) * SC),
               "sc": SC, "pet_h": int(BASE_PET_H * SC), "hr": HR}
        ph = lay["pet_h"] - 8 * SC
        pet = Image.open(os.path.join(ASSETS, pet_png)).convert("RGBA")
        pw = ph * pet.width / pet.height
        x = (lay["W"] - pw) / 2
        y_bottom = lay["H"] - 4 * SC
        srect = (x, y_bottom - ph, pw, ph)

        canvas = Image.new("RGBA", (lay["W"], lay["H"]), (247, 244, 238, 255))
        pet_s = pet.resize((int(round(pw)), int(round(ph))), Image.LANCZOS)

        face = A.anchor_for(anchors, state_key.replace("alt_", ""),
                            "", "alt" if state_key.startswith("alt_") else "q")
        for kind in kinds:
            spec = A.KINDS[kind]
            fn = (spec["files"][0] if spec["files"] else "acc_cat_walk0.png")
            acc = Image.open(os.path.join(ASSETS, fn)).convert("RGBA")
            ar = acc.width / acc.height
            pl = A.compute_placement(kind, face, srect, ar, lay, cat_x=cat_x)
            if not pl:
                continue
            ax, ay, aw, ah = pl
            a = acc.resize((max(1, int(round(aw))), max(1, int(round(ah)))), Image.LANCZOS)
            # 黑猫画在立绘**之下**（从她脚边走）,其余画在**之上**（穿戴/抱持）
            if kind == "cat":
                canvas.alpha_composite(a, (int(round(ax)), int(round(ay))))
                canvas.alpha_composite(pet_s, (int(round(x)), int(round(y_bottom - ph))))
            else:
                canvas.alpha_composite(pet_s, (int(round(x)), int(round(y_bottom - ph))))
                canvas.alpha_composite(a, (int(round(ax)), int(round(ay))))
        if not kinds:
            canvas.alpha_composite(pet_s, (int(round(x)), int(round(y_bottom - ph))))
        return canvas, srect

    combos = [
        ("WorkBuddy · 小黑猫", ["cat"], "idle", "pet_idle.png"),
        ("ZCode · 贝雷帽", ["beret"], "idle", "pet_idle.png"),
        ("DSH · 鲸鱼玩偶", ["whale_plush"], "idle", "pet_idle.png"),
        ("三者同时（Q 版）", ["cat", "beret", "whale_plush"], "idle", "pet_idle.png"),
        ("三者同时（高冷版）", ["cat", "beret", "whale_plush"], "alt_idle", "pet_alt_idle.png"),
        ("高冷版单配（帽+偶）", ["beret", "whale_plush"], "alt_idle", "pet_alt_idle.png"),
    ]
    panels = []
    for label, kinds, st, pet_png in combos:
        if True:
            cat_x = None
            if "cat" in kinds:
                # 用真实行为区间取一个靠边的位置（最容易越界的地方）
                srect_probe = (65, HR + 4, 169, 202)
                face = A.anchor_for(anchors, st.replace("alt_", ""), "",
                                    "alt" if st.startswith("alt_") else "q")
                pl = A.compute_placement("cat", face, srect_probe, 56 / 46, 
                                         {"W": 300, "H": 210 + HR, "sc": 1.0}, cat_x=30)
                cat_x = 30 if pl else None
            canvas, _ = window_canvas(pet_png, kinds, st, cat_x=cat_x)
            panels.append((f"{label} · {st}", canvas))

    SCALE = 3
    COLS = 2
    pad, top = 14, 28
    pw_ = 300 * SCALE
    ph_ = int((210 + MOTION.ACC_HEADROOM) * SCALE)
    rows = (len(panels) + COLS - 1) // COLS
    W = COLS * pw_ + pad * (COLS + 1)
    H = rows * (ph_ + top) + pad
    sheet = Image.new("RGBA", (W, H), (247, 244, 238, 255))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(panels):
        r, c = divmod(i, COLS)
        x = pad + c * (pw_ + pad)
        y = pad + r * (ph_ + top)
        big = im.resize((pw_, ph_), Image.LANCZOS)
        d.text((x, y + 8), label, fill=(60, 56, 74, 255))
        sheet.alpha_composite(big, (x, y + top))

    path = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "acc_preview.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.convert("RGB").save(path)
    return path


def build_anchors():
    """为全部立绘实测配件锚点 → assets/_acc_config.json。

    与 _eye_config.json 同样的思路（逐立绘实测，不用估算），但测的是配件需要的量：
      head_top  发顶 y（归一化）—— 帽檐对齐点
      head_cx   头心 x（归一化）
      head_w    头宽（归一化，在眼高行取不透明跨度）—— 帽子/玩偶/猫的尺寸基准
      eye_cy    眼高 y（归一化）
      chest_y   胸口 y（归一化）—— 玩偶抱持位置
    """
    import json
    states = ("idle", "happy", "shy", "pout", "blush", "stone")
    out, missing = {}, []
    for style_prefix, name_prefix in (("q", ""), ("alt", "alt_")):
        for st in states:
            for suffix, mirror_flag in (("", False), ("_f", True)):
                key = f"{style_prefix}.{st}{suffix}"
                fn = f"pet_{name_prefix}{st}{suffix}.png"
                path = os.path.join(ASSETS, fn)
                if not os.path.isfile(path):
                    missing.append(fn)
                    continue
                im = Image.open(path).convert("RGBA")
                w, h = im.size
                px = im.split()[3].load()
                TH = 30
                y_top = 0
                for y in range(h):
                    if any(px[x, y] > TH for x in range(0, w, 2)):
                        y_top = y
                        break
                # 眼高行（优先用 _eye_config 的实测眼位，缺失则按发顶下 16% 估）
                eye_cy = None
                try:
                    with open(os.path.join(ASSETS, "_eye_config.json"),
                              encoding="utf-8") as f:
                        cfg = json.load(f)
                    base_st = f"alt_{st}" if style_prefix == "alt" else st
                    e = ((cfg.get("states") or {}).get(base_st) or {}).get("eyes") or {}
                    if e.get("left") and e.get("right"):
                        eye_cy = (e["left"]["cy"] + e["right"]["cy"]) / 2.0 * h
                except Exception:
                    pass
                if eye_cy is None:
                    eye_cy = y_top + h * 0.16
                row = int(max(y_top + 1, min(h - 1, eye_cy)))
                xs = [x for x in range(w) if px[x, row] > TH]
                if xs:
                    span, cx = xs[-1] - xs[0], (xs[0] + xs[-1]) / 2.0
                else:
                    span, cx = w * 0.5, w / 2.0
                # chest_y（玩偶底边）：**按风格定值**，不用"相对眼睛"的公式——
                # Q 版是大头娃娃（头占掉上半身），高冷版是常规比例，两者的
                # 眼睛位置与胸口位置的相对关系完全不同，一个公式必然有一个不准。
                # 目视标定：0.86/0.58 时玩偶顶边压到下巴，整档下移
                chest = 0.92 if style_prefix == "q" else 0.62
                out[key] = {
                    "head_top": round(y_top / h, 4),
                    "head_cx": round(cx / w, 4),
                    "head_w": round(span / w, 4),
                    "eye_cy": round(eye_cy / h, 4),
                    "chest_y": chest,
                }
    # 资产契约：pivot_y（素材高度的哪一比例对齐锚点）/ 尺寸比例 / 尺寸基准。
    # **必须由生成器写出、运行时读取**——否则改画布尺寸后会与 wb_accessories 里
    # 硬编码的值失配（贝雷帽画布从 33 改到 30 时就会错位）。这是"两处漂移"的根治办法。
    from acc_art import CAT_W, CAT_H, BERET_W, BERET_H, BERET_BOTTOM_CY, WHALE_W, WHALE_H
    assets = {
        "beret": {"pivot_y": round(BERET_BOTTOM_CY / BERET_H, 5)},
        "whale_plush": {"pivot_y": 1.0},
        "cat": {"pivot_y": 1.0},
    }
    data = {
        "version": 1,
        "note": "配件锚点 + 资产契约。由 tools/make_accessories.py 生成，勿手改。",
        "keys": sorted(out.keys()),
        "assets": assets,
        "sprites": out,
    }
    p = os.path.join(ASSETS, "_acc_config.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[accessories] 锚点表 → {p}（{len(out)} 个立绘"
          + (f"，缺 {len(missing)}" if missing else "") + "）")
    return p


def main():
    ap = argparse.ArgumentParser(description="生成桌宠配件素材 + 锚点表")
    ap.add_argument("--preview", action="store_true", help="输出合成预览大图")
    args = ap.parse_args()

    made = []

    import acc_art as _ART
    for name, img in _ART.build_cat():
        made.append(save(img, name + ".png"))
        made.append(save(mirror(img), name + "_f.png"))

    # 贝雷帽 / 鲸鱼玩偶：**优先用生成式素材**（assets/_gen/cut_*.png，由
    # tools/gen_to_asset.py 从 ImageGen 出图抠出）。生成素材质量远高于程序化图元；
    # 但保留 acc_art 作兜底——素材缺失时自动回退，不会开天窗。
    # 注意：**换素材必须重标 KINDS 的 pivot_y / ratio**（见 docs/配件美术规范.md）。
    _GEN = {"acc_beret": "cut_beret.png", "acc_whale_plush": "cut_whale.png"}
    for _base, _cut in _GEN.items():
        _gsrc = os.path.join(ASSETS, "_gen", _cut)
        if os.path.isfile(_gsrc):
            _g = Image.open(_gsrc).convert("RGBA")
            made.append(save(_g, _base + ".png"))
            made.append(save(_g.transpose(Image.FLIP_LEFT_RIGHT), _base + "_f.png"))
        else:
            print(f"[accessories] 缺 {_cut}，回退程序化绘制：{_base}")
            _fn = _ART.draw_beret if _base == "acc_beret" else _ART.draw_whale_plush
            made.append(save(_fn(), _base + ".png"))
            made.append(save(mirror(_fn()), _base + "_f.png"))

    print(f"[accessories] 生成 {len(made)} 个素材文件 → {ASSETS}")
    build_anchors()

    if args.preview:
        print(f"[accessories] 合成预览 → {build_preview()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
