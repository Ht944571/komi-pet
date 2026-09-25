# -*- coding: utf-8 -*-
r"""calib_face.py — 眼部/脸颊锚点的目视标定工具（网格叠加 → 人工读数 → 校验图）。

为什么需要它：detect_face.py 的虹膜特征色检测对高冷版（全身立绘、头部占比小、
每张姿势不同、刘海投影压眼）失效率过高（6 态里 4 态检不出、检出的也有 junk）。
项目已有先例是「目视标定」（配件 pivot_y / ratio 就是这么标的），本工具把
眼部锚点纳入同一方法论：

  1) --grid STATE   ：立绘叠加归一化网格（0.05 细线 / 0.1 粗线带标注）出图，
                      人工读出眼缝框 (cx, cy, w, h) 与（alt 的）腮红框。
  2) 读数写进 tools/_eye_calib.json。
  3) --verify       ：把标定框画回立绘（2× 头部裁切），逐状态目视核对。
  4) --write        ：生成 assets/_eye_config.json v4：
                      · 有标定的状态用标定值（可见眼缝约定：框 = 上下睑缘之间的
                        可见暗区，眨眼行程直接发生在其上——部分眨眼才可见）
                      · 未标定状态沿用 v3 值（Q 版 6 态已真机验证）
                      · 每状态自动采样 lid_skin（眼底下方皮肤带均值，盖板用，
                        解决"平涂亮肤色盖板"与发影区不融合的问题）

用法：
  python tools/calib_face.py --grid alt_idle [--crop 0.30,0.10,0.75,0.40] [--zoom 3]
  python tools/calib_face.py --verify [STATE ...]
  python tools/calib_face.py --write
"""
import argparse
import json
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
ASSETS = os.path.join(APP, "assets")
CALIB_FILE = os.path.join(HERE, "_eye_calib.json")
OUT_DIR = os.path.join(os.path.dirname(APP), "docs", "face_calib")

STATES = (
    "idle", "happy", "shy", "pout", "blush", "stone",
    "alt_idle", "alt_happy", "alt_shy", "alt_pout", "alt_blush", "alt_stone",
)


def load(state):
    return Image.open(os.path.join(ASSETS, f"pet_{state}.png")).convert("RGBA")


def skin_mask(img):
    """与 detect_face.py 同判据的皮肤掩膜（用于 lid_skin 采样）。"""
    w, h = img.size
    px = img.load()
    m = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a >= 128 and r > 228 and g > 195 and b > 185 \
                    and 6 <= r - b <= 60 and r - g <= 40:
                m[y][x] = True
    return m


def draw_grid(img, zoom=2.0, crop=None):
    """叠加归一化网格：每 0.05 一条线、每条都带大号标注（小标签读错行 = 标定翻车）。"""
    w, h = img.size
    if crop:
        cx0, cy0, cx1, cy1 = crop
        box = (int(cx0 * w), int(cy0 * h), int(cx1 * w), int(cy1 * h))
    else:
        box = (0, 0, w, h)
    region = img.crop(box)
    rw, rh = region.size
    region = region.resize((int(rw * zoom), int(rh * zoom)), Image.LANCZOS)
    dr = ImageDraw.Draw(region)
    zw, zh = region.size
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", max(14, int(zh * 0.022)))
    except Exception:
        from PIL import ImageFont
        font = ImageFont.load_default()
    fx0, fy0 = box[0] / w, box[1] / h
    fx1, fy1 = box[2] / w, box[3] / h
    v = round(fx0 * 20) / 20
    while v <= fx1 + 1e-9:
        x = (v - fx0) / (fx1 - fx0) * zw
        dr.line([(x, 0), (x, zh)], fill=(255, 60, 60, 210), width=1)
        dr.text((x + 2, 2), f"{v:.2f}", fill=(255, 30, 30, 255), font=font)
        dr.text((x + 2, zh - 16), f"{v:.2f}", fill=(255, 30, 30, 255), font=font)
        v += 0.05
    u = round(fy0 * 20) / 20
    while u <= fy1 + 1e-9:
        y = (u - fy0) / (fy1 - fy0) * zh
        dr.line([(0, y), (zw, y)], fill=(60, 110, 255, 210), width=1)
        dr.text((2, y + 2), f"{u:.2f}", fill=(20, 60, 255, 255), font=font)
        dr.text((zw - 52, y + 2), f"{u:.2f}", fill=(20, 60, 255, 255), font=font)
        u += 0.05
    return region


def cmd_grid(args):
    os.makedirs(OUT_DIR, exist_ok=True)
    crop = tuple(float(v) for v in args.crop.split(",")) if args.crop else None
    img = load(args.state)
    out = draw_grid(img, args.zoom, crop)
    p = os.path.join(OUT_DIR, f"grid_{args.state}.png")
    out.save(p)
    print(f"  ✓ {p}  ({out.width}x{out.height}, sprite {img.size[0]}x{img.size[1]})")


def box_from_norm(img, e):
    w, h = img.size
    return ((e["cx"] - e["w"] / 2) * w, (e["cy"] - e["h"] / 2) * h,
            (e["cx"] + e["w"] / 2) * w, (e["cy"] + e["h"] / 2) * h)


def cmd_verify(args):
    os.makedirs(OUT_DIR, exist_ok=True)
    calib = json.load(open(CALIB_FILE, encoding="utf-8")) if os.path.isfile(CALIB_FILE) else {}
    states = args.states or STATES
    for st in states:
        img = load(st)
        base = img.copy()
        dr = ImageDraw.Draw(base)
        ent = calib.get(st, {})
        for key, e in ((ent.get("eyes") or {}).items()):
            dr.rectangle(box_from_norm(img, e), outline=(255, 0, 0, 255), width=3)
        for key, e in ((ent.get("cheeks") or {}).items()):
            dr.rectangle(box_from_norm(img, e), outline=(0, 160, 255, 255), width=3)
        # 头部大致在 sprites 上半 → 裁上半身放大看
        w, h = img.size
        head = base.crop((0, 0, w, int(h * args.head_frac)))
        zoom = args.zoom
        head = head.resize((int(head.width * zoom), int(head.height * zoom)), Image.LANCZOS)
        p = os.path.join(OUT_DIR, f"verify_{st}.png")
        head.save(p)
        n_e = len(ent.get("eyes") or {})
        n_c = len(ent.get("cheeks") or {})
        print(f"  ✓ {p}  eyes={n_e} cheeks={n_c}")


def sample_lid_skin(img, mask, e):
    """眼底下方皮肤带均值色（盖板色）。采样不到 → None（运行时回退）。"""
    w, h = img.size
    px = img.load()
    x0 = int((e["cx"] - e["w"] * 0.40) * w)
    x1 = int((e["cx"] + e["w"] * 0.40) * w)
    y0 = int((e["cy"] + e["h"] * 0.55) * h)
    y1 = int((e["cy"] + e["h"] * 1.05) * h)
    n = sr = sg = sb = 0
    for y in range(max(0, y0), min(h, y1)):
        for x in range(max(0, x0), min(w, x1)):
            if mask[y][x]:
                r, g, b, a = px[x, y]
                sr += r
                sg += g
                sb += b
                n += 1
    if n < 12:
        return None
    return f"#{sr // n:02X}{sg // n:02X}{sb // n:02X}"


# ---------------- 自动检测（sclera / 暗色眼缝） ----------------

def _components(mask, w, h, min_area, x_lim=None, y_lim=None):
    """4-邻接连通域，返回 [(area, x0, y0, x1, y1, cx, cy), ...] 按面积降序。"""
    from collections import deque
    seen = [[False] * w for _ in range(h)]
    x_lo, x_hi = x_lim or (0, w)
    y_lo, y_hi = y_lim or (0, h)
    x_hi, y_hi = min(x_hi, w), min(y_hi, h)     # 区域钳制（越界一个像素即崩）
    out = []
    for y0 in range(y_lo, y_hi):
        for x0 in range(x_lo, x_hi):
            if not mask[y0][x0] or seen[y0][x0]:
                continue
            q = deque([(x0, y0)])
            seen[y0][x0] = True
            area = 0
            ax0 = ax1 = x0
            ay0 = ay1 = y0
            while q:
                x, y = q.popleft()
                area += 1
                ax0 = min(ax0, x)
                ax1 = max(ax1, x)
                ay0 = min(ay0, y)
                ay1 = max(ay1, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if x_lo <= nx < x_hi and y_lo <= ny < y_hi \
                            and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            if area >= min_area:
                out.append((area, ax0, ay0, ax1, ay1,
                            (ax0 + ax1) / 2, (ay0 + ay1) / 2))
    out.sort(reverse=True)
    return out


def _norm_box(bb, w, h):
    x0, y0, x1, y1 = bb
    return {"cx": round((x0 + x1) / 2 / w, 4), "cy": round((y0 + y1) / 2 / h, 4),
            "w": round((x1 - x0 + 1) / w, 4), "h": round((y1 - y0 + 1) / h, 4)}


def _face_region(img, skin):
    """脸 = 皮肤连通域里「够大且最靠上」的那块（头在所有皮肤之上；
    最大块可能是手/腿——alt_blush 实测翻车过）。返回其 bbox。"""
    w, h = img.size
    comps = _components(skin, w, h, min_area=int(w * h * 0.004))
    if not comps:
        return (0, 0, w, int(h * 0.45))
    top = max(comps[0][0] * 0.25, w * h * 0.004)
    cands = [c for c in comps if c[0] >= top]
    _, x0, y0, x1, y1, _, _ = min(cands, key=lambda c: c[6])
    return (x0, y0, x1, y1)


def auto_alt_state(state):
    """高冷版：白色巩膜是强信号（Q 版没有）。巩膜对 → 与相邻暗色（睫线+虹膜）
    合并 = 眼缝框；粉色红晕 → 腮红框。"""
    img = load(state)
    w, h = img.size
    px = img.load()
    skin = skin_mask(img)                       # 复用统一判据（别传空掩膜）
    white = [[False] * w for _ in range(h)]
    pink = [[False] * w for _ in range(h)]
    dark = [[False] * w for _ in range(h)]
    fx0, fy0, fx1, fy1 = _face_region(img, skin)
    for y in range(fy0, min(fy1 + 1, h)):
        for x in range(fx0, min(fx1 + 1, w)):
            r, g, b, a = px[x, y]
            if a < 128:
                continue
            mn, mx = min(r, g, b), max(r, g, b)
            # 巩膜是「中性白」：高光皮肤（r-b≈20-60 的暖白）必须排除，
            # 否则脸部高光把整片连成一个巨块（实测翻车过）
            if mn > 195 and mx - mn <= 26 and abs(r - b) <= 12:
                white[y][x] = True
            if r > 205 and 90 < g < 205 and 110 < b < 215 and r - g > 40:
                pink[y][x] = True                     # 红晕
            if r + g + b < 330:                        # 睫线/虹膜深色
                dark[y][x] = True
    wc = _components(white, w, h, min_area=max(24, int((fx1 - fx0) * (fy1 - fy0) * 0.002)),
                     x_lim=(fx0, fx1 + 1), y_lim=(fy0, fy1 + 1))
    # 挑一对：y 接近、面积相近、水平分离
    pair = None
    for i in range(min(6, len(wc))):
        for j in range(i + 1, min(8, len(wc))):
            a1, x01, y01, x11, y11, c1, _ = wc[i]
            a2, x02, y02, x12, y12, c2, _ = wc[j]
            if not (0.45 < a1 / max(1, a2) < 2.2):
                continue
            # 歪头/3-4 侧脸双眼 y 差可以很大（alt_happy 实测），只挡"明显不同排"
            if abs((y01 + y11) / 2 - (y02 + y12) / 2) > 0.5 * max(y11 - y01, y12 - y02) + 14:
                continue
            sep = abs(c1 - c2)
            wmean = ((x11 - x01) + (x12 - x02)) / 2
            if sep < wmean * 0.9:
                continue
            pair = (wc[i], wc[j])
            break
        if pair:
            break
    if not pair:
        return None
    eyes = {}
    for side, comp in zip(("left", "right"), sorted(pair, key=lambda c: c[5])):
        _, sx0, sy0, sx1, sy1, _, _ = comp
        # 巩膜外扩 40% 找相邻暗色（睫线在上方、虹膜在下溢出）
        mx0 = max(fx0, int(sx0 - (sx1 - sx0) * 0.40))
        mx1 = min(fx1, int(sx1 + (sx1 - sx0) * 0.40) + 1)
        my0 = max(fy0, int(sy0 - (sy1 - sy0) * 0.85))
        my1 = min(fy1, int(sy1 + (sy1 - sy0) * 0.85) + 1)
        merged = [[False] * w for _ in range(h)]
        for y in range(my0, my1):
            for x in range(mx0, mx1):
                if white[y][x] or dark[y][x]:
                    merged[y][x] = True
        mc = _components(merged, w, h, min_area=12, x_lim=(mx0, mx1), y_lim=(my0, my1))
        if not mc:
            continue
        _, ax0, ay0, ax1, ay1, _, _ = mc[0]
        eyes[side] = _norm_box((ax0, ay0, ax1, ay1), w, h)
    if len(eyes) < 2:
        return None
    out = {"eyes": eyes}
    # 腮红：眼外下方的粉色连通域（最大的两块，按左右分）
    pc = _components(pink, w, h, min_area=60, x_lim=(fx0, fx1 + 1), y_lim=(fy0, fy1 + 1))
    if len(pc) >= 2:
        lps = [c for c in pc if c[5] < (eyes["left"]["cx"] + eyes["right"]["cx"]) / 2 * w]
        rps = [c for c in pc if c[5] >= (eyes["left"]["cx"] + eyes["right"]["cx"]) / 2 * w]
        cheeks = {}
        for side, lst in (("left", lps), ("right", rps)):
            if lst:
                _, bx0, by0, bx1, by1, _, _ = lst[0]
                cheeks[side] = _norm_box((bx0, by0, bx1, by1), w, h)
        if len(cheeks) == 2:
            out["cheeks"] = cheeks
    return out


def auto_q_state(state, old_eyes):
    """Q 版：在 v3 框（含少量余量）内找最大暗色连通域 = 睫线+虹膜 = 眼缝。"""
    img = load(state)
    w, h = img.size
    px = img.load()
    eyes = {}
    for side, e in old_eyes.items():
        x0 = int((e["cx"] - e["w"] * 0.62) * w)
        x1 = int((e["cx"] + e["w"] * 0.62) * w)
        y0 = int((e["cy"] - e["h"] * 0.75) * h)
        y1 = int((e["cy"] + e["h"] * 0.85) * h)
        dark = [[False] * w for _ in range(h)]
        for y in range(max(0, y0), min(h, y1 + 1)):
            for x in range(max(0, x0), min(w, x1 + 1)):
                r, g, b, a = px[x, y]
                if a >= 128 and r + g + b < 340:
                    dark[y][x] = True
        comps = _components(dark, w, h, min_area=20,
                            x_lim=(max(0, x0), min(w, x1 + 1)),
                            y_lim=(max(0, y0), min(h, y1 + 1)))
        if not comps:
            return None
        area, ax0, ay0, ax1, ay1, _, _ = comps[0]
        bw, bh = (x1 - x0), (y1 - y0)
        if area < 0.10 * bw * bh:      # 暗域太小 → 框可能根本不在眼上
            return None
        eyes[side] = _norm_box((ax0, ay0, ax1, ay1), w, h)
    return {"eyes": eyes} if len(eyes) == 2 else None


def cmd_auto(args):
    """自动检测并写入标定文件（Q 版以 v3 框为搜索区，alt 全自动）。"""
    calib = json.load(open(CALIB_FILE, encoding="utf-8")) if os.path.isfile(CALIB_FILE) else {}
    old = json.load(open(os.path.join(ASSETS, "_eye_config.json"), encoding="utf-8"))
    for st in STATES:
        try:
            if st.startswith("alt_"):
                ent = auto_alt_state(st)
            else:
                v3 = (old.get("states") or {}).get(st) or {}
                if not v3.get("eyes"):
                    continue
                ent = auto_q_state(st, v3["eyes"])
        except Exception as e:
            print(f"[{st}] auto 失败：{e}")
            continue
        if not ent:
            print(f"[{st}] auto 未检出（保留原值）")
            continue
        calib[st] = ent
        L, R = ent["eyes"]["left"], ent["eyes"]["right"]
        print(f"[{st}] L(cx{L['cx']:.3f},cy{L['cy']:.3f},w{L['w']:.3f},h{L['h']:.3f}) "
              f"R(cx{R['cx']:.3f},cy{R['cy']:.3f},w{R['w']:.3f},h{R['h']:.3f})"
              + (" +cheeks" if "cheeks" in ent else ""))
    with open(CALIB_FILE, "w", encoding="utf-8") as f:
        json.dump(calib, f, ensure_ascii=False, indent=2)
    print("calib written:", CALIB_FILE)


def cmd_write(args):
    calib = json.load(open(CALIB_FILE, encoding="utf-8")) if os.path.isfile(CALIB_FILE) else {}
    old = json.load(open(os.path.join(ASSETS, "_eye_config.json"), encoding="utf-8"))
    out = {"version": 4, "eyelid_color": old.get("eyelid_color", "#2A2438"),
           "fallback": "idle", "states": {}}
    for st in STATES:
        img = load(st)
        mask = skin_mask(img)
        ent = dict((old.get("states") or {}).get(st) or {})
        cal = calib.get(st) or {}
        if cal.get("eyes"):
            ent["eyes"] = cal["eyes"]
        if cal.get("cheeks"):
            ent["cheeks"] = cal["cheeks"]
        if not ent.get("eyes"):
            print(f"[{st}] 无 eyes（标定与 v3 都没有），跳过")
            continue
        lids = []
        for key in ("left", "right"):
            e = (ent.get("eyes") or {}).get(key)
            if e:
                c = sample_lid_skin(img, mask, e)
                if c:
                    lids.append(c)
        if lids:
            # 双眼取较暗的一个（盖板偏向发影档，避免"亮胶带"）
            ent["lid_skin"] = min(lids, key=lambda c: int(c[1:3], 16) + int(c[3:5], 16) + int(c[5:7], 16))
        out["states"][st] = ent
        print(f"[{st}] eyes={'calib' if cal.get('eyes') else 'v3'} "
              f"cheeks={'calib' if cal.get('cheeks') else ('v3' if ent.get('cheeks') else '-')} "
              f"lid_skin={ent.get('lid_skin')}")
    out["notes"] = ("v4：alt 6 态眼部/腮红锚点经 calib_face.py 网格目视标定（可见眼缝约定："
                    "框=上下睑缘之间的可见暗区，眨眼行程发生在其上）；Q 版沿用 v3 实测值；"
                    "lid_skin 为眼底皮肤带实测均值（眨眼盖板色，与发影融合）。")
    dst = os.path.join(ASSETS, "_eye_config.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("written:", dst)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grid")
    g.add_argument("state")
    g.add_argument("--crop", default="")
    g.add_argument("--zoom", type=float, default=2.0)
    v = sub.add_parser("verify")
    v.add_argument("states", nargs="*")
    v.add_argument("--zoom", type=float, default=2.0)
    v.add_argument("--head-frac", type=float, default=0.45)
    w = sub.add_parser("write")
    a = sub.add_parser("auto")
    a.add_argument("--states", nargs="*", default=None)
    args = ap.parse_args()
    if args.cmd == "grid":
        cmd_grid(args)
    elif args.cmd == "verify":
        cmd_verify(args)
    elif args.cmd == "auto":
        cmd_auto(args)
    else:
        cmd_write(args)


if __name__ == "__main__":
    main()
