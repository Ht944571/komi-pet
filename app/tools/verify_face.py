# -*- coding: utf-8 -*-
r"""表情贴图贴合验证：强制闭眼 + 腮红渲染各状态帧，导出 PNG 目视核对。

用法：python tools/verify_face.py
产出：$TEMP/wb_face_render_{idle,happy,pout,idle_f}.png
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_whale_win as W          # noqa: E402
import wb_motion as MOTION        # noqa: E402
from scan_seam import surf_to_png  # noqa: E402


def render(app, tag):
    app.draw()
    tmp = os.path.join(os.environ.get("TEMP", "."), f"wb_face_render_{tag}.png")
    im = surf_to_png(app.surf, tmp)
    print(f"[{tag}] {tmp} {im.size}")
    return tmp


def main():
    app = W.WhalePet(run_seconds=None)
    W._user32.ShowWindow(app.hwnd, 0)
    app.active = []
    app._report_event = lambda *a, **k: None
    time.sleep(0.3)
    now = time.time()
    # 冻结运动参数，只保留表情贴图
    app._gaze_dx = 0.0
    app._drag_tilt = 0.0
    app._squash_t0 = 0.0
    app._hover_t = 0.0
    app._breath = 0.0
    app._float_dy = 0.0
    app._wobble_until = 0.0
    app._blinker.eye_opening_ratio = lambda now: 0.0     # 强制全闭

    # 1) idle + 强制腮红（害羞档）
    app._emotion = MOTION.EMOTION_NEUTRAL
    app._react_face = "blush"
    app._react_until = now + 60
    app._flip_until = 0.0
    app._facing = ""
    render(app, "idle")

    # 2) happy（成功情绪 → 自带 soft 腮红）
    app._react_until = 0.0
    app._emotion = MOTION.EMOTION_SUCCESS
    render(app, "happy")

    # 3) pout（嘟嘴档）
    app._emotion = MOTION.EMOTION_NEUTRAL
    app._react_face = "pout"
    app._react_until = now + 60
    render(app, "pout")

    # 4) idle 镜像 + 腮红（验证 _f 翻转）
    app._react_face = "blush"
    app._react_until = now + 60
    app._flip_until = now + 60
    app._flip_dir = "_f"
    render(app, "idle_f")

    app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
