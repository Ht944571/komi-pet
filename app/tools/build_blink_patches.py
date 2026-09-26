# -*- coding: utf-8 -*-
"""从「生成的闭眼图」构建眨眼贴片（分层差分）。

思路：
  生成图只改了眼睛，其余应当逐像素一致。所以 **diff 区域就是眼睛**——
  既拿到贴片，又不用再标定眼位（这是这套方案最省事的地方）。
  贴片的 alpha 直接由 **diff 幅度**生成：diff 大 = 该处确实变了（alpha 255），
  diff 小 = 没变（alpha 0）。于是「全闭」时贴片在眼区完全替换、在边缘平滑过渡，
  不会出现硬边接缝。

对齐：gen_blink_variants.py 把立绘搬到 1024² 白底时记录了 (s_fit, offset)，
  这里按同一映射反算回 v3 画布坐标（不重新检测脸框，避免二次误差）。

用法：
    python tools/build_blink_patches.py [state ...]      # 默认全部
    python tools/build_blink_patches.py --report         # 只看质量报告，不写文件
"""
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
V3 = os.path.join(APP, "assets", "pet_v3")
GEN = os.path.join(APP, "assets", "_gen3")
IN_DIR = os.path.join(GEN, "blink_in")
OUT_DIR = os.path.join(GEN, "blink_out")
PATCH_DIR = os.path.join(V3, "blink")
# 调试用整图（不进版本库）：`PATCH_DIR` 是要提交的运行期资产，别往里塞大文件
DBG_DIR = os.path.join(GEN, "blink_dbg")
MANIFEST = os.path.join(PATCH_DIR, "_patches.json")
MAP = os.path.join(IN_DIR, "_map.json")
CFG = os.path.join(V3, "_eye_config.json")

SIZE = 1024
PAD = 18            # 贴片外扩（像素，画布坐标）
# diff 阈值：实测眼内 diff 均值 118 / 中位 110，眼外均值 2.0 / 中位 0 / p99 只有 23。
# 所以 26 太低（会吃进噪声长尾，导致"框外变化 30%"的假警报），90 能干净地切出眼睛。
DIFF_T = 90
SOFT = 1.6          # alpha 过渡柔和度
# 「允许变化区」= 眼框向外扩：闭眼时眼线比睁眼可见区更宽、眼睑还在纵向移动
ZONE_DX, ZONE_DY = 0.55, 1.30
SLOTS = 6           # 半闭变体档数（按眼睑下落位置做垂直遮罩，运行时按闭合度取档）


def load_gen(state):
    """取该态最新一张生成图。"""
    cands = sorted(glob.glob(os.path.join(OUT_DIR, "*.png")), key=os.path.getmtime)
    # 生成文件名不含 state（Tool 用提示词命名），故按时间顺序与 STATES 对齐不可靠 →
    # 改为让调用方显式传入文件。这里只做兜底：单文件时直接用。
    if len(cands) == 1:
        return cands[0]
    raise SystemExit(
        f"[{state}] 无法自动判断用哪张生成图（{len(cands)} 张）。"
        f"请用 --file 显式指定，或先把该态的输出重命名进 {OUT_DIR}\\<state>.png")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    report_only = "--report" in sys.argv
    mapping = json.load(open(MAP, encoding="utf-8"))
    eye_cfg = json.load(open(CFG, encoding="utf-8"))["states"]
    os.makedirs(PATCH_DIR, exist_ok=True)
    os.makedirs(DBG_DIR, exist_ok=True)
    manifest = json.load(open(MANIFEST, encoding="utf-8")) if os.path.isfile(MANIFEST) else {}

    print(f"{'state':10}{'diff框(画布)':>26}{'框外变化%':>10}{'贴片尺寸':>12}  判定")
    for st in (args or list(mapping)):
        if st not in mapping:
            print(f"{st:10} [无映射]")
            continue
        # 生成图：优先 <state>.png（约定命名），否则报错要求显式指定
        cand = os.path.join(OUT_DIR, f"{st}.png")
        if not os.path.isfile(cand):
            rest = [p for p in glob.glob(os.path.join(OUT_DIR, "*.png"))
                    if os.path.basename(p) not in
                    {f"{s}.png" for s in mapping} and st not in os.path.basename(p)]
            print(f"{st:10} [缺生成图] 请把该态的生成结果另存为 {cand}")
            continue

        base = Image.open(os.path.join(V3, f"pet_{st}.png")).convert("RGBA")
        W, H = base.size
        m = mapping[st]
        s = m["s_fit"]; ox, oy = m["offset"]
        nw = max(1, round(W * s)); nh = max(1, round(H * s))

        gen = Image.open(cand).convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
        back = gen.crop((ox, oy, ox + nw, oy + nh)).resize((W, H), Image.LANCZOS)

        # 白底上比较 RGB（基底的透明处补白，避免与生成的白色背景产生假 diff）
        bgw = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        a = np.array(Image.alpha_composite(bgw, base).convert("RGB"), dtype=np.int16)
        b = np.array(back, dtype=np.int16)
        diff = np.abs(a - b).max(axis=2)

        # 眼区（来自锚点）：ez = 睁眼可见框；zone = 允许变化区（外扩，闭眼眼线更宽）
        ez = np.zeros((H, W), bool)
        zone = np.zeros((H, W), bool)
        ent = eye_cfg.get(st) or {}
        for e in (ent.get("eyes") or {}).values():
            x0 = int((e["cx"] - e["w"] / 2) * W); x1 = int((e["cx"] + e["w"] / 2) * W)
            y0 = int((e["cy"] - e["h"] / 2) * H); y1 = int((e["cy"] + e["h"] / 2) * H)
            ez[max(0, y0 - PAD):y1 + PAD, max(0, x0 - PAD):x1 + PAD] = True
            dx = int((x1 - x0) * ZONE_DX); dy = int((y1 - y0) * ZONE_DY)
            zone[max(0, y0 - dy):min(H, y1 + dy),
                 max(0, x0 - dx):min(W, x1 + dx)] = True
        changed = diff > DIFF_T
        n_ch = int(changed.sum())
        if n_ch == 0:
            print(f"{st:10} {'—':>26}{'—':>10}{'—':>12}  ❌ 无变化（生成图与基底相同？）")
            continue
        outside = int((changed & ~zone).sum()) / n_ch * 100
        # 只保留允许区内的改动：模型偶尔会顺手动一下领结/发梢，不该进眨眼贴片
        changed = changed & zone
        if changed.sum() == 0:
            print(f"{st:10} {'—':>26}{'—':>10}{'—':>12}  ❌ 允许区内无变化")
            continue

        ys, xs = np.nonzero(changed)
        bx0, bx1 = max(0, xs.min() - PAD), min(W, xs.max() + 1 + PAD)
        by0, by1 = max(0, ys.min() - PAD), min(H, ys.max() + 1 + PAD)

        # 贴片：RGB 取生成图，alpha = diff 幅度（归一 + 平滑），且只在允许区内生效
        alpha = np.clip((diff - DIFF_T * 0.5) * SOFT / 255.0 * 255.0, 0, 255)
        alpha = (alpha * zone).astype(np.uint8)
        patch = Image.new("RGBA", (bx1 - bx0, by1 - by0))
        patch.paste(back.crop((bx0, by0, bx1, by1)), (0, 0))
        patch.putalpha(Image.fromarray(alpha[by0:by1, bx0:bx1]))

        ok = outside < 12
        print(f"{st:10}{f'({bx0},{by0})-({bx1},{by1})':>26}{outside:9.1f}%"
              f"{str(patch.size):>12}  {'✅' if ok else '⚠️ 框外改动偏多'}")
        if report_only:
            continue
        patch.save(os.path.join(PATCH_DIR, f"{st}.png"), optimize=True)
        # ---- 半闭变体：按「眼睑下落位置」做垂直遮罩 ----
        # 为什么不能靠「闭眼图 × (1-ratio) 的 alpha 交叉」：那只是两张图混合，
        # 睁眼的暗瞳会透过半透明闭眼图显出来，糊成一层灰（实测 ratio=0.5 明显重影）。
        # 真实半闭 = **上睑从上往下压住眼球**，所以按眼睑位置遮罩才对：
        # 睑线以上用闭眼像素，以下保留睁眼像素。斜边留 soft 过渡避免硬横线。
        for slot in range(1, SLOTS + 1):
            c = slot / SLOTS
            # 贴片矩形内默认全遮罩，再逐眼把「睑线以下」的遮罩抹掉
            ramp = np.zeros((H, W), np.float32)
            ramp[by0:by1, bx0:bx1] = 1.0
            yy = np.arange(H, dtype=np.float32)[:, None]
            for e in (ent.get("eyes") or {}).values():
                x0 = int((e["cx"] - e["w"] / 2) * W); x1 = int((e["cx"] + e["w"] / 2) * W)
                y0 = int((e["cy"] - e["h"] / 2) * H); y1 = int((e["cy"] + e["h"] / 2) * H)
                lid = y0 + c * (y1 - y0)           # 睑线下落到眼高的 c 处
                soft = max(2.0, (y1 - y0) * 0.22)  # 斜边留一点过渡，避免一道硬横线
                ram = np.clip((lid - yy) / soft, 0.0, 1.0)      # (H,1)
                colm = np.zeros((1, W), np.float32)
                colm[0, max(0, x0):min(W, x1)] = 1.0
                m = np.where(colm > 0, ram, 1.0)                # (H,W)：眼内用 ram，眼外不遮
                ramp = np.minimum(ramp, m)
            pa = (alpha.astype(np.float32) * ramp).astype(np.uint8)
            pv = Image.new("RGBA", (bx1 - bx0, by1 - by0))
            pv.paste(back.crop((bx0, by0, bx1, by1)), (0, 0))
            pv.putalpha(Image.fromarray(pa[by0:by1, bx0:bx1]))
            pv.save(os.path.join(PATCH_DIR, f"{st}_c{slot}.png"), optimize=True)
        manifest[st] = {"rect": [int(bx0), int(by0), int(bx1 - bx0), int(by1 - by0)],
                        "canvas": [int(W), int(H)], "slots": SLOTS,
                        "outside_pct": round(float(outside), 2)}
        # 同时存一张"全闭"整图便于直观核对（放调试目录，不进版本库）
        back.save(os.path.join(DBG_DIR, f"{st}_closed_full.png"))

    if not report_only:
        json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n贴片目录: {PATCH_DIR}   清单: {MANIFEST}")


if __name__ == "__main__":
    main()
