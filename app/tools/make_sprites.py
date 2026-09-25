# -*- coding: utf-8 -*-
"""立绘素材处理：边缘洪泛去白底（保护内部白色女仆装）→ 裁切 → 镜像 → 透明 PNG。

输入：素材源目录（默认 ~/Pictures/deepseek 桌宠图片，可用环境变量 KOMI_SRC_DEEPSEEK 覆盖）
输出：本项目 app/assets/（**不再指向 WorkBuddy 技能目录**）
"""
import os
from collections import deque

from PIL import Image, ImageFilter

# 素材源目录（生成时的本机路径；开源用户无需运行本工具——assets/ 已含全部成品。
# 如需重建：把原始生成图放入此目录，或用环境变量 KOMI_SRC_DEEPSEEK 覆盖）
SRC = os.environ.get(
    "KOMI_SRC_DEEPSEEK", os.path.expanduser(r"~\Pictures\deepseek 桌宠图片"))
# 输出到本项目内部（原先硬编码 WorkBuddy 技能目录，换机器必炸）
DST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
os.makedirs(DST, exist_ok=True)

TOL = 30          # 背景白判定容差（JPEG 噪点）
FEATHER = 1       # alpha 边缘羽化


def load(name):
    return Image.open(os.path.join(SRC, name)).convert("RGB")


def remove_bg(img):
    """从四边洪泛填充：只有与边缘相连的近白色才视为背景，角色内部白色保留。"""
    w, h = img.size
    px = img.load()
    bg = bytearray(w * h)          # 1 = 背景
    q = deque()

    def is_bg(x, y):
        r, g, b = px[x, y]
        return r > 255 - TOL and g > 255 - TOL and b > 255 - TOL

    for x in range(w):
        for y in (0, h - 1):
            if is_bg(x, y) and not bg[y * w + x]:
                bg[y * w + x] = 1
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if is_bg(x, y) and not bg[y * w + x]:
                bg[y * w + x] = 1
                q.append((x, y))
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not bg[ny * w + nx] and is_bg(nx, ny):
                bg[ny * w + nx] = 1
                q.append((nx, ny))

    out = img.convert("RGBA")
    op = out.load()
    for y in range(h):
        base = y * w
        for x in range(w):
            if bg[base + x]:
                op[x, y] = (0, 0, 0, 0)
    return out


def crop_to_content(img, pad=6):
    bbox = img.getbbox()
    if not bbox:
        return img
    l, t, r, b = bbox
    l = max(0, l - pad); t = max(0, t - pad)
    r = min(img.width, r + pad); b = min(img.height, b + pad)
    return img.crop((l, t, r, b))


def feather(img):
    """对 alpha 轻微模糊，消除 JPEG 去底后的锯齿白边。"""
    a = img.getchannel("A").filter(ImageFilter.GaussianBlur(FEATHER))
    a = a.point(lambda v: 0 if v < 24 else (255 if v > 250 else v))
    img.putalpha(a)
    return img


def process(src_name, out_base):
    img = load(src_name)
    print(f"{src_name}: 原始 {img.size}")
    img = remove_bg(img)
    img = crop_to_content(img)
    img = feather(img)
    print(f"  → 透明裁切后 {img.size}")
    img.save(os.path.join(DST, out_base + ".png"))
    img.transpose(Image.FLIP_LEFT_RIGHT).save(
        os.path.join(DST, out_base + "_f.png"))
    # 统计透明/不透明像素比例，验证去底成功
    a = img.getchannel("A")
    hist = a.histogram()
    transparent = sum(hist[:16]); opaque = sum(hist[240:])
    total = img.width * img.height
    print(f"  → 透明 {transparent / total:.1%} 不透明 {opaque / total:.1%}")


process("2026-09-06_194924(1).JPG", "pet_idle")     # 常态（文静）
process("2026-09-06_194924.JPG", "pet_happy")      # 开心（张嘴笑）
print("全部完成 →", DST)
