# -*- coding: utf-8 -*-
"""把旧 `assets/_eye_config.json` 的眼/腮红锚点，精确换算到 v3 新立绘坐标系。

原理（为什么可以精确换算，而不是重新标定）：
  新旧两条管线**共用同一次「洪泛去底」**，之后只差两点：
    ① `crop_to_content` 的 pad：旧 6px、新 4px   → 内容原点相差 2px（有时被边界钳制）
    ② 新管线多一步「按脸宽缩放 s」+ 贴到统一画布的偏移 paste_xy
  所以映射是纯仿射：
      旧立绘坐标 --(+旧内容原点)--> 去底图坐标 --(-新内容原点, ×s, +paste)--> 新立绘坐标
  旧锚点是**在旧立绘上标定的**，而这 6 个态的源图没换 → 换算等价于重新标定，且无误差。

  · `joy` 眼睛本就是闭的 → 不需要锚点
  · `surprise` 是 image-to-image 自 idle 源图生成的，实测 content/scale/paste 与 idle 完全一致
    → 直接继承 idle 换算后的锚点

用法：
    python tools/remap_eye_config_v3.py            # 换算 + 出核验图
"""
import json
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_pet_v3 as B                  # noqa: E402  复用去底/裁切，保证与构建同源

ASSETS = os.path.join(APP, "assets")
V3 = os.path.join(ASSETS, "pet_v3")
OLD_CFG = os.path.join(ASSETS, "_eye_config.json")
META = os.path.join(V3, "_build_meta.json")
OUT_CFG = os.path.join(V3, "_eye_config.json")
DOCS = os.path.join(os.path.dirname(APP), "docs", "face_calib_v3")

OLD_PAD, NEW_PAD = 6, 4


def content_origin(path, pad):
    """返回 (ox, oy, size)：与 build_pet_v3 / make_sprites_komi 同源的重算。"""
    pre = B.load_rgba(path)
    if pre is not None:
        bg = pre
    else:
        bg = B.remove_bg(Image.open(path))
    bbox = bg.getbbox()
    W, H = bg.size
    L, T, R, Bo = bbox
    ox, oy = max(0, L - pad), max(0, T - pad)
    ox1, oy1 = min(W, R + pad), min(H, Bo + pad)
    return ox, oy, (ox1 - ox, oy1 - oy)


def box_edges(box, w, h):
    """归一化框 → 像素边 (x0,y0,x1,y1)，采用与运行时一致的「cx,cy,w,h」。"""
    return ((box["cx"] - box["w"] / 2) * w, (box["cy"] - box["h"] / 2) * h,
            (box["cx"] + box["w"] / 2) * w, (box["cy"] + box["h"] / 2) * h)


def to_norm(x0, y0, x1, y1, W, H):
    return {"cx": round((x0 + x1) / 2 / W, 4), "cy": round((y0 + y1) / 2 / H, 4),
            "w": round((x1 - x0) / W, 4), "h": round((y1 - y0) / H, 4)}


def main():
    meta = json.load(open(META, encoding="utf-8"))
    old = json.load(open(OLD_CFG, encoding="utf-8"))
    old_states = old["states"]

    new_cfg = {"version": 4, "eyelid_color": old.get("eyelid_color", "#2A2438"),
               "fallback": old.get("fallback", "idle"), "states": {}}
    report = []

    for state, m in meta.items():
        af = m.get("anchor_from")
        if not af:
            # 该态眼睛本就是闭的（joy）→ 显式标 skip，运行时不画眼睑（也不回退到 idle）
            new_cfg["states"][state] = {"skip": True}
            report.append(f"{state:10} eye_config = skip（{af}）——眼睛本就是闭的，无需眼睑")
            continue
        if af not in old_states:
            report.append(f"{state:10} 旧配置里没有 {af} → 跳过")
            continue
        # 旧态用的源图 = 当前态的源图（对 surprise 是 idle 的源图）
        src = m["source"] if af == state else json.load(open(META, encoding="utf-8"))[af]["source"]
        # surprise 与 idle 的几何完全一致 → 直接沿用 idle 的换算结果
        if state == "surprise":
            src = meta["idle"]["source"]

        ox, oy, old_size = content_origin(src, OLD_PAD)
        nx, ny, new_content = content_origin(src, NEW_PAD)
        s = m["scale"]
        px, py = m["paste_xy"]
        CW, CH = m["canvas_size"]

        committed = os.path.join(ASSETS, f"pet_{af}.png")
        cs = Image.open(committed).size if os.path.isfile(committed) else None
        ok = (cs == old_size)
        report.append(f"{state:10} ← 旧 {af:8} 源 {os.path.basename(src)}")
        report.append(f"           旧立绘实盘 {cs}  重算 {old_size}  {'✓一致' if ok else '✗不一致(换算可能偏)'}")
        report.append(f"           内容原点 旧({ox},{oy}) 新({nx},{ny})  新内容 {new_content}  ×{s:.3f}  贴({px},{py})  画布 {CW}x{CH}")

        ow, oh = (cs if cs else old_size)
        entry = {}
        for key in ("eyes", "cheeks"):
            if key not in old_states[af]:
                continue
            out = {}
            for side, b in old_states[af][key].items():
                x0, y0, x1, y1 = box_edges(b, ow, oh)
                # 旧立绘坐标 → 去底图坐标
                X0, X1 = x0 + ox, x1 + ox
                Y0, Y1 = y0 + oy, y1 + oy
                # → 新内容坐标 → 画布坐标
                X0 = (X0 - nx) * s + px
                X1 = (X1 - nx) * s + px
                Y0 = (Y0 - ny) * s + py
                Y1 = (Y1 - ny) * s + py
                out[side] = to_norm(X0, Y0, X1, Y1, CW, CH)
            entry[key] = out
        if "lid_skin" in old_states[af]:
            entry["lid_skin"] = old_states[af]["lid_skin"]
        new_cfg["states"][state] = entry
        report.append(f"           eyes → " + ", ".join(
            f"{sd}: cx={v['cx']:.3f} cy={v['cy']:.3f} w={v['w']:.3f} h={v['h']:.3f}"
            for sd, v in entry.get("eyes", {}).items()))

    print("\n".join(report))
    json.dump(new_cfg, open(OUT_CFG, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n已写 {OUT_CFG}（{len(new_cfg['states'])} 态）")

    # ---- 核验图：把换算后的框画回新立绘（头部 2× 裁切）----
    os.makedirs(DOCS, exist_ok=True)
    rows = [(st, os.path.join(V3, f"pet_{st}.png"), new_cfg["states"].get(st))
            for st in meta]
    CWt, cols = 430, 4
    rr = (len(rows) + cols - 1) // cols
    cv = Image.new("RGB", (cols * CWt, rr * CWt), (250, 248, 250))
    d = ImageDraw.Draw(cv)
    for j, (st, p, ent) in enumerate(rows):
        im = Image.open(p).convert("RGBA")
        W, H = im.size
        t = im.copy()
        dd = ImageDraw.Draw(t)
        for side, col in (("left", (255, 0, 0)), ("right", (0, 120, 255))):
            b = (ent or {}).get("eyes", {}).get(side)
            if not b:
                continue
            x0, y0, x1, y1 = box_edges(b, W, H)
            dd.rectangle([x0, y0, x1, y1], outline=col, width=5)
        for side in ("left", "right"):
            b = (ent or {}).get("cheeks", {}).get(side)
            if not b:
                continue
            x0, y0, x1, y1 = box_edges(b, W, H)
            dd.rectangle([x0, y0, x1, y1], outline=(0, 180, 60), width=4)
        # 头部裁切
        eb = (ent or {}).get("eyes", {}).get("left")
        if eb:
            cy = eb["cy"] * H; eh = eb["h"] * H
            y0 = max(0, int(cy - eh * 3.0)); y1 = min(H, int(cy + eh * 2.2))
        else:
            y0, y1 = 0, int(H * 0.5)
        crop = t.crop((0, y0, W, y1))
        crop.thumbnail((CWt - 8, CWt - 26), Image.LANCZOS)
        x = (j % cols) * CWt + (CWt - crop.width) // 2
        yy = (j // cols) * CWt + 22
        cv.paste(crop, (x, yy))
        d.text(((j % cols) * CWt + 5, (j // cols) * CWt + 4),
               f"pet_{st}  {'有锚点' if ent else '无锚点(闭眼)'}",
               fill=(20, 100, 20) if ent else (180, 120, 0))
    out = os.path.join(DOCS, "v3_anchor_verify.png")
    cv.save(out)
    print("核验图:", out)


if __name__ == "__main__":
    main()
