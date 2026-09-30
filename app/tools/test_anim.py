# -*- coding: utf-8 -*-
r"""帧序列动作 + 状态机回归测试。

守护的不变量：
  [A] 取帧
    ① 写字只循环**写字段**，永不越过分界
    ② 展示段从分界帧播到末帧并**停住**
    ③ 待机(idle)/困了(sleepy) **整段循环**
    ④ 变困(doze)/拿本子(pickup) 单向推进、停在末帧
    ⑤ 醒来(wake) 是变困的**倒放**（同一时刻帧号首尾互补）
  [B] 状态机流转
    idle →(有任务) pickup →(播完) write →(任务完成) present →(播完) idle
    idle →(空闲超时) doze →(播完) sleepy →(有任务) wake →(播完) pickup
  [C] 真实素材：五个动作都能加载，内容框在画布内
  [D] 缺素材 → load() 返回 None，不崩

用法： py tools/test_anim.py
"""
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


print("  帧序列动作 + 状态机回归测试")
print("=" * 60)

meta = {"name": "write", "count": 49, "fps": 12.0,
        "canvas": [420, 565], "content_box": [0.15, 0.36, 0.84, 1.0]}
clip = A.AnimClip(meta, os.path.join(APP, "assets", "anim", "write"))
t = 500.0
split = clip.split

print("\n[A] 动作 → 帧号")
check("A1 写字从第 0 帧开始", clip.index(A.ACT_WRITE, t, t) == 0)
idx = [clip.index(A.ACT_WRITE, t, t + k / clip.fps) for k in range(split * 3)]
check("A2 写字段循环且**永不越过分界**", max(idx) < split,
      f"max={max(idx)} split={split}")
check("A3 展示段从分界帧开始", clip.index(A.ACT_PRESENT, t, t) == split)
tail = [clip.index(A.ACT_PRESENT, t, t + k / clip.fps)
        for k in range(int((clip.count - split) * 3))]
check("A4 展示段播到末帧后**停在末帧**",
      max(tail) == clip.count - 1 and tail[-1] == clip.count - 1,
      f"max={max(tail)} 末帧={clip.count - 1}")
check("A5 展示段单调推进（不回跳）",
      all(b >= a for a, b in zip(tail, tail[1:])))
cyc = [clip.index(A.ACT_IDLE, t, t + k / clip.fps) for k in range(clip.count * 2)]
check("A6 待机是**整段循环**（会越过分界）",
      max(cyc) == clip.count - 1 and cyc[0] == 0, f"max={max(cyc)}")
one = [clip.index(A.ACT_DOZE, t, t + k / clip.fps) for k in range(clip.count * 2)]
check("A7 变困单向推进并**停在末帧**",
      max(one) == clip.count - 1 and one[-1] == clip.count - 1, f"max={max(one)}")
wake = [clip.index(A.ACT_WAKE, t, t + k / clip.fps) for k in range(clip.count)]
check("A8 醒来是变困的**倒放**",
      all(w == clip.count - 1 - d for w, d in zip(wake, one[:clip.count])))

print("\n[B] 播完判定")
check("B1 刚起步不算播完", (t - t) < clip.duration())
check("B2 播够时长算播完", (clip.duration() + 0.2) >= clip.duration())

print("\n[C] 真实素材")
clips = A.load_all(os.path.join(APP, "assets"))
for name in A.ACTIONS:
    c = clips.get(name)
    check(f"C·{name} 已加载（{c.count if c else 0} 帧）", bool(c) and c.count > 10)
    if c:
        cb = c.content_box
        check(f"C·{name} 内容框在画布内",
              0 <= cb[0] and cb[2] <= 1.0 and 0 <= cb[1] and cb[3] <= 1.0, str(cb))

print("\n[D] 头大小对齐（HEAD_SCALE）")
_w = A.head_scale_of("write")
check("D1 写字是基准（倍数 = 1.0）", abs(_w - 1.0) < 1e-6, str(_w))
_g = [A.head_scale_of(n) for n in ("idle", "doze", "sleepy", "pickup")]
check("D2 四个待机动作倍数一致（切换时不跳）",
      max(_g) - min(_g) < 1e-6, str(_g))
check("D3 待机是**放大**（近景素材在画布里偏小）", _g[0] > 1.0, str(_g[0]))
check("D4 放大后不会横向溢出画布",
      all((b - a_) * A.head_scale_of(n) <= 1.0 for n, a_, b in
          ((n, 0.157, 0.841) for n in ("idle", "doze", "sleepy", "pickup"))),
      "内容宽 0.684 × 1.27 = 0.869")

print("\n[E] 状态机流转")
st = A.PetPhase(sleepy_after=10.0)
check("E1 起手是待机", st.act == A.ACT_IDLE, st.act)
check("E2 首次喂 n>0 直接写字（启动时任务已在跑，不播拿本子）",
      (st.feed(1, t) == "task_start") and st.act == A.ACT_WRITE, st.act)

st2 = A.PetPhase(sleepy_after=10.0)
st2.feed(0, t)                       # 建基线：空闲
check("E3 空闲 → 有任务：先拿本子",
      st2.feed(2, t + 1) == "task_start" and st2.act == A.ACT_PICKUP, st2.act)
st2.tick(t + 1 + st2._dur(A.ACT_PICKUP) + 0.01)
check("E4 拿本子播完 → 开始写字", st2.act == A.ACT_WRITE, st2.act)
check("E5 任务完成 → 举本子展示",
      st2.feed(0, t + 20) == "task_done" and st2.act == A.ACT_PRESENT, st2.act)
st2.tick(t + 20 + st2._dur(A.ACT_PRESENT) + 0.01)
check("E6 展示播完 → 回到待机", st2.act == A.ACT_IDLE, st2.act)

st3 = A.PetPhase(sleepy_after=10.0)
st3.feed(0, t)
st3.note_activity(t)
st3.tick(t + 11)
check("E7 待机超时 → 开始变困", st3.act == A.ACT_DOZE, st3.act)
st3.tick(t + 11 + st3._dur(A.ACT_DOZE) + 0.01)
check("E8 变困播完 → 困了（循环）", st3.act == A.ACT_SLEEPY, st3.act)
check("E9 睡着时来任务 → 先醒过来",
      st3.feed(3, t + 30) == "wake_up" and st3.act == A.ACT_WAKE, st3.act)
st3.tick(t + 30 + st3._dur(A.ACT_WAKE) + 0.01)
check("E10 醒来（有任务）→ 接拿本子", st3.act == A.ACT_PICKUP, st3.act)
st3.tick(t + 30 + st3._dur(A.ACT_WAKE) + st3._dur(A.ACT_PICKUP) + 0.02)
check("E11 拿本子播完 → 写字", st3.act == A.ACT_WRITE, st3.act)

print("\n[F] 缺素材")
check("F1 不存在的动作名 → None（不抛异常）",
      A.load("__no_such_anim__", os.path.join(APP, "assets")) is None)
check("F2 素材全缺时 clip_name 返回 None（不崩）", A.PetPhase().clip_name({}) is None)

print("\n" + "=" * 60)
print(f"  总结：PASS {len(PASSED)} / FAIL {len(FAILED)}")
for f in FAILED:
    print(f"    FAIL: {f}")
sys.exit(1 if FAILED else 0)
