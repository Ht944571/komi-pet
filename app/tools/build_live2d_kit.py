# -*- coding: utf-8 -*-
"""Live2D 拆层工作包生成器（P0：给"自己拆层"的人把工作变成填空）。

产出（`live2d/` 目录）：
  reference/front|side|back.png   清洗掉水印、放大的三视图参考
  拆层骨架.ora                     Krita 可直接打开：图层名/顺序已按 Cubism 习惯排好
  （手册由另行维护的 markdown 提供）

.ora（OpenRaster）就是"zip + stack.xml + 若干 PNG"，Krita / Photopea 原生打开。
"""
import os
import re
import zipfile
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from collections import deque

SRC = r"C:\Users\htft2\.workbuddy\clipboard-images\clipboard-2026-09-26T06-06-47-606Z-36ca17cb.jpg"
OUT = r"D:\workbuddy\古见同学桌宠\live2d"
REF = os.path.join(OUT, "reference")
CANVAS = 1024
UP = 1.6                      # 参考图放大倍率（够看清即可，不是最终精度）

# 三视图在原图里的 x 区间（实测：空白竖带分隔）
VIEWS = [("front", 100, 517), ("side", 753, 1142), ("back", 1417, 1818)]

# 图层栈：**从上到下**（Krita 顶层画在最前）= Live2D 的绘制顺序（前 → 后）
# must=1 的层是"最小可跑模型"必须有的；其余为进阶（可后补，不影响先用）
LAYERS = [
    ("01_发饰呆毛_可选", 0),
    ("02_前发",         1),
    ("03_眉",           1),
    ("04_眼_上睑",      1),
    ("05_眼_瞳孔高光",  1),
    ("06_眼_眼白",      1),
    ("07_眼_下睑",      1),
    ("08_鼻_可选",      0),
    ("09_嘴",           1),
    ("10_脸_肤色底",    1),
    ("11_腮红",         0),
    ("12_身体制服",     1),
    ("13_手臂左右_可选", 0),
    ("14_裙摆",         1),
    ("15_腿袜",         1),
    ("16_后发",         1),
    ("17_补块_被遮挡区", 1),
    ("90_参考_三视图",  -1),   # 参考层（半透明、锁定）
]


def clean_wm(a):
    """清洗水印：图块四角的'浅灰/近白'小字 → 纯白（角色是饱和紫/绯红/深色，不受影响）。"""
    H, W = a.shape[:2]
    for (x0, y0, x1, y1) in [(0, int(H * .92), int(W * .30), H),      # 左下
                             (int(W * .70), int(H * .92), W, H),      # 右下
                             (int(W * .74), 0, W, int(H * .07))]:     # 右上（AI 生成角标）
        sub = a[y0:y1, x0:x1, :3].astype(np.int16)
        smx, smn = sub.max(axis=2), sub.min(axis=2)
        wm = ((smx - smn <= 34) & (smx >= 96)) | (smn >= 243)
        a[y0:y1, x0:x1, :3][wm] = 255
    return a


def isolate(im, x0, x1):
    """裁出一个视图 → 去白底（边缘洪泛）→ 内容 bbox → 放大。"""
    a = np.array(im.crop((x0, 0, x1, im.height)).convert("RGBA"))
    a = clean_wm(a)
    H, W = a.shape[:2]
    white = (a[..., 0] > 246) & (a[..., 1] > 246) & (a[..., 2] > 246)
    bg = np.zeros((H, W), bool)
    q = deque()
    for x in range(W):
        for y in (0, H - 1):
            if white[y, x] and not bg[y, x]:
                bg[y, x] = True; q.append((x, y))
    for y in range(H):
        for x in (0, W - 1):
            if white[y, x] and not bg[y, x]:
                bg[y, x] = True; q.append((x, y))
    while q:
        x, y = q.popleft()
        for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
            if 0 <= nx < W and 0 <= ny < H and white[ny, nx] and not bg[ny, nx]:
                bg[ny, nx] = True; q.append((nx, ny))
    a[bg, 3] = 0
    # 连通域过滤：只保留**最大的主体**。作用：像"AI 生成"这种深色徽章（浅色清洗清不掉，
    # 且不与角色相连）会被整块剔除 —— 不用关心它在图里哪个位置。
    mask = a[..., 3] > 8
    lab = np.zeros((H, W), np.int32); cur = 0; sizes = {}
    for sy in range(H):
        for sx in range(W):
            if mask[sy, sx] and lab[sy, sx] == 0:
                cur += 1; cnt = 0; qq = deque([(sx, sy)]); lab[sy, sx] = cur
                while qq:
                    x, y = qq.popleft(); cnt += 1
                    for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
                        if 0 <= nx < W and 0 <= ny < H and mask[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = cur; qq.append((nx, ny))
                sizes[cur] = cnt
    if sizes:
        big = max(sizes.values())
        keep = {k for k, v in sizes.items() if v >= big * 0.04}   # 主体 + 头发丝等大碎块
        a[..., 3] = np.where(np.isin(lab, list(keep)), a[..., 3], 0)
    ys, xs = np.nonzero(a[..., 3] > 8)
    a = a[ys.min():ys.max()+1, xs.min():xs.max()+1]
    out = Image.fromarray(a, "RGBA")
    w, h = out.size
    s = min(UP * 1024 / max(w, h), UP)          # 放大但不超 1.6×（放大太多只会糊）
    return out.resize((round(w*s), round(h*s)), Image.LANCZOS)


def main():
    os.makedirs(REF, exist_ok=True)
    im = Image.open(SRC).convert("RGB")
    refs = {}
    for name, x0, x1 in VIEWS:
        v = isolate(im, x0, x1)
        v.save(os.path.join(REF, f"{name}.png"))
        refs[name] = v
        print(f"reference/{name}.png  {v.size}")

    # ---- 参考合成图（放进 .ora 的最底层，半透明）----
    H = max(v.height for v in refs.values())
    gap = 40
    W = sum(v.width for v in refs.values()) + gap * (len(refs) + 1)
    comp = Image.new("RGBA", (W, H + 60), (255, 255, 255, 255))
    d = ImageDraw.Draw(comp)
    x = gap
    for name, v in refs.items():
        comp.paste(v, (x, 50), v)
        d.text((x + 4, 20), name.upper(), fill=(60, 50, 70))
        x += v.width + gap
    s = min((CANVAS - 40) / comp.width, (CANVAS - 40) / comp.height)
    comp = comp.resize((round(comp.width*s), round(comp.height*s)), Image.LANCZOS)
    # 参考层压暗+降透明度，画画时不抢眼
    a = np.array(comp)
    a[..., :3] = (a[..., :3].astype(np.float32) * 0.82 + 255 * 0.18).astype(np.uint8)
    comp = Image.fromarray(a, "RGBA")
    comp.putalpha(comp.getchannel("A").point(lambda v: int(v * 0.30)))
    ref_layer = comp

    # ---- 组装 .ora ----
    ora = os.path.join(OUT, "拆层骨架.ora")
    stack, data = [], []
    n = 0
    for name, must in LAYERS:
        n += 1
        fn = f"data/layer{n:03d}.png"
        if name.startswith("90_"):
            img = ref_layer
            op, vis = 0.32, "visible"
        else:
            img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
            op, vis = 1.0, "visible"
        buf = io_ = None
        import io as _io
        buf = _io.BytesIO(); img.save(buf, "PNG")
        data.append((fn, buf.getvalue()))
        tag = "★必做" if must == 1 else ("参考" if must == -1 else "可选")
        stack.append(
            f'    <layer name="{name} [{tag}]" src="{fn}" x="0" y="0" '
            f'opacity="{op}" visibility="{vis}" composite-op="svg:src-over"/>')

    stack_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<image version="0.0.3" w="{CANVAS}" h="{CANVAS}" xres="72" yres="72">\n'
        '  <stack>\n' + "\n".join(stack) + '\n  </stack>\n</image>\n')

    thumb = Image.new("RGB", (256, 256), (245, 244, 248))
    tt = ref_layer.copy(); tt.thumbnail((256, 256))
    thumb.paste(tt, ((256-tt.width)//2, (256-tt.height)//2), tt)
    import io as _io
    tb = _io.BytesIO(); thumb.save(tb, "PNG")

    with zipfile.ZipFile(ora, "w", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "image/openraster")
        z.writestr("stack.xml", stack_xml)
        for fn, b in data:
            z.writestr(fn, b)
        z.writestr("Thumbnails/thumbnail.png", tb.getvalue())
    print(f"\n拆层骨架 → {ora}  ({os.path.getsize(ora)//1024}KB, {len(LAYERS)} 层)")
    print(f"画布 {CANVAS}x{CANVAS} · 必做层 {sum(1 for _,m in LAYERS if m==1)} · 可选 {sum(1 for _,m in LAYERS if m==0)}")


if __name__ == "__main__":
    main()
