# -*- coding: utf-8 -*-
r"""面部叠加层真机出图：眨眼各相位 / 部分眨眼 / 腮红 / 高冷版，1× 全身 + 4× 面部裁切。

与 shot_accessories.py 同一管线（真 GDI 绘制 → DIB 读回），专拍"程序化叠加层"
（眨眼眼睑盖板 / 腮红）——这些元素每几秒出现一次，1× 下的画质只有真机能说明。

用法：python tools/shot_face_frames.py [--out 目录] [--scale 1.0]
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
from PIL import Image                        # noqa: E402

# (名字, 预设函数(app)：在 draw() 前把眨眼/表情状态摆到位)
CASES = [
    ("open",      lambda app: _set_blink(app, None)),
    ("full_50",   lambda app: _set_blink(app, 0.50, depth=1.0)),
    ("full_hold", lambda app: _set_blink(app, "hold", depth=1.0)),
    ("full_75o",  lambda app: _set_blink(app, "open", u=0.75, depth=1.0)),
    ("part_bot65", lambda app: _set_blink(app, 0.0, depth=0.65)),   # 部分眨眼最低点
    ("part_bot40", lambda app: _set_blink(app, 0.0, depth=0.40)),
    ("happy",     lambda app: (_set_blink(app, None), _set_emotion(app, "success"))),
    ("shy",       lambda app: (_set_blink(app, None), _set_react(app, "blush"))),
    ("alt_open",  lambda app: (_set_blink(app, None), _set_style(app, "alt"))),
    ("alt_hold",  lambda app: (_set_blink(app, "hold", depth=1.0), _set_style(app, "alt"))),
]


def _set_blink(app, phase, u=0.0, depth=1.0):
    """把眨眼状态机摆到指定相位。phase: None=睁开 / 数=闭眼段进度 / 'hold' / 'open'。"""
    b = app._blinker
    b.quality = app._quality
    now = time.time()
    b.close_depth = depth
    if depth < 1.0:
        b.seg_close = 0.099
        b.seg_hold = 0.0
        b.seg_open = 0.165
    else:
        b.seg_close, b.seg_hold, b.seg_open = 0.099, 0.066, 0.165
    total = b.seg_close + b.seg_hold + b.seg_open
    if phase is None:
        b.blink_t0 = b.blink_total = 0.0
    else:
        if phase == "hold":
            off = b.seg_close + b.seg_hold * 0.5
        elif phase == "open":
            off = b.seg_close + b.seg_hold + b.seg_open * u
        else:
            off = b.seg_close * phase
        b.blink_t0 = now - off
        b.blink_total = total + 1.0          # 留余量，is_active 只由 off 决定


def _set_emotion(app, name):
    import wb_motion as MOTION
    app._emotion = getattr(MOTION, f"EMOTION_{name.upper()}")
    app._emotion_until = time.time() + 60


def _set_react(app, face):
    app._react_face = face
    app._react_until = time.time() + 60


def _set_style(app, style):
    app.style = style


def _zoom_face(img, rect, zoom=4, head_frac=0.62):
    """裁立绘上部（脸）并放大。rect = _spr_rect (x, y, w, h)。"""
    x, y, w, h = rect
    box = (max(0, int(x) - 6), max(0, int(y) - 6),
           int(x + w) + 6, int(y + h * head_frac) + 6)
    face = img.crop(box)
    return face.resize((face.width * zoom, face.height * zoom), Image.NEAREST)


def _zoom_sprite(img, rect, zoom=3):
    x, y, w, h = rect
    box = (max(0, int(x) - 6), max(0, int(y) - 6),
           int(x + w) + 6, int(y + h) + 6)
    sp = img.crop(box)
    return sp.resize((sp.width * zoom, sp.height * zoom), Image.NEAREST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(APP), "docs", "face_frames"))
    ap.add_argument("--scale", type=float, default=1.0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    import wb_whale_win as W

    app = W.WhalePet(run_seconds=None)
    app.scale = args.scale
    if app._presence:
        app._presence.stop()
        app._presence = None

    # 稳定数据态：无会话在跑 → 立绘走 idle，气泡走固定文案
    app.active = []
    app.latest_turn = {"credit": 3.2, "total_tokens": 184000,
                       "first_ts": time.time() - 95, "last_ts": time.time(),
                       "title": "眨眼叠加层诊断"}
    app.db_ok = True
    app.api_ok = True

    for name, setup in CASES:
        setup(app)
        app._drawn_sig = None
        app.draw()
        img = surf_to_image(app.surf)
        p = os.path.join(args.out, f"face_{name}.png")
        img.save(p)
        rect = app._spr_rect
        if rect:
            _zoom_face(img, rect).save(
                os.path.join(args.out, f"face_{name}_zoom.png"))
            _zoom_sprite(img, rect, 2).save(
                os.path.join(args.out, f"face_{name}_body2x.png"))
        print(f"  ✓ {name:10s} → face_{name}*.png  style={app.style}")
        _reset(app)


def _reset(app):
    import wb_motion as MOTION
    app._emotion = MOTION.EMOTION_NEUTRAL
    app._emotion_until = 0.0
    app._react_face = None
    app._react_until = 0.0
    app._morph_state = ""
    app.style = "q"
    app._blinker.blink_t0 = 0.0
    app._blinker.blink_total = 0.0
    app._blinker.close_depth = 1.0
    app._blinker.seg_close, app._blinker.seg_hold, app._blinker.seg_open = \
        0.099, 0.066, 0.165


if __name__ == "__main__":
    raise SystemExit(main())
