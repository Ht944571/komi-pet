# -*- coding: utf-8 -*-
r"""配件层真实渲染出图：走**真正的 GDI 管线**（不是 PIL 模拟）。

对每种 agent 组合，让 WhalePet 用注入的活跃集合画一帧，再从分层窗口的 DIB
读回像素存 PNG。用于目视核对"戴上是什么样"，也给回归留底图。

用法：python tools/shot_accessories.py [--out 目录]
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

from _shotutil import surf_to_image          # noqa: E402

COMBOS = [
    ("none", []),
    ("workbuddy", ["workbuddy"]),
    ("zcode", ["zcode"]),
    ("dsh", ["deepseek-harness"]),
    ("all3", ["workbuddy", "zcode", "deepseek-harness"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(APP), "docs"))
    ap.add_argument("--scale", type=float, default=1.0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    import wb_whale_win as W

    app = W.WhalePet(run_seconds=None)
    app.scale = args.scale
    app.acc_on = True
    app._acc = None
    app._init_accessories()
    if app._acc is None:
        print("!! 配件层初始化失败")
        return 1
    # 关掉探测线程的写入（本工具直接注入活跃集合）
    if app._presence:
        app._presence.stop()
        app._presence = None

    made = []
    for name, act in COMBOS:
        app._acc.set_active_agents(set(act))
        app._acc.cat.inited = False
        # 把配件进度直接推到"完全到位"（跳过入退场），再定格一帧
        for _ in range(40):
            app._acc.update(0.05, app._layout(), time.time(), enter_s=0.01, exit_s=0.01)
        app.draw()
        if app._acc.transition("cat"):
            app._acc.cat.state = "walk"          # 定格在行走帧，便于目视
        app.draw()
        img = surf_to_image(app.surf)
        p = os.path.join(args.out, f"acc_live_{name}.png")
        img.save(p)
        made.append(p)
        print(f"  ✓ {name:12s} → {os.path.basename(p)}  active={sorted(act)}")

    app.close()
    print(f"[shot] {len(made)} 张 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
