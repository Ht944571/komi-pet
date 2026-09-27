# -*- coding: utf-8 -*-
"""
重新设计古见同学 PSD 的上眼皮 eyelid_upper：
- 参考三视图：平直、细长的粗黑上眼线，眼尾微扬，眼神冷淡半睁
- 同时向上扩展成大色块（睁眼时藏在刘海里，闭眼时下移盖住眼睛）
输出：拆层_古见同学_对齐裁切_新眼皮.psd（先不覆盖原件）
"""
import os
from psd_tools import PSDImage
from PIL import Image, ImageDraw

SRC = r"D:\workbuddy\古见同学桌宠\live2d\拆层_古见同学_对齐裁切.psd"
OUT_PSD = r"D:\workbuddy\古见同学桌宠\live2d\拆层_古见同学_对齐裁切_新眼皮.psd"
OUT_PREVIEW = r"D:\workbuddy\古见同学桌宠\live2d\新眼皮预览.png"

psd = PSDImage.open(SRC)
W, H = psd.width, psd.height
print("canvas", W, H)

# 1. 取出各图层位图
layers = {}
for l in psd:
    layers[l.name] = l.composite()  # RGBA, 整画布大小

eyes = layers["eyes"]
old_lid = layers["eyelid_upper"]

# 2. 定位眼睛实际像素范围
def content_bbox(rgba):
    alpha = rgba.split()[3]
    return alpha.getbbox()

eyes_bbox = content_bbox(eyes)
print("eyes bbox", eyes_bbox)

# 分析左右眼：用 alpha 在水平方向的投影，找两块
import numpy as np
ea = np.array(eyes.split()[3])
cols = (ea > 16).sum(axis=0)
# 找连续有像素的列段
segs = []
in_seg = False
for x, v in enumerate(cols):
    if v > 0 and not in_seg:
        start = x; in_seg = True
    elif v == 0 and in_seg:
        segs.append((start, x)); in_seg = False
if in_seg:
    segs.append((start, len(cols)))
print("eye column segs", segs)

# 3. 画新上眼皮
new_lid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(new_lid)

# 颜色
LINE = (28, 16, 30, 255)       # 近黑深紫（眼线）
MASS = (58, 40, 66, 255)       # 头发深紫（藏在刘海里的色块主体）

def eye_region(seg):
    x0, x1 = seg
    # 该眼在 eyes 图里的纵向范围
    sub = ea[:, x0:x1]
    rows = np.where((sub > 16).any(axis=1))[0]
    return x0, x1, rows.min(), rows.max()

for seg in segs:
    x0, x1, ytop, ybot = eye_region(seg)
    cx = (x0 + x1) // 2
    print("eye:", x0, x1, ytop, ybot)
    # 眼线：沿着眼睛上缘，平直粗黑，眼尾（外侧）微扬
    # 判断内外眦：画面中心在 W/2
    outer_is_right = cx > W / 2  # 右眼外眦在右
    # 眼线纵向位置：略高于眼睛顶
    line_y = ytop - 2
    thickness = 9
    # 眼线横向范围比眼睛略宽
    lx0 = x0 - 6
    lx1 = x1 + 8
    # 画略带弧度的粗眼线（用多边形）
    # 上缘/下缘控制点，下缘平直，外眦上扬
    rise = 5  # 眼尾上扬量
    if outer_is_right:
        poly = [
            (lx0, line_y + 2), (lx1 - 10, line_y - rise), (lx1, line_y - rise - 2),
            (lx1, line_y - rise + thickness - 2), (lx1 - 10, line_y + thickness - rise),
            (lx0, line_y + thickness + 2)
        ]
    else:
        poly = [
            (lx1, line_y + 2), (lx0 + 10, line_y - rise), (lx0, line_y - rise - 2),
            (lx0, line_y - rise + thickness - 2), (lx0 + 10, line_y + thickness - rise),
            (lx1, line_y + thickness + 2)
        ]
    d.polygon(poly, fill=LINE)

    # 睫毛：外眦上方 2-3 根
    lash_color = LINE
    if outer_is_right:
        for i, off in enumerate([0, 10, 20]):
            bx = lx1 - 6 - off
            d.line([(bx, line_y - rise + 2), (bx + 6 - i*2, line_y - rise - 9 + i*2)],
                   fill=lash_color, width=3)
    else:
        for i, off in enumerate([0, 10, 20]):
            bx = lx0 + 6 + off
            d.line([(bx, line_y - rise + 2), (bx - 6 + i*2, line_y - rise - 9 + i*2)],
                   fill=lash_color, width=3)

    # 大色块主体：从眼线向上扩展，高度要能盖住眼睛（闭眼用）
    mass_top = line_y - (ybot - ytop) - 18  # 盖住整个眼睛还多一点
    mx0 = lx0 - 4
    mx1 = lx1 + 4
    d.rectangle([mx0, mass_top, mx1, line_y + thickness], fill=MASS)
    # 重新把眼线画在最上面（保证下缘颜色深）
    d.polygon(poly, fill=LINE)
    if outer_is_right:
        for i, off in enumerate([0, 10, 20]):
            bx = lx1 - 6 - off
            d.line([(bx, line_y - rise + 2), (lx1 if i==0 else bx + 6 - i*2,
                   line_y - rise - 9 + i*2)], fill=lash_color, width=3)
    else:
        for i, off in enumerate([0, 10, 20]):
            bx = lx0 + 6 + off
            d.line([(bx, line_y - rise + 2), (lx0 if i==0 else bx - 6 + i*2,
                   line_y - rise - 9 + i*2)], fill=lash_color, width=3)

# 4. 预览：新眼皮叠在 eyes + head 上
prev = Image.new("RGBA", (W, H), (255, 255, 255, 255))
for name in ["head", "blush", "nose", "mouth", "eyes", "highlight"]:
    if name in layers:
        prev.alpha_composite(layers[name])
prev.alpha_composite(new_lid)
prev.save(OUT_PREVIEW)
print("preview ->", OUT_PREVIEW)

# 5. 重组 PSD（用 pytoshop 若可用，否则导出图层文件夹备用）
try:
    import pytoshop
    from pytoshop import user_layers
    have_ps = True
except Exception as e:
    have_ps = False
    print("pytoshop not available:", e)

if have_ps:
    # 用 pytoshop 写分层 PSD
    import numpy as np
    order = ["hair_back","legs","skirt","arms","body","hair_side_l","hair_side_r",
             "head","mouth","nose","blush","eyes","highlight","eyelid_upper","hair_front"]
    psd_layers = []
    for name in order:
        img = layers[name] if name != "eyelid_upper" else new_lid
        arr = np.array(img)  # H,W,4
        r = arr[:, :, 0].astype(np.uint8)
        g = arr[:, :, 1].astype(np.uint8)
        b = arr[:, :, 2].astype(np.uint8)
        a = arr[:, :, 3].astype(np.uint8)
        psd_layers.append(user_layers.image_layer(
            name, top=0, left=0, bottom=H, right=W,
            channels={-1: a, 0: r, 1: g, 2: b},
            opacity=255, color_mode=pytoshop.enums.ColorMode.rgb))
    header = pytoshop.header.Header(
        version=pytoshop.enums.Version.psd,
        width=W, height=H,
        channels=3, depth=8,
        color_mode=pytoshop.enums.ColorMode.rgb)
    with open(OUT_PSD, "wb") as fd:
        with pytoshop.PSD(fd) as p:
            p.header = header
            p.layer_and_mask_info = user_layers.layer_and_mask(
                psd_layers, psd.color_mode)
    print("PSD ->", OUT_PSD)
else:
    # 备用：单独存出新眼皮 PNG，供手动替换
    p = r"D:\workbuddy\古见同学桌宠\live2d\新eyelid_upper.png"
    new_lid.save(p)
    print("saved layer png ->", p)
