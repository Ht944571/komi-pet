# -*- coding: utf-8 -*-
r"""一次性检测立绘的眼部位置与主色，写入 _eye_config.json。

运行时 wb_whale_win 若该文件不存在会自动调用本脚本（若 PIL 可用）；
手动重新生成：`python tools/detect_eyes.py`。
"""
import json
import os
import sys
from collections import deque

try:
    from PIL import Image
except ImportError:
    print("需要 Pillow（pip install pillow）", file=sys.stderr)
    sys.exit(1)


HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.normpath(os.path.join(HERE, "..", "assets"))
OUT = os.path.join(ASSETS, "_eye_config.json")


def _detect(img_path, downsample=6, dark_r=80, dark_g=80, dark_b=100, alpha_min=100):
    im = Image.open(img_path).convert("RGBA")
    w, h = im.size
    sw, sh = w // downsample, h // downsample
    small = im.resize((sw, sh), Image.LANCZOS)
    px = small.load()
    mask = [[0] * sw for _ in range(sh)]
    for y in range(sh):
        for x in range(sw):
            r, g, b, a = px[x, y]
            if a >= alpha_min and r < dark_r and g < dark_g and b < dark_b:
                mask[y][x] = 1
    seen = [[False] * sw for _ in range(sh)]
    comps = []
    for y in range(sh):
        for x in range(sw):
            if mask[y][x] and not seen[y][x]:
                q = deque([(x, y)])
                seen[y][x] = True
                cells = []
                while q:
                    cx, cy = q.popleft()
                    cells.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < sw and 0 <= ny < sh and mask[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            q.append((nx, ny))
                if len(cells) >= 6:
                    xs = [c[0] for c in cells]
                    ys = [c[1] for c in cells]
                    cw = max(xs) - min(xs) + 1
                    ch = max(ys) - min(ys) + 1
                    if 0.04 <= cw / sw <= 0.20 and 0.04 <= ch / sh <= 0.15:
                        comps.append((
                            len(cells),
                            (sum(xs) / len(xs)) / sw,
                            (sum(ys) / len(ys)) / sh,
                            cw / sw, ch / sh,
                        ))
    comps.sort(key=lambda c: abs(c[1] - 0.5))  # 按与中轴的距离升序
    if len(comps) < 2:
        return None
    # 取最靠近中轴、且横向距离相近的一对（左右眼）
    pairs = []
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            a, b = comps[i], comps[j]
            if a[1] >= 0.5 or b[1] <= 0.5:
                continue
            # 眼间距应近似
            if abs(a[2] - b[2]) < 0.05 and abs(a[3] - b[3]) < 0.04:
                pairs.append((a, b))
                if len(pairs) >= 1:
                    break
        if pairs:
            break
    if not pairs:
        return None
    left, right = pairs[0]  # type: ignore
    return {
        "left":  {"cx": left[1],  "cy": left[2],  "w": left[3],  "h": left[4]},
        "right": {"cx": right[1], "cy": right[2], "w": right[3], "h": right[4]},
    }


def _sample_eye_color(img_path, ratio):
    """在眼睛中心采样 iris 主色（alpha 加权 RGB 平均）。"""
    im = Image.open(img_path).convert("RGBA")
    w, h = im.size
    cx = int(ratio["cx"] * w)
    cy = int(ratio["cy"] * h)
    r0, r1 = max(0, cx - 8), min(w, cx + 8)
    c0, c1 = max(0, cy - 6), min(h, cy + 6)
    crop = im.crop((r0, c0, r1, c1))
    rs, gs, bs, aw = 0, 0, 0, 0
    for pr, pg, pb, pa in crop.getdata():
        if pa < 60:
            continue
        rs += pr * pa
        gs += pg * pa
        bs += pb * pa
        aw += pa
    if aw == 0:
        return "#3B6D11"
    return "#{:02X}{:02X}{:02X}".format(rs // aw, gs // aw, bs // aw)


def main_():
    cfg = {}
    src = os.path.join(ASSETS, "pet_idle.png")
    eyes = _detect(src)
    if not eyes:
        print("未检测到眼睛，已写入空配置（blink/eyelid 将禁用）")
        cfg = {"eyes": None}
    else:
        cfg = {"eyes": eyes}
        cfg["left_eye_color"] = _sample_eye_color(src, eyes["left"])
        cfg["right_eye_color"] = _sample_eye_color(src, eyes["right"])
        print("L", eyes["left"])
        print("R", eyes["right"])
        print("color", cfg["left_eye_color"], cfg["right_eye_color"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("→", OUT)


if __name__ == "__main__":
    main_()