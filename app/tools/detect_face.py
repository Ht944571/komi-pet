# -*- coding: utf-8 -*-
"""detect_face.py — 从古见立绘像素中实测眼睛/脸颊锚点，生成 _eye_config.json (v2)。

原理：
  1. 皮肤掩膜：浅色暖调像素（脸/手/腿），用于圈定面部范围。
  2. 眼睛检测：深色连通域中，被皮肤包围、面积/长宽比合理的椭圆 → 眼睛。
  3. 脸颊锚点：优先检测已有的粉色红晕像素（happy 立绘自带）；
     否则按面部几何推算 —— 眼外缘与脸边缘之间的中点、眼底下方。
  4. 输出每状态 (idle/happy/pout) 的归一化 eyes + cheeks 坐标，
     并画调试图到 %TEMP%/wb_face_*.png 供目视核对。

用法： python detect_face.py [--write]   （--write 才落盘 _eye_config.json）
"""
import json
import os
import sys
import tempfile
from collections import deque

from PIL import Image, ImageDraw

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
# 6 表情 × 2 版本 = 12 状态（古见双版本：Q 版 + 高冷版）
STATES = (
    "idle", "happy", "shy", "pout", "blush", "stone",
    "alt_idle", "alt_happy", "alt_shy", "alt_pout", "alt_blush", "alt_stone",
)


def load_rgba(state):
    """Q 版 = pet_xxx.png；高冷版 = pet_alt_xxx.png（共用 load 函数）。"""
    if state.startswith("alt_"):
        path = os.path.join(ASSETS, f"pet_{state}.png")
    else:
        path = os.path.join(ASSETS, f"pet_{state}.png")
    img = Image.open(path).convert("RGBA")
    return img, path


def build_masks(img):
    """返回 (skin, iris, pink, body) 四个布尔二维表 + 尺寸。

    虹膜颜色在三张立绘中高度一致（采样：r 58-63 / g 40-50 / b 63-75），
    用它做特征色检测比深色连通域可靠得多（头发/描边全连成一片无法分离）。
    """
    w, h = img.size
    px = img.load()
    skin = [[False] * w for _ in range(h)]
    iris = [[False] * w for _ in range(h)]
    pink = [[False] * w for _ in range(h)]
    body = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 128:
                continue
            body[y][x] = True
            # 皮肤：亮、暖（R>G>=B 且 R 明显高于 B），排除纯白衣领
            if r > 228 and g > 195 and b > 185 and 6 <= r - b <= 60 and r - g <= 40:
                skin[y][x] = True
            # 虹膜特征色：两段
            #   ① Q 版：暗紫 (62,48,73)±8，b>r>g
            #   ② 高冷版：蓝紫 (47,57,93)±10，b>r
            if (50 <= r <= 72 and 34 <= g <= 58 and 56 <= b <= 84 and b > r > g) \
                    or (35 <= r <= 70 and 45 <= g <= 75 and 75 <= b <= 110 and b > r):
                iris[y][x] = True
            # 粉红红晕：R 显著高于 G/B 且整体偏亮
            if r > 215 and 90 < g < 200 and 110 < b < 210 and r - g > 45 and r - b > 25:
                pink[y][x] = True
    return skin, iris, pink, body, w, h


def components(mask, w, h, min_area):
    """4-邻接连通域，返回 [(area, x0, y0, x1, y1, cx, cy), ...] 按面积降序。"""
    seen = [[False] * w for _ in range(h)]
    out = []
    for y0 in range(h):
        row = mask[y0]
        for x0 in range(w):
            if not row[x0] or seen[y0][x0]:
                continue
            q = deque([(x0, y0)])
            seen[y0][x0] = True
            area = 0
            sx = sy = 0
            ax0 = ax1 = x0
            ay0 = ay1 = y0
            while q:
                x, y = q.popleft()
                area += 1
                sx += x
                sy += y
                ax0 = min(ax0, x)
                ax1 = max(ax1, x)
                ay0 = min(ay0, y)
                ay1 = max(ay1, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            if area >= min_area:
                out.append((area, ax0, ay0, ax1, ay1, sx / area, sy / area))
    out.sort(reverse=True)
    return out


def ring_skin_ratio(skin, comp, w, h):
    """连通域外扩 3px 的一圈环中皮肤占比（眼睛应被皮肤包围）。"""
    _, x0, y0, x1, y1, _, _ = comp
    pad = 3
    tot = skn = 0
    for y in range(max(0, y0 - pad), min(h, y1 + pad + 1)):
        for x in range(max(0, x0 - pad), min(w, x1 + pad + 1)):
            on_ring = (x < x0 or x > x1 or y < y0 or y > y1)
            if not on_ring:
                continue
            tot += 1
            if skin[y][x]:
                skn += 1
    return skn / tot if tot else 0.0


def detect_eyes(skin, iris, body, w, h, debug):
    """虹膜特征色连通域 → 两颗最大且一左一右的虹膜，外扩得到整眼 bbox。"""
    body_area = sum(sum(r) for r in body)
    cands = components(iris, w, h, min_area=int(body_area * 0.0008))
    eyes = []
    for comp in cands:
        area, x0, y0, x1, y1, cx, cy = comp
        cw, ch = x1 - x0 + 1, y1 - y0 + 1
        if ch == 0:
            continue
        aspect = cw / ch
        fill = area / (cw * ch)
        # 虹膜：近圆/竖椭圆/眯眼扁椭圆、填充率高、占身体面积 0.1%~8%
        if not (0.50 <= aspect <= 3.3):
            continue
        if fill < 0.50:
            continue
        if not (0.001 <= area / body_area <= 0.08):
            continue
        eyes.append(comp)
        debug.append(("iris", comp, 0))
        if len(eyes) >= 8:
            break
    eyes.sort(key=lambda c: -c[0])
    pair = []
    for c in eyes:
        if not pair:
            pair.append(c)
            continue
        # 第二颗：与第一颗明显水平分离（两眼间距 > 虹膜直径一半）
        if abs(c[5] - pair[0][5]) > (pair[0][3] - pair[0][1]) * 0.6:
            pair.append(c)
            break
    if len(pair) < 2:
        return pair
    pair.sort(key=lambda c: c[5])          # 左、右
    # 虹膜只是眼睛核心（瞳孔+底色），整眼（含高光/下缘）向外扩并略下移：
    # 实测 Q 版立绘眼睛比虹膜连通域宽约 18%、高约 35%，中心偏下 ~4%
    out = []
    for area, x0, y0, x1, y1, cx, cy in pair:
        cw, ch = x1 - x0 + 1, y1 - y0 + 1
        exw, exh = cw * 1.18, ch * 1.35
        cy += ch * 0.04
        out.append((area, cx - exw / 2, cy - exh / 2,
                    cx + exw / 2, cy + exh / 2, cx, cy))
    return out


def skin_coverage(skin, w_img, h_img, cx, cy, ew, eh):
    """椭圆区域内皮肤占比（采样 ~180 点），用于把腮红收缩到脸颊皮肤内。"""
    import math as _m
    tot = hit = 0
    rx, ry = ew / 2, max(1.0, eh / 2)
    for i in range(16):
        for j in range(12):
            dx = -1.0 + 2.0 * (i + 0.5) / 16
            dy = -1.0 + 2.0 * (j + 0.5) / 12
            if dx * dx + dy * dy > 1.0:
                continue
            x = int(cx + dx * rx)
            y = int(cy + dy * ry)
            if 0 <= x < w_img and 0 <= y < h_img:
                tot += 1
                if skin[y][x]:
                    hit += 1
    return hit / tot if tot else 0.0


def detect_cheeks(skin, pink, eyes, w, h, debug):
    """每侧脸颊：优先粉色红晕连通域；否则按「眼外缘—脸边缘」几何推算，
    再用皮肤覆盖率迭代收缩，确保腮红椭圆落在脸颊皮肤内。"""
    pinks = components(pink, w, h, min_area=60)
    cheeks = {}
    used = set()
    for side, eye in zip(("left", "right"), eyes):
        ex, ey = eye[5], eye[6]
        ew, eh = eye[3] - eye[1] + 1, eye[4] - eye[2] + 1
        best = None
        best_i = -1
        for i, comp in enumerate(pinks):
            if i in used:
                continue
            cx, cy = comp[5], comp[6]
            # 红晕应在眼的外侧下方不远处
            outward = (cx < ex) if side == "left" else (cx > ex)
            if not outward:
                continue
            if abs(cx - ex) > ew * 2.5 or cy < ey or cy > ey + eh * 2.2:
                continue
            if best is None or comp[0] > best[0]:
                best = comp
                best_i = i
        if best is not None and best[0] >= 2000:
            # 只有大面积红晕才是立绘自带腮红（小连通域多为耳朵/阴影误检）
            used.add(best_i)
            cheeks[side] = best
            debug.append(("cheek-pink", best, 0))
    # 缺的一侧（或两侧）：在「眼外下方」候选网格中找皮肤覆盖率最高的锚点，
    # 再迭代收缩贴合。不依赖脸缘行扫描（耳朵/头发会污染）。
    for side, eye in zip(("left", "right"), eyes):
        if side in cheeks:
            continue
        ex, ey = eye[5], eye[6]
        ew, eh = eye[3] - eye[1] + 1, eye[4] - eye[2] + 1
        sign = -1.0 if side == "left" else 1.0
        cw0, ch0 = ew * 0.52, eh * 0.36
        best = None  # (cov, cx, cy)
        for fx in (0.18, 0.30, 0.42, 0.05):
            for fy in (0.70, 0.88, 1.06, 1.24):
                cx = ex + sign * ew * fx
                cy = ey + eh * fy
                cov = skin_coverage(skin, w, h, cx, cy, cw0, ch0)
                # 同等覆盖率下偏好更靠外、更靠下的自然腮红位
                score = cov + fx * 0.02 + fy * 0.01
                if best is None or score > best[0]:
                    best = (score, cov, cx, cy)
        if best is None:
            continue
        _, cov, cx, cy = best
        cw, ch = cw0, ch0
        for _ in range(6):
            if cov >= 0.60:
                break
            cw *= 0.88
            ch *= 0.94
            cov = skin_coverage(skin, w, h, cx, cy, cw, ch)
        comp = (int(cw * ch), cx - cw / 2, cy - ch / 2,
                cx + cw / 2, cy + ch / 2, cx, cy)
        cheeks[side] = comp
        debug.append(("cheek-geo", comp, cov))
    return [cheeks.get("left"), cheeks.get("right")]


def to_norm(comp, w, h, scale_w=1.0, scale_h=1.0):
    _, x0, y0, x1, y1, cx, cy = comp
    return {
        "cx": round(cx / w, 4),
        "cy": round(cy / h, 4),
        "w": round((x1 - x0 + 1) / w * scale_w, 4),
        "h": round((y1 - y0 + 1) / h * scale_h, 4),
    }


def avg_skin_color(img, skin, w, h):
    """皮肤掩膜像素的平均色 → 眨眼盖板用（让盖板与脸色一致）。"""
    px = img.load()
    n = sr = sg = sb = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if skin[y][x]:
                r, g, b, a = px[x, y]
                sr += r
                sg += g
                sb += b
                n += 1
    if not n:
        return "#FFF2EA"
    return f"#{sr // n:02X}{sg // n:02X}{sb // n:02X}"


def main():
    write = "--write" in sys.argv
    out = {"version": 2, "eyelid_color": "#2A2438", "fallback": "idle", "states": {}}
    tmp = tempfile.gettempdir()
    skin_colors = []
    for state in STATES:
        img, path = load_rgba(state)
        skin, iris, pink, body, w, h = build_masks(img)
        skin_colors.append(avg_skin_color(img, skin, w, h))
        debug = []
        eyes = detect_eyes(skin, iris, body, w, h, debug)
        if len(eyes) < 2:
            print(f"[{state}] 眼睛检测失败（只找到 {len(eyes)} 颗），跳过")
            continue
        cheeks = detect_cheeks(skin, pink, eyes, w, h, debug)
        entry = {
            "eyes": {
                "left": to_norm(eyes[0], w, h),
                "right": to_norm(eyes[1], w, h),
            },
        }
        if all(cheeks):
            entry["cheeks"] = {
                "left": to_norm(cheeks[0], w, h, 1.35, 0.85),
                "right": to_norm(cheeks[1], w, h, 1.35, 0.85),
            }
        out["states"][state] = entry
        print(f"[{state}] {w}x{h}")
        print("  eye L:", entry["eyes"]["left"])
        print("  eye R:", entry["eyes"]["right"])
        if "cheeks" in entry:
            print("  chk L:", entry["cheeks"]["left"])
            print("  chk R:", entry["cheeks"]["right"])
        # 调试图
        dbg = img.copy()
        dr = ImageDraw.Draw(dbg)
        for comp in eyes:
            dr.rectangle([comp[1], comp[2], comp[3], comp[4]], outline=(255, 0, 0, 255), width=4)
        for comp in cheeks:
            if comp:
                dr.rectangle([comp[1], comp[2], comp[3], comp[4]], outline=(0, 160, 255, 255), width=4)
        dbg_path = os.path.join(tmp, f"wb_face_{state}.png")
        dbg.save(dbg_path)
        print("  debug:", dbg_path)
    out["skin_color"] = max(set(skin_colors), key=skin_colors.count) \
        if skin_colors else "#FFF2EA"
    out["notes"] = "v2：detect_face.py 像素实测（皮肤掩膜+虹膜特征色+红晕检测），分状态；_f 镜像由代码翻转。"
    if write:
        dst = os.path.join(ASSETS, "_eye_config.json")
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("written:", dst)
    else:
        print("(dry-run，加 --write 落盘)")


if __name__ == "__main__":
    main()
