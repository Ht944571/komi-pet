# -*- coding: utf-8 -*-
r"""把一段 AI 生成的视频转成桌宠可播放的**透明帧序列**（不是"贴一张静态图"）。

背景
----
视频是白底的（AI 工具里常见），角色身上也有大片浅色（脸、书页）→
**朴素阈值抠图会把她的脸和书页一起抠穿**。本项目 `gen_to_asset.background_mask`
早就解决了这个：只把「**与图像边界连通**的中性亮区」当背景（扫描线洪水）。
所以这里直接复用它，不重复造轮子。

另外 AI 视频常带**水印**（暗色小字，抠图时会被当成前景 → 撑大内容框），
所以要先把底部裁掉再取内容框。

输出
----
    assets/anim/<name>/<name>_000.png ... （真透明底）
    assets/anim/<name>/_anim_meta.json    （画布 / 内容框 / fps / 帧数 / 分段）

约定：**画布与桌宠的显示口径一致（1167×1570，底边对齐）**，这样桌宠里
气泡锚点 / 命中区 / 视线判定全部不用改就能用。

用法：
    python tools/video_to_pet_frames.py <视频> --name write --fps 12 --crop-bottom 40
    python tools/video_to_pet_frames.py <视频> --name write --preview     # 出核对图
"""

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                       # 复用同目录的既有抠图实现
from gen_to_asset import background_mask       # noqa: E402

APP = os.path.dirname(HERE)
ASSETS = os.path.join(APP, "assets")
ANIM_DIR = os.path.join(ASSETS, "anim")

# 帧画布尺寸。2026-09-28 起桌宠只有帧序列这一种形态，**没有立绘可参照了** ——
# 画布尺寸的唯一出处改成「已有素材的 meta」（不写死），读不到才退回这个常量
# （= 原 v3 立绘的画布，也是当前所有帧素材用的尺寸）。
CANVAS_FALLBACK = (1167, 1570)


def canvas_size():
    """帧画布尺寸：优先读已有素材 `_anim_meta.json` 的 `canvas_source`。

    这样以后改了画布尺寸、重跑本工具时会自动跟随，不必手改常量。
    """
    for name in ("write",):
        mp = os.path.join(ANIM_DIR, name, "_anim_meta.json")
        try:
            with open(mp, encoding="utf-8") as f:
                meta = json.load(f)
            cs = meta.get("canvas_source")
            if cs and len(cs) == 2 and all(int(v) > 0 for v in cs):
                return int(cs[0]), int(cs[1])
        except Exception:
            pass
    return CANVAS_FALLBACK


def decode(video, keep):
    """解码并抽帧。`keep` = 要保留的帧序号列表。"""
    import imageio.v3 as iio
    frames = iio.imread(video)
    out = []
    for i in keep:
        if 0 <= i < len(frames):
            out.append(Image.fromarray(frames[i]).convert("RGB"))
    return out


def pick_indices(n, src_fps, dst_fps):
    """按目标 fps 均匀抽帧。"""
    if dst_fps >= src_fps:
        return list(range(n))
    step = src_fps / float(dst_fps)
    idx, i = [], 0.0
    while int(i) < n:
        idx.append(int(i))
        i += step
    return idx


def frame_alpha(im):
    """返回 bool 数组：True = 背景。"""
    w, h = im.size
    m = background_mask(im)
    return np.frombuffer(bytes(m), dtype=np.uint8).reshape(h, w).astype(bool)


def union_bbox(imgs, crop_bottom):
    """跨帧取**统一**的内容框 —— 逐帧各自裁会导致画面抖动。

    `crop_bottom`：底部要切掉的像素（AI 水印通常在这）。
    """
    x0 = y0 = 1 << 30
    x1 = y1 = -1
    for im in imgs:
        w, h = im.size
        bg = frame_alpha(im)
        limit = h - crop_bottom
        fg = (~bg)
        fg[limit:, :] = False                  # 水印那一条不算前景
        ys, xs = np.where(fg)
        if len(ys) == 0:
            continue
        x0, y0 = min(x0, int(xs.min())), min(y0, int(ys.min()))
        x1, y1 = max(x1, int(xs.max())), max(y1, int(ys.max()))
    if x1 < 0:
        raise SystemExit("❌ 没找到任何前景像素（抠图判据需要调整）")
    return (x0, y0, x1, y1)


def render_to_canvas(im, box, canvas, scale=1.0, bottom_align=True):
    """裁到统一内容框 → 等比缩放 → 贴进帧画布（底边对齐）。"""
    cw, ch = canvas
    crop = im.crop(box)
    cwpx, chpx = crop.size
    # 先按画布比例算出能放多大，再乘可调系数
    k = min(cw / float(cwpx), ch / float(chpx)) * scale
    tw, th = max(1, int(round(cwpx * k))), max(1, int(round(chpx * k)))
    crop = crop.resize((tw, th), Image.LANCZOS)
    a = np.asarray(crop)
    bg = frame_alpha(crop)
    rgba = np.dstack([a, np.where(bg, 0, 255).astype(np.uint8)])
    out = Image.new("RGBA", canvas, (0, 0, 0, 0))
    x = (cw - tw) // 2
    y = (ch - th) if bottom_align else (ch - th) // 2
    out.paste(Image.fromarray(rgba, "RGBA"), (x, y), Image.fromarray(rgba, "RGBA"))
    return out, (x / float(cw), y / float(ch), tw / float(cw), th / float(ch))


def preview(paths, meta, out_png):
    """拼一张核对图：前 6 帧 + 最后 2 帧，浅/深两种底各一行。"""
    ims = [Image.open(p) for p in paths[::max(1, len(paths) // 8)][:8]]
    Wm = max(i.width for i in ims) // 3
    Hm = max(i.height for i in ims) // 3
    ims = [i.resize((Wm, Hm), Image.LANCZOS) for i in ims]
    gap = 8
    sheet = Image.new("RGB", (len(ims) * (Wm + gap) + gap, Hm * 2 + gap * 3), (247, 245, 241))
    for row, bg in enumerate(((247, 245, 241), (60, 58, 64))):
        band = Image.new("RGB", (sheet.width, Hm + gap * 2), bg)
        for i, im in enumerate(ims):
            band.paste(im, (gap + i * (Wm + gap), gap), im)
        sheet.paste(band, (0, row * (Hm + gap * 2) + gap))
    sheet.save(out_png)


def main():
    ap = argparse.ArgumentParser(description="视频 → 桌宠透明帧序列")
    ap.add_argument("video")
    ap.add_argument("--name", default="write", help="动作名（= 子目录名）")
    ap.add_argument("--fps", type=float, default=12.0, help="抽帧目标 fps（原片 24）")
    ap.add_argument("--src-fps", type=float, default=24.0)
    ap.add_argument("--crop-bottom", type=int, default=40, help="底部裁掉多少像素（去水印）")
    ap.add_argument("--scale", type=float, default=1.0, help="放进画布时的缩放微调")
    ap.add_argument("--center", action="store_true", help="垂直居中而不是底边对齐")
    ap.add_argument("--out-size", type=int, default=420, help="存储宽度（默认 420，够用且体积小）")
    ap.add_argument("--preview", action="store_true", help="额外出一张核对图")
    args = ap.parse_args()

    canvas = canvas_size()
    print(f"  帧画布: {canvas[0]}x{canvas[1]}")

    import imageio.v3 as iio
    total = len(iio.imread(args.video, index=0)) * 0  # 只是确认能打开
    raw = iio.imread(args.video)
    n = len(raw)
    keep = pick_indices(n, args.src_fps, args.fps)
    print(f"  原片 {n} 帧 @ {args.src_fps:.0f}fps → 抽到 {len(keep)} 帧 @ {args.fps:.0f}fps")

    ims = [Image.fromarray(raw[i]).convert("RGB") for i in keep]
    box = union_bbox(ims, args.crop_bottom)
    print(f"  统一内容框: {box}  （宽 {box[2]-box[0]} 高 {box[3]-box[1]}）")

    out_dir = os.path.join(ANIM_DIR, args.name)
    os.makedirs(out_dir, exist_ok=True)
    # 清掉上一轮的旧帧（只清本动作的）。⚠️ 这一步**绝不能让转帧中断**：
    #   · 沙箱的 safe-delete 在中文路径下会失败（把「古见同学桌宠」解成乱码）
    #   · 桌宠进程还可能正读着这些帧
    # 删不掉也无所谓 —— 后面按帧号覆盖写，而读取端只看 `_anim_meta.json` 里的 count，
    # 多余的老帧（帧号 ≥ count）永远不会被读到。踩过一次：整段转帧因为删文件失败而中断，
    # 却只在最后抛异常，前面的"抽帧成功"日志照打，看起来像转好了。
    for f in os.listdir(out_dir):
        if f.endswith(".png") and f.startswith(args.name + "_"):
            try:
                os.remove(os.path.join(out_dir, f))
            except Exception:
                pass

    paths, boxes = [], []
    for k, im in enumerate(ims):
        out, rel = render_to_canvas(im, box, canvas, args.scale, bottom_align=not args.center)
        # 存成显示尺寸：**不要**按 1167×1570 存 —— 49 帧那样要 49MB，
        # 而桌宠实际只显示 200~300px 高，纯浪费。画布比例不变、内容框仍是归一化的，绘制端照旧。
        if args.out_size and canvas[0] > args.out_size:
            ow = args.out_size
            oh = int(round(canvas[1] * ow / float(canvas[0])))
            out = out.resize((ow, oh), Image.LANCZOS)
        p = os.path.join(out_dir, f"{args.name}_{k:03d}.png")
        out.save(p)
        paths.append(p)
        # ⚠️ 内容框必须取**实际非透明像素**的紧框，不能用"摆放矩形" ——
        #    摆放矩形含透明留白，会让内容框变成整张画布，进而把气泡/命中区/本子全顶歪。
        a = np.asarray(out)[:, :, 3]
        ys, xs = np.where(a > 8)
        if len(ys):
            cw_, ch_ = out.size
            boxes.append((xs.min() / float(cw_), ys.min() / float(ch_),
                          (xs.max() + 1) / float(cw_), (ys.max() + 1) / float(ch_)))

    # 内容框（取所有帧里"最紧"的公共内框）
    bx0 = min(b[0] for b in boxes)
    by0 = min(b[1] for b in boxes)
    bx1 = max(b[2] for b in boxes)
    by1 = max(b[3] for b in boxes)

    meta = {
        "name": args.name,
        "canvas": list(Image.open(paths[0]).size),
        "canvas_source": list(canvas),
        "fps": args.fps,
        "count": len(paths),
        "src_frames": len(keep),
        "content_box": [round(bx0, 4), round(by0, 4), round(bx1, 4), round(by1, 4)],
        "bottom_aligned": not args.center,
        "source": os.path.basename(args.video),
        "note": ("由 AI 生成视频转来。**仅当源片无水印/已裁掉水印时**才能进成品 "
                 "（项目红线：带水印的源图不得进成品）。"),
    }
    mp = os.path.join(out_dir, "_anim_meta.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {len(paths)} 帧 → {out_dir}")
    print(f"  ✅ 元数据 → {mp}")
    print(f"     内容框(归一化): {meta['content_box']}")

    if args.preview:
        pv = os.path.join(APP, f"_preview_anim_{args.name}.png")
        preview(paths, meta, pv)
        print(f"  ✅ 核对图 → {pv}")


if __name__ == "__main__":
    main()
