# -*- coding: utf-8 -*-
"""Q 版立绘 v3 构建：从「同源高分辨率单图」重建整套 8 态立绘。

为什么重做（2026-09-26 实测）：
  1) 现有 `make_sprites_komi.py` 产物经逐像素比对与源图一致（平均差 0.211）——
     美术管线没降质，**差距在选型与渲染尺寸**：18 张 Q版源图只用了 5 张，
     且 pout 的源图 `14_IMG_0781.jpeg` 不在 Q版 目录（脚本每跑必跳过它）。
  2) 旧 24 张尺寸各异（670x997 ~ 1013x1536），代码按**同一高度**绘制
     → 宽高比不同 → 切表情时角色大小与站位会跳。
  3) 立绘 1024² 被画到屏幕 ~176x210（缩 4.7 倍），细线丢失。

本脚本：
  - 只用 **1024² 单姿态图**（不用 1440² 九宫格：切格后每格仅 480²，分辨率减半）
  - **按「脸宽」归一化**（脸是画面最大肤色连通域，站立/趴地/蹲坐都成立），
    基准 FACE_REF 取源图自然脸宽 → 不做无谓重采样，保住原始像素
  - **两遍归一化到统一画布**（所有态同尺寸同底边）→ 从根上消除切表情跳变
  - 边缘洪泛去白底 + alpha 羽化，输出 PNG 与镜像 _f

用法：
    python tools/build_pet_v3.py            # 构建到 assets/pet_v3/
    python tools/build_pet_v3.py --review   # 额外产出对照图与报告
"""
import os
import sys
import json
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import calib_face as CF          # noqa: E402  复用项目已验证的脸/连通域判据

ROLL_Q = os.path.expanduser(r"~\Pictures\Camera Roll\古见同学桌宠\古见同学图片\Q版")
GALLERY = r"D:\workbuddy\用量看板\古见同学展示页\assets"
GEN3 = os.path.join(APP, "assets", "_gen3")

DST = os.path.join(APP, "assets", "pet_v3")
os.makedirs(DST, exist_ok=True)

TOL = 30            # 背景白判定容差（JPEG 噪点）
FEATHER = 1
FACE_REF = 620.0    # 脸宽归一基准 ≈ 1024² 源图的自然脸宽（≈不重采样）
PAD_FRAC = 0.02     # 统一画布四周留白（占内容尺寸）
GROUND = 6          # 底边地面留白（px）

# ---- 8 态选型（全部 1024² 单姿态图；只有 surprise 本机缺失，用 image-to-image 补）----
def q(f):   # 相机胶卷 Q版
    return os.path.join(ROLL_Q, f)


def g(f):   # 展示页画廊
    return os.path.join(GALLERY, f)


# ---- 归一化微调（第二轮实测回填）----
# 按「脸宽」归一后，在**真机渲染**上重量各态脸宽，仍有 115~127px（极差 9.4%）的残余差异
# ——来源是各源图「脸框」检测纳入的下巴/脖子范围略有不同。
# 这里按 `bias = 目标脸宽 / 实测脸宽` 逐态补偿，把渲染后的脸宽拉到一致。
# 复现测量见 docs/美术v3重构交接-2026-09-26.md §6。
BIAS_FIX = {
    "idle": 1.004, "happy": 0.965, "pout": 1.029, "shy": 0.980,
    "blush": 1.065, "stone": 0.972, "joy": 0.972, "surprise": 1.004,
}

STATES = [
    ("idle",     q("3_IMG_0802.jpeg"),  BIAS_FIX["idle"],  "站立手叉腰·半阖暗瞳·腮红（=旧 idle 源，锚点可换算）"),
    ("happy",    q("0_IMG_0797.jpeg"),  BIAS_FIX["happy"], "坐姿双手比耶·笑（=旧 happy 源，锚点可换算）"),
    ("pout",     g("14_IMG_0781.jpeg"), BIAS_FIX["pout"],  "蹲坐托腮（=旧 pout 源，锚点可换算）"),
    ("shy",      q("6_IMG_0805.jpeg"),  BIAS_FIX["shy"],   "侧爬回头·大脸红（=旧 shy 源，锚点可换算）"),
    ("blush",    q("4_IMG_0801.jpeg"),  BIAS_FIX["blush"], "跪坐低头·大脸红（=旧 blush 源，锚点可换算）"),
    ("stone",    q("5_IMG_0800.jpeg"),  BIAS_FIX["stone"], "趴地额头贴地（=旧 stone 源，锚点可换算）"),
    ("joy",      g("9_IMG_0786.jpeg"),  BIAS_FIX["joy"],   "站立·闭眼微笑（眼睛本就是闭的 → 不需要眨眼锚点）"),
    ("surprise", os.path.join(GEN3, "surprise_src.png"), BIAS_FIX["surprise"],
     "站立·圆睁大眼+张嘴「o」（ImageGen 以 idle 源图 i2i 生成 → 继承 idle 锚点）"),
]

# 锚点可换算的态：新态 ← 旧 _eye_config.json 里的旧态
#   None = 不需要锚点（joy 眼睛本就是闭的）
ANCHOR_FROM = {
    "idle": "idle", "happy": "happy", "pout": "pout",
    "shy": "shy", "blush": "blush", "stone": "stone",
    "joy": None, "surprise": "idle",
}


def load_rgba(p):
    im = Image.open(p)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        return im.convert("RGBA")          # 已是透明底：跳过去底步骤
    return None


def remove_bg(img):
    """从四边洪泛：只有与边缘相连的近白色才判为背景（保护角色内部浅色）。"""
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()
    bg = bytearray(w * h)
    q = deque()

    def is_bg(x, y):
        r, gg, b = px[x, y]
        return r > 255 - TOL and gg > 255 - TOL and b > 255 - TOL

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


def crop_to_content(img, pad=4):
    bbox = img.getbbox()
    if not bbox:
        return img
    l, t, r, b = bbox
    return img.crop((max(0, l - pad), max(0, t - pad),
                     min(img.width, r + pad), min(img.height, b + pad)))


def face_box(img):
    """脸框 = 皮肤连通域里「够大且最靠上」的那块（复用 calib_face._face_region）。

    为什么不用「最大块」：最大块可能是手/腿——本项目 alt_blush 上实测翻车过，
    calib_face 的注释里留了记录。头在所有皮肤之上，所以按 cy 取最靠上的够大块。
    """
    a = np.array(img)
    rgb = a[..., :3].astype(int)
    al = a[..., 3]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    skin_np = (al >= 128) & (r > 228) & (g > 195) & (b > 185) \
        & ((r - b) >= 6) & ((r - b) <= 60) & ((r - g) <= 40)
    if skin_np.sum() < 200:
        return None
    fx0, fy0, fx1, fy1 = CF._face_region(img, skin_np.tolist())
    return fx0, fy0, fx1, fy1


def feather(img):
    a = img.getchannel("A").filter(ImageFilter.GaussianBlur(FEATHER))
    a = a.point(lambda v: 0 if v < 24 else (255 if v > 250 else v))
    img.putalpha(a)
    return img


def prepare(path, bias, log):
    """去底 → 裁切 → 按脸宽归一 → 返回 (RGBA, 元数据)。元数据供锚点几何换算用。"""
    pre = load_rgba(path)
    if pre is not None:
        log.append(f"源已是透明底，跳过洪泛去底")
        img = crop_to_content(pre)
    else:
        img = remove_bg(Image.open(path))
        img = crop_to_content(img)
    meta = {"path": path, "content_size": img.size}
    fb = face_box(img)
    if fb:
        fx0, fy0, fx1, fy1 = fb
        fw = fx1 - fx0 + 1
        scale = (FACE_REF / fw) * bias
        log.append(f"脸框 ({fx0},{fy0})-({fx1},{fy1}) 宽 {fw} 高 {fy1-fy0+1} → ×{scale:.3f}")
    else:
        scale = bias
        log.append("脸框检测失败 → 不缩放")
    meta["face_box"] = fb
    meta["scale"] = scale
    if abs(scale - 1.0) > 0.01:
        img = img.resize((max(1, round(img.width * scale)),
                          max(1, round(img.height * scale))), Image.LANCZOS)
    log.append(f"归一后 {img.size}")
    return img, meta


def main():
    review = "--review" in sys.argv
    prepped = []
    for state, path, bias, desc in STATES:
        log = [f"[{state}] {desc}", f"源 {os.path.basename(path)}"]
        if not os.path.isfile(path):
            print("\n".join("  " + x for x in log))
            print(f"  [跳过] 缺图: {path}\n")
            continue
        im, meta = prepare(path, bias, log)
        prepped.append((state, im, log, meta))

    # ---- 第二遍：统一画布 = 最大内容尺寸 + 留白，底边对齐 ----
    if not prepped:
        print("没有可用素材")
        return
    W = max(im.width for _, im, _, _ in prepped)
    H = max(im.height for _, im, _, _ in prepped)
    CW = int(W * (1 + 2 * PAD_FRAC))
    CH = int(H * (1 + 2 * PAD_FRAC)) + GROUND
    print(f"统一画布 = {CW}x{CH}（最大内容 {W}x{H}，四周留白 {PAD_FRAC:.0%}）\n")

    made, build_meta = [], {}
    for state, im, log, meta in prepped:
        canvas = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        x = (CW - im.width) // 2
        y = CH - GROUND - im.height
        canvas.alpha_composite(im, (x, y))
        canvas = feather(canvas)
        canvas.save(os.path.join(DST, f"pet_{state}.png"))
        canvas.transpose(Image.FLIP_LEFT_RIGHT).save(
            os.path.join(DST, f"pet_{state}_f.png"))
        log.append(f"贴到 {CW}x{CH} @({x},{y})")
        made.append((state, canvas, log))
        # 归一化内容框（相对画布）：**点击分区要用**。
        # v3 是统一画布 + 底部对齐，角色上方留空 → 运行时的 _body_region 不能再按
        # 「立绘区高度的百分比」分区（那会让点头顶判成 face、点眼睛判成 body）。
        # 这里离线算好（2D 运行时零依赖，没有 numpy/PIL 可用），写进 meta 供运行时读。
        cys, cxs = np.nonzero(np.array(canvas.getchannel("A")) > 16)
        cbox = [round(float(cxs.min()) / CW, 4), round(float(cys.min()) / CH, 4),
                round(float(cxs.max() + 1) / CW, 4), round(float(cys.max() + 1) / CH, 4)]
        build_meta[state] = {
            "source": meta["path"],
            "content_size": list(meta["content_size"]),
            "scale": meta["scale"],
            "face_box": meta["face_box"],
            "paste_xy": [x, y],
            "canvas_size": [CW, CH],
            "content_box": cbox,
            "anchor_from": ANCHOR_FROM.get(state),
        }
        print("\n".join("  " + s for s in log))
        print()

    with open(os.path.join(DST, "_build_meta.json"), "w", encoding="utf-8") as f:
        json.dump(build_meta, f, ensure_ascii=False, indent=1)
    print(f"→ 已输出 {len(made) * 2} 张（含镜像 _f）到 {DST}")
    print(f"→ 几何元数据 {os.path.join(DST, '_build_meta.json')}")

    if review:
        cols = 4
        CWt = 470
        rows = (len(made) + cols - 1) // cols
        for bgname, bg in (("white", (250, 248, 250)), ("dark", (36, 33, 40))):
            cv = Image.new("RGB", (cols * CWt, rows * CWt), bg)
            d = ImageDraw.Draw(cv)
            for j, (state, im, _) in enumerate(made):
                t = Image.alpha_composite(Image.new("RGBA", im.size, bg + (255,)), im)
                t = t.convert("RGB")
                t.thumbnail((CWt - 10, CWt - 16), Image.LANCZOS)
                x = (j % cols) * CWt + (CWt - t.width) // 2
                y = (j // cols) * CWt + 14
                cv.paste(t, (x, y))
                d.text(((j % cols) * CWt + 5, (j // cols) * CWt + 2),
                       f"pet_{state}", fill=(255, 140, 140) if bgname == "dark"
                       else (150, 60, 90))
            out = os.path.join(DST, f"_review_{bgname}.png")
            cv.save(out)
            print("对照图:", out)

        print("\n=== 结果 ===")
        for state, im, _ in made:
            a = np.array(im.getchannel("A"))
            ys, xs = np.nonzero(a > 16)
            print(f"  pet_{state:9} {im.size}  内容 {xs.max()-xs.min()+1:4}x{ys.max()-ys.min()+1:4}"
                  f"  底边 y={ys.max():4}  内容占比 {100*((a>16).mean()):5.1f}%")


if __name__ == "__main__":
    main()
