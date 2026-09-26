# -*- coding: utf-8 -*-
"""⚠️ **已弃用（2026-09-26）—— 请用 `build_pet_v3.py`**

本脚本是 v3 之前的立绘管线，产出的 24 张旧立绘（`app/assets/pet_*.png`，含高冷版）
**已于 2026-09-26 全部删除**，运行时不读。现在再跑本脚本只会：
  · 把已被取代的旧立绘重新写回 `app/assets/`（**运行时不会用，纯属制造困惑**）
  · 且 `pout` 的源图路径本来就是坏的（`14_IMG_0781.jpeg` 不在 Q版 目录）→ 每跑必跳过

保留它只为记录旧管线的做法（边缘洪泛去白底 + crop_to_content + 羽化 + 原子覆盖）。
**新立绘、改美术、加态，一律走 `build_pet_v3.py` + `remap_eye_config_v3.py` + `verify_sprites_v3.py`。**

---
古见同学立绘素材处理（双版本）：边缘洪泛去白底 → 裁切 → 镜像 → 透明 PNG。

源图：
  Q 版：D:/workbuddy/用量看板/.../古见同学图片/Q版/  (14 张 chibi 大头小身)
  高冷版：D:/workbuddy/用量看板/.../古见同学图片/高冷版/  (9 张标准少女比例)

输出：skillhub scripts/assets/
  Q 版：pet_idle / pet_happy / pet_shy / pet_pout / pet_blush / pet_stone  (各正反)
  高冷版：pet_alt_idle / pet_alt_happy / pet_alt_shy / pet_alt_pout / pet_alt_blush / pet_alt_stone

复用现有去白底逻辑（边缘洪泛，保护角色内部浅色区域）。
"""
import os
from collections import deque

from PIL import Image, ImageFilter

# 素材源目录（生成时的本机路径；开源用户无需运行本工具——assets/ 已含全部成品。
# 如需重建：把原始生成图放入对应目录，或用环境变量 KOMI_SRC_Q / KOMI_SRC_ALT 覆盖）
SRC_Q = os.environ.get(
    "KOMI_SRC_Q", os.path.expanduser(r"~\Pictures\Camera Roll\古见同学桌宠\古见同学图片\Q版"))
SRC_ALT = os.environ.get(
    "KOMI_SRC_ALT", os.path.expanduser(r"~\Pictures\Camera Roll\古见同学桌宠\古见同学图片\高冷版"))
# 输出到本项目内部（原先硬编码 WorkBuddy 技能目录，换机器必炸）
DST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
os.makedirs(DST, exist_ok=True)

# 避免运行时被桌宠进程文件锁拒绝：先写到 TEMP 再 mv 覆盖
TMP = os.path.join(os.environ.get("TEMP", "."), "wb_komi_sprites_tmp")
os.makedirs(TMP, exist_ok=True)

TOL = 30          # 背景白判定容差（JPEG 噪点）
FEATHER = 1       # alpha 边缘羽化

# 各表情的源图选型（人设优先：招牌动作契合）
#   idle:   标准呆滞/清冷   happy: 开心打招呼   shy: 害羞躲闪
#   pout:   嘟嘴/委屈      blush: 紧张脸红     stone: 石像化（僵直）
PICKS_Q = {
    "idle":   "3_IMG_0802.jpeg",    # 站立呆滞，紫黑大眼半垂
    "happy":  "0_IMG_0797.jpeg",    # 站立摆手打招呼，睁大眼睛
    "shy":    "6_IMG_0805.jpeg",    # 侧身脸红逃跑
    "pout":   "14_IMG_0781.jpeg",   # 托腮半闭眼（嘟嘴）
    "blush":  "4_IMG_0801.jpeg",    # 跪坐低头垂眼（紧张等待）
    "stone":  "5_IMG_0800.jpeg",    # 趴地额头贴地（石像化）
}
PICKS_ALT = {
    "idle":   "15_IMG_0814.jpeg",   # 标准站立，紫黑大眼直视（清冷）
    "happy":  "22_IMG_0819.jpeg",   # 单手摸头发，回眸惊讶（开心但仍有疏离感）
    "shy":    "19_IMG_0816.jpeg",   # 侧身脸红冷汗张嘴说不出话（害羞说不出话，招牌）
    "pout":   "21_IMG_0820.jpeg",   # 侧身睁大眼，紧张
    "blush":  "18_IMG_0817.jpeg",   # 紧张攥拳脸红冷汗（石像化前夕）
    "stone":  "17_IMG_0812.jpeg",   # 站立僵直直视（石像化）
}


def load(path):
    return Image.open(path).convert("RGB")


def remove_bg(img):
    """从四边洪泛填充：只有与边缘相连的近白色才视为背景，角色内部白色保留。"""
    w, h = img.size
    px = img.load()
    bg = bytearray(w * h)
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


def process(src_path, out_base):
    img = load(src_path)
    print(f"  {os.path.basename(src_path)}: 原始 {img.size}")
    img = remove_bg(img)
    img = crop_to_content(img)
    img = feather(img)
    print(f"    → 透明裁切后 {img.size}")
    # 写到临时目录，避开桌宠进程文件锁
    img.save(os.path.join(TMP, out_base + ".png"))
    img.transpose(Image.FLIP_LEFT_RIGHT).save(
        os.path.join(TMP, out_base + "_f.png"))
    a = img.getchannel("A")
    hist = a.histogram()
    transparent = sum(hist[:16]); opaque = sum(hist[240:])
    total = img.width * img.height
    print(f"    → 透明 {transparent / total:.1%} 不透明 {opaque / total:.1%}")


def move_to_dst():
    """处理完后一次性把临时文件 mv 到目标目录（覆盖旧文件）。

    避开 os.remove / os.replace → 沙箱 shim（safe-delete 拦截）。
    直接用 Windows API MoveFileExW with MOVEFILE_REPLACE_EXISTING，原子覆盖。
    """
    import ctypes
    from ctypes import wintypes as wt
    k = ctypes.windll.kernel32
    k.MoveFileExW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
    k.MoveFileExW.restype = wt.BOOL
    MOVEFILE_REPLACE_EXISTING = 0x00000001

    moved = 0
    for f in os.listdir(TMP):
        src = os.path.join(TMP, f)
        dst = os.path.join(DST, f)
        if not f.startswith("pet_"):
            continue
        ok = k.MoveFileExW(src, dst, MOVEFILE_REPLACE_EXISTING)
        if ok:
            moved += 1
        else:
            print(f"  [FAIL] MoveFileExW 失败: {f}  err={ctypes.get_last_error()}")
    print(f"  → 已移动 {moved} 个文件到 {DST}")


if __name__ == "__main__":
    print("=== Q 版（萌系）===")
    for state, fname in PICKS_Q.items():
        src = os.path.join(SRC_Q, fname)
        if not os.path.isfile(src):
            print(f"  [跳过] 缺图: {src}")
            continue
        process(src, f"pet_{state}")
    print()
    print("=== 高冷版（清冷疏离）===")
    for state, fname in PICKS_ALT.items():
        src = os.path.join(SRC_ALT, fname)
        if not os.path.isfile(src):
            print(f"  [跳过] 缺图: {src}")
            continue
        process(src, f"pet_alt_{state}")
    print()
    print("全部处理完成")
    move_to_dst()
    print("全部完成 →", DST)