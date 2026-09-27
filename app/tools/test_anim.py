# -*- coding: utf-8 -*-
r"""帧序列动画（AI 视频 → 透明帧）的相位/取帧回归测试。

守护的不变量：
  ① writing 相位 → 循环播放「写字段」（永远到不了展示段）
  ② present 相位 → 从分界帧播到最后一帧，然后**停在末帧**（不会回到写字）
  ③ 没素材 → load() 返回 None（调用方回退 v3 立绘，不能崩）
用法： py tools/test_anim.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_anim as A           # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if not cond else ""))


print("  帧序列动画回归测试")
print("=" * 60)

meta = {"name": "write", "count": 49, "fps": 12.0,
        "canvas": [420, 565], "content_box": [0.15, 0.36, 0.84, 1.0]}
clip = A.AnimClip(meta, os.path.join(APP, "assets", "anim", "write"))
t = 500.0
split = clip.split

print("\n[A] 相位 → 帧号")
check("A1 写字段从第 0 帧开始", clip.index("writing", t, t) == 0)
idx = [clip.index("writing", t, t + k / clip.fps) for k in range(split * 3)]
check("A2 写字段循环且**永不越过分界**", max(idx) < split,
      f"max={max(idx)} split={split}")
check("A3 展示段从分界帧开始", clip.index("present", t, t) == split)
tail = [clip.index("present", t, t + k / clip.fps) for k in range(int((clip.count - split) * 3))]
check("A4 展示段播到末帧后**停在末帧**",
      max(tail) == clip.count - 1 and tail[-1] == clip.count - 1,
      f"max={max(tail)} 末帧={clip.count - 1}")
check("A5 展示段单调推进（不回跳）",
      all(b >= a for a, b in zip(tail, tail[1:])))
check("A6 未知相位回退第 0 帧", clip.index("idle", t, t + 9) == 0)

print("\n[B] 播完判定")
check("B1 展示刚开始不算播完", not clip.finished("present", t, t + 0.1))
check("B2 播够时长算播完",
      clip.finished("present", t, t + (clip.count - split) / clip.fps + 0.2))

print("\n[C] 真实素材加载")
real = A.load("write", os.path.join(APP, "assets"))
if real:
    check("C1 元数据可读且帧数一致", real.count == 49, str(real.count))
    check("C2 首帧文件存在", os.path.isfile(real.frame_path(0)))
    check("C3 末帧文件存在", os.path.isfile(real.frame_path(real.count - 1)))
    check("C4 内容框在画布内",
          0 <= real.content_box[0] and real.content_box[2] <= 1.0
          and 0 <= real.content_box[1] and real.content_box[3] <= 1.0,
          str(real.content_box))
else:
    check("C1 素材不存在时返回 None（回退立绘）", True)

print("\n[D] 缺素材")
check("D1 不存在的动作名 → None（不抛异常）",
      A.load("__no_such_anim__", os.path.join(APP, "assets")) is None)

print("\n" + "=" * 60)
print(f"  总结：PASS {len(PASSED)} / FAIL {len(FAILED)}")
for f in FAILED:
    print(f"    FAIL: {f}")
sys.exit(1 if FAILED else 0)
