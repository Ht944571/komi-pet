# -*- coding: utf-8 -*-
"""看板装饰素材重建（v2）+ 注入看板。

为什么要重建（2026-09-26，与桌宠 v3 美术统一）：
  看板里嵌的角色与桌宠**同一批源图**，但走的是**旧管线**：
    · 输出只有 190×171 / 127×190（v3 立绘是 1167×1570，低 ~8 倍）
    · 有 **1px alpha 腐蚀** → 线稿被磨细，观感比桌宠"轻薄"
  → 同一批源图、两套管线、两种观感。v2 换成 v3 的管线：**不腐蚀、2× 分辨率**。

两处旧工具的坑（本脚本修正）：
  1. 旧 `build_komi_assets.py` 依赖 **scipy**（当前环境没装），已不可复跑；
  2. 旧 `inject_komi_assets.py` 的 `DASH` 是**硬编码的 skillhub 老路径**
     （09-10 的副本），照它跑会改到**死文件**上，活看板一个字都不动。
     本脚本直接指向宠物项目内的 `app/wb_usage/dashboard.html`。

用法：
    python tools/build_komi_assets_v2.py            # 重建 + 注入
    python tools/build_komi_assets_v2.py --dry      # 只出图不注入
"""
import base64
import io
import json
import os
import re
import shutil
import sys
from collections import deque

import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
DASH = os.path.join(APP, "wb_usage", "dashboard.html")      # ★ 宠物项目内的活看板
OUT = r"D:\workbuddy\用量看板\komi_assets"                   # 素材产物（沿旧约定）
URIS = os.path.join(OUT, "data_uris.json")
SRC = os.path.expanduser(r"~\Pictures\Camera Roll\古见同学桌宠\古见同学图片")

SCALE_X = 2          # 输出放大倍率（旧值是 1×，糊）
TOL = 30
FEATHER = 1

# (源文件, 输出名, 旧目标最大边, 底部裁切线)  —— 与旧 JOBS 一致，去掉高冷版 elegant
JOBS = [
    ("Q版/10_IMG_0807.jpeg", "stand",  380, None),   # 站立・手扶头  → 页脚主立绘
    ("Q版/2_IMG_0799.jpeg",  "peek",   320, 966),    # 趴着探头     → 页脚左侧（裁掉水印带）
    ("Q版/1_IMG_0798.jpeg",  "think",  190, None),   # 托腮思考     → 空状态
    ("Q版/6_IMG_0805.jpeg",  "shy",    190, None),   # 侧趴害羞     → 客户端空状态
    ("Q版/0_IMG_0797.jpeg",  "peace",  190, None),   # 双手比耶     → 备用
]


def clean_watermark(a, W, H):
    """预清洗水印：右下角矩形内的"浅灰/近白"像素 → 纯白（角色是饱和紫/绯红/深色，不满足）。"""
    x0, y0 = int(W * 0.62), int(H * 0.93)
    sub = a[y0:, x0:, :3].astype(np.int16)
    smx, smn = sub.max(axis=2), sub.min(axis=2)
    wm = ((smx - smn <= 30) & (smx >= 100)) | (smn >= 245)
    a[y0:, x0:, :3][wm] = 255
    return a


def remove_bg(a):
    """边缘洪泛去白底（与 build_pet_v3 同判据）：只有与边缘相连的近白才算背景。"""
    H, W = a.shape[:2]
    is_white = (a[:, :, 0] > 255 - TOL) & (a[:, :, 1] > 255 - TOL) & (a[:, :, 2] > 255 - TOL)
    bg = np.zeros((H, W), bool)
    q = deque()
    for x in range(W):
        for y in (0, H - 1):
            if is_white[y, x] and not bg[y, x]:
                bg[y, x] = True
                q.append((x, y))
    for y in range(H):
        for x in (0, W - 1):
            if is_white[y, x] and not bg[y, x]:
                bg[y, x] = True
                q.append((x, y))
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < W and 0 <= ny < H and is_white[ny, nx] and not bg[ny, nx]:
                bg[ny, nx] = True
                q.append((nx, ny))
    out = a.copy()
    out[bg, 3] = 0
    return out


def cut(src_path, max_dim, trim=None):
    im = Image.open(src_path).convert("RGBA")
    W, H = im.size
    a = np.array(im)
    a = clean_watermark(a, W, H)
    a = remove_bg(a)
    # 主体外全部透明（上面只按"连边"判，画面里被角色围住的白色保留 —— 与桌面版一致）
    ys, xs = np.nonzero(a[:, :, 3] > 0)
    if len(ys) == 0:
        raise RuntimeError("空结果: " + src_path)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    if trim is not None and y1 > trim:
        y1 = trim                                # 定点裁掉右下角水印带
    a = a[y0:y1, x0:x1]
    out = Image.fromarray(a, "RGBA")
    if FEATHER:                                  # 与 build_pet_v3 同款羽化（**不腐蚀**）
        al = out.getchannel("A").filter(ImageFilter.GaussianBlur(FEATHER))
        al = al.point(lambda v: 0 if v < 24 else (255 if v > 250 else v))
        out.putalpha(al)
    w, h = out.size
    target = max_dim * SCALE_X                   # ★ 2× 分辨率（旧版糊在这里）
    s = target / max(w, h)
    if s < 1:
        out = out.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    return out, f"{x1-x0}x{y1-y0} @({x0},{y0}) of {W}x{H}"


def main():
    dry = "--dry" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    uris = {}
    print(f"{'name':8}{'新尺寸':>12}{'旧尺寸(约)':>12}   webp")
    for src, name, md, trim in JOBS:
        p = os.path.join(SRC, src.replace("/", os.sep))
        im, info = cut(p, md, trim)
        png = io.BytesIO(); im.save(png, "PNG", optimize=True)
        wb = io.BytesIO(); im.save(wb, "WEBP", quality=86, method=6)
        with open(os.path.join(OUT, name + ".png"), "wb") as f:
            f.write(png.getvalue())
        with open(os.path.join(OUT, name + ".webp"), "wb") as f:
            f.write(wb.getvalue())
        uris[name] = "data:image/webp;base64," + base64.b64encode(wb.getvalue()).decode()
        print(f"{name:8}{str(im.size):>12}{f'{md}x~':>12}   {len(wb.getvalue())//1024}KB   crop[{info}]")

    # logo0..3 由 build_komi_logo.py 负责，沿用旧值合并（别丢键）
    if os.path.isfile(URIS):
        old = json.load(open(URIS, encoding="utf-8"))
        for k, v in old.items():
            if k.startswith("logo"):
                uris[k] = v
        dropped = [k for k in old if k not in uris and not k.startswith("logo")]
        if dropped:
            print(f"\n不再产出（高冷版已停用/未使用）：{dropped}")
    json.dump(uris, open(URIS, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\n素材清单 → {URIS}（{len(uris)} 键）")
    if dry:
        return

    # ---- 注入活看板 ----
    if not os.path.isfile(DASH):
        raise SystemExit(f"找不到看板：{DASH}")
    html = open(DASH, encoding="utf-8").read()
    used = set(re.findall(r'data-komi="([A-Za-z0-9_]+)"', html))
    for grp in re.findall(r'data-komi-set="([^"]+)"', html):
        used.update(x.strip() for x in grp.split(",") if x.strip())
    missing = sorted(u for u in used if u not in uris)
    if missing:
        raise SystemExit(f"看板引用了但素材里没有：{missing}")
    used = sorted(used)

    bak = DASH + ".pre_v2.bak"
    if not os.path.exists(bak):
        shutil.copy2(DASH, bak)
        print("已备份 →", bak)

    lines = ["const KOMI={"]
    for i, name in enumerate(used):
        c = "," if i < len(used) - 1 else ""
        lines.append(f'  {json.dumps(name)}:{json.dumps(uris[name], ensure_ascii=False)}{c}')
    lines.append("};")
    block = "\n".join(lines)
    new, n = re.subn(r"(/\*__KOMI_ASSETS_START__\*/)(.*?)(/\*__KOMI_ASSETS_END__\*/)",
                     lambda m: m.group(1) + "\n" + block + "\n" + m.group(3),
                     html, flags=re.S)
    if n != 1:
        raise SystemExit(f"注入标记未命中（期望 1，实际 {n}）")
    open(DASH, "w", encoding="utf-8", newline="").write(new)
    print(f"已注入 {used} → {DASH}")
    print(f"base64 合计 {sum(len(uris[u]) for u in used)//1024} KB，看板现为 {len(new.encode('utf-8'))//1024} KB")


if __name__ == "__main__":
    main()
