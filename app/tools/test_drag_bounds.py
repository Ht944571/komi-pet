# -*- coding: utf-8 -*-
r"""桌宠拖动范围回归测试（上界 = 「可见内容顶」，不是窗口上沿）

背景 —— 2026-09-29 用户报障「桌宠在桌面向上移动只能达到这里」：
拖动 / 贴边吸附 / 启动摆放原先一律按**窗口矩形**夹取（`ay + 4`），
但窗口上沿之上还有一大段**透明留白**（气泡区 162*sc + 帧内上方的透明边，
合起来 300+px）→ 桌宠离屏幕上边白空一大截，怎么拖都上不去。
现在上界改按**可见内容顶**算：气泡开着 = 气泡上沿，关气泡 = 人物头顶。

验证点：
  A. 几何：`_dead_top` / 静态内容顶 == 实画内容顶 / 上界贴着工作区上沿 / 放开量 / 其余边界
  B. 行为：`_snap` 吸附到新上界；`_place` 原样恢复贴顶的保存位置；越下界仍被夹回

用法：python tools/test_drag_bounds.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_whale_win as W                       # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


ax = ay = aw = ah = 0

# 临时设置/位置文件（绝不碰用户真实配置）
tmpdir = os.path.join(HERE, "_tmp_drag_test")
os.makedirs(tmpdir, exist_ok=True)
pos_file = os.path.join(tmpdir, ".whale_pos.json")
saved_pos_file, saved_sf = W.POS_FILE, W.SETTINGS_FILE
W.POS_FILE = pos_file
W.SETTINGS_FILE = os.path.join(tmpdir, ".whale_settings.json")

app = None
try:
    for p in (pos_file, W.SETTINGS_FILE):
        if os.path.exists(p):
            os.remove(p)

    app = W.WhalePet(run_seconds=0)           # 构造时会 _place()（读上面的临时位置文件）
    # ⚠️ work_area() 必须在 WhalePet 之后取：`_set_dpi_aware()` 在 __init__ 里做，
    #    之前调 SPI_GETWORKAREA 拿到的是**虚拟化**坐标（150% 缩放下差 1.5 倍）。
    ax, ay, aw, ah = W.work_area()
    lay = app._layout()
    sc = lay["sc"]
    app.draw()                                # 画一帧 → `_spr_rect` 有值（内容顶可对照）

    print("\n[A] 拖动上界几何（工作区上沿之上的透明留白要还给用户）")
    _, bub_y, _, _ = app._bubble_box(lay)
    dead_on = app._dead_top(lay)
    check("A1: 气泡开着 → 死空间 = 气泡椭圆上沿",
          abs(dead_on - bub_y) <= 0.6, f"dead={dead_on} 气泡上沿={bub_y}")
    check(f"A2: 死空间确实是一大段留白（> 100px @ sc={sc}）", dead_on > 100 * sc,
          f"dead={dead_on:.0f}px sc={sc}")

    app.bubble_on = False
    dead_off = app._dead_top(lay)
    static_top = app._static_content_top(lay)
    check("A3: 关气泡 → 死空间 = 人物静态内容顶",
          static_top is not None and abs(dead_off - static_top) <= 0.6,
          f"dead={dead_off} static={static_top}")
    if static_top is not None:
        drawn_top = app._anim_content_top(lay)
        check("A4: 静态内容顶 == 实画内容顶（±12px：忽略 hover/呼吸/挤压）",
              abs(static_top - drawn_top) <= 12 * sc,
              f"static={static_top:.1f} drawn={drawn_top:.1f}")
    app.bubble_on = True

    bx0, by0, bx1, by1 = app._drag_bounds(lay)
    check("A5: 上界 = 工作区上沿 - 死空间 + 4（可见内容顶贴住屏幕上边）",
          by0 == int(ay - dead_on + 4), f"by0={by0} 期望={int(ay - dead_on + 4)}")
    check("A6: 相比旧上界（工作区上沿 + 4）至少放开 100px",
          (ay + 4) - by0 >= 100,
          f"旧={ay + 4} 新={by0} 放开={(ay + 4) - by0}px")
    check("A7: 下界/左右界不变（仍按窗口矩形贴工作区）",
          by1 == int(ay + ah - lay["H"] - 4)
          and bx1 == int(ax + aw - lay["W"] - 4)
          and bx0 == int(ax + 4),
          f"({bx0},{by0})-({bx1},{by1})")

    print("\n[B] 行为：贴边吸附 / 启动恢复位置")
    _nx, _ny = app._snap(bx0 + 10, by0 + 10)
    check("B1: 靠近上界松手 → 吸附到新上界（不是旧的工作区上沿）",
          _ny == by0, f"ny={_ny} by0={by0}")
    _nx2, _ny2 = app._snap(ax + aw + 50, ay + ah - lay["H"] - 6)
    check("B2: 靠近下界松手 → 吸附到下界", _ny2 == by1, f"ny={_ny2} by1={by1}")

    with open(pos_file, "w", encoding="utf-8") as f:
        json.dump({"x": ax + 100, "y": by0}, f)
    app._place()
    _x, _y = app._window_xy(app.hwnd)
    check("B3: 贴顶保存的位置原样恢复（不被旧上界拽回来）",
          abs(_y - by0) <= 1 and abs(_x - (ax + 100)) <= 1, f"恢复到 ({_x},{_y}) 期望=({ax + 100},{by0})")
    check("B4: 恢复出来的位置确实高于旧上界", _y < ay + 4, f"y={_y} 旧上界={ay + 4}")

    with open(pos_file, "w", encoding="utf-8") as f:
        json.dump({"x": ax + 100, "y": ay + ah + 800}, f)
    app._place()
    _x, _y = app._window_xy(app.hwnd)
    check("B5: 越下界的保存位置仍被夹回工作区内", _y == by1, f"y={_y} by1={by1}")
finally:
    if app is not None:
        try:
            app.close()
        except Exception:
            pass
    W.POS_FILE, W.SETTINGS_FILE = saved_pos_file, saved_sf

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
