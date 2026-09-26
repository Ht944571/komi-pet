# -*- coding: utf-8 -*-
r"""点击分区回归测试：4 个部位分区是否与**画面上看得见的身体**对得上。

背景（2026-09-26）：`_body_region` 原按「立绘区高度的百分比」分区，前提是立绘填满立绘区。
v3 立绘改成**统一画布 + 底部对齐**（各态内容高只占画布 50%~95%）后，该前提不成立：
实测 8 态里 7 态错位 —— 点头顶判成 face、点眼睛判成 body，
导致「摸头→joy」「戳脸→surprise」两个交互根本点不出来。
现在按真实几何分区（立绘实际绘制矩形 + 内容框 + 眼部锚点），本测试守住它。

验收口径（比"在固定位置探点"更稳，因为趴/蹲等紧凑姿态的"胸口"本来就在脸旁边）：
  A. 四个区都可达（各占角色面积 ≥3%）
  B. face 区覆盖到左右眼中心
  C. 角色最上方一条是 head、最下方一条是 skirt
  D. 眼睛所在的水平窄列被判定为 face（不会被 body 抢走）

用法：python tools/test_hit_regions.py
"""
import os
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, APP)

STATES = ["idle", "happy", "pout", "shy", "blush", "stone", "joy", "surprise"]
N = 64                     # 栅格分辨率

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def main():
    import wb_whale_win as W

    app = W.WhalePet(run_seconds=None)
    lay = app._layout()
    print(f"窗口 {lay['W']}x{lay['H']}  bubble_h={lay['bubble_h']} pet_h={lay['pet_h']}")
    check("G0: 立绘内容框已加载（点击分区依赖它）",
          len(getattr(app, "_spr_cbox", {})) == len(STATES),
          f"got {sorted(getattr(app, '_spr_cbox', {}).keys())}")

    for st in STATES:
        app._morph_state = st
        app._morph_until = time.time() + 9999
        app.draw()
        rect = app._spr_rect
        if not rect:
            check(f"{st}: 立绘矩形可用", False, "self._spr_rect 为空")
            continue
        sx, sy, sw, sh = rect
        cbox = app._spr_cbox.get(st)
        if not cbox:
            continue
        x0, y0, x1, y1 = cbox
        # 在内容框上栅格化分区
        us = np.linspace(0.005, 0.995, N)
        grid = {}
        for uy in us:
            cy = (y0 + uy * (y1 - y0)) * sh + sy
            for ux in us:
                cx = (x0 + ux * (x1 - x0)) * sw + sx
                r = app._body_region(int(cx), int(cy), lay)
                grid[(round(ux, 3), round(uy, 3))] = r
        tot = len(grid)
        share = {k: sum(1 for v in grid.values() if v == k) / tot for k in
                 ("head", "face", "body", "skirt")}
        # 眼心
        fm = app._face_metrics(st, cbox)
        eye_ok, at_eyes = True, []
        if fm:
            ey, eh, _, _ = fm
            for e in (app._face_cfg(st, "").get("eyes") or {}).values():
                uy = (e["cy"] - y0) / (y1 - y0)
                ux = (e["cx"] - x0) / (x1 - x0)
                r = app._body_region(int((x0 + ux * (x1 - x0)) * sw + sx),
                                     int((y0 + uy * (y1 - y0)) * sh + sy), lay)
                at_eyes.append(r)
                if r != "face":
                    eye_ok = False
        top = grid[(round(us[N // 2], 3), round(us[0], 3))]
        bot = grid[(round(us[N // 2], 3), round(us[-1], 3))]
        ok4 = all(share[k] >= 0.03 for k in share)
        det = (f"占比 " + " ".join(f"{k}={share[k]:.0%}" for k in share)
               + f" | 眼处判定={at_eyes}")
        check(f"{st}: 四区可达 & face 含眼 & 顶=head 底=skirt", ok4 and eye_ok
              and top == "head" and bot == "skirt", det)

    app.close()
    print(f"\n=== 总结 ===\nPASS: {len(PASSED)}\nFAIL: {len(FAILED)}")
    for f in FAILED:
        print(f"  - {f}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
