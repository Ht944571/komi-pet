# -*- coding: utf-8 -*-
r"""缩放切换回归测试（自动化验证，不依赖人工点击）

验证点：
  A. 切换尺寸前后 ANIM/TICK 定时器都活着（_anim_tick / draw 仍在被调用）
  B. 切换后窗口尺寸确实变了，且 hwnd 保持不变（定时器不丢）
  C. 切换后点击身体仍有互动反馈（粒子生成 + react 事件 + 重绘）
  D. 切换后吞掉/保留：双击泡泡区域仍然只是开看板路径（这里只验证区域判定未失效）

用法：python tools/test_scale_switch.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_whale_win as W  # noqa: E402

_u = W._user32
_u.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
_u.PostMessageW.restype = wt.BOOL

WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
MK_LBUTTON = 0x0001


def _makelparam(x, y):
    return (y << 16) | (x & 0xFFFF)


def legacy_recreate(app):
    """修复前的实现：新建 hwnd + 销毁旧 hwnd（用于复现 bug，证明根因）。"""
    lay = app._layout()
    old = app.hwnd
    x, y = app._window_xy(old)
    hwnd = app._create_window(0, 0, lay["W"], lay["H"])
    W._WINDOWS.pop(old, None)
    app.hwnd = hwnd
    W._WINDOWS[hwnd] = app
    app.surf.close()
    app.surf = W.Surface(lay["W"], lay["H"])
    app._build_sprite_cache()
    ax, ay, aw, ah = W.work_area()
    nx = max(ax + 4, min(x, ax + aw - lay["W"] - 4))
    ny = max(ay + 4, min(y, ay + ah - lay["H"] - 4))
    app._move_window(hwnd, int(nx), int(ny))
    app._drawn_sig = None
    app.draw()
    W._user32.ShowWindow(hwnd, W.SW_SHOWNA)
    W._user32.DestroyWindow(old)


def main():
    app = W.WhalePet(run_seconds=26)

    # 帧序列现在是**唯一形态**，不能置 None —— 那样她会整个消失。
    # 改为把动作钉在「待机」（循环段，帧号可预期），让画面在本测试里保持确定。
    # （本机此刻若真有会话在跑，动作会被 _sync_bubble_mode 驱动，但画的始终是同一套帧，
    #   不影响本测试要验的「重建窗口后定时器是否还活着」。）
    app._wr.act = "idle"
    app._drawn_sig = None
    hwnd0 = app.hwnd

    counters = {"anim": 0, "draw": 0, "tick": 0}
    _anim, _draw, _tick = app._anim_tick, app.draw, app.tick

    def anim():
        counters["anim"] += 1
        _anim()

    def draw(*a, **k):
        counters["draw"] += 1
        _draw(*a, **k)

    def tick(*a, **k):
        counters["tick"] += 1
        _tick(*a, **k)

    app._anim_tick, app.draw, app.tick = anim, draw, tick

    # 拦截 open_dashboard：只计数，不真的开浏览器
    dash = {"n": 0}
    app.open_dashboard = lambda *a, **k: dash.__setitem__("n", dash["n"] + 1)

    report = {}

    def rect(hwnd):
        r = W.RECT()
        _u.GetWindowRect(hwnd, ctypes.byref(r))
        return (r.right - r.left, r.bottom - r.top)

    def click_body(times=2):
        lay = app._layout()
        x = int(lay["W"] / 2)
        y = int(lay["bubble_h"] + lay["pet_h"] * 0.55)
        for _ in range(times):
            _u.PostMessageW(app.hwnd, WM_LBUTTONDOWN, MK_LBUTTON,
                            _makelparam(x, y))
            _u.PostMessageW(app.hwnd, WM_LBUTTONUP, 0, _makelparam(x, y))
            time.sleep(0.12)

    def scenario():
        time.sleep(3.0)
        report["before"] = {"anim": counters["anim"], "draw": counters["draw"],
                            "tick": counters["tick"], "rect": rect(app.hwnd)}
        # 单击身体（切换前）
        click_body(2)
        time.sleep(0.6)
        report["before_react"] = {
            "particles": len(app._particles),
            "wobble_until": round(app._wobble_until - time.time(), 2),
            "quote": app._quote,
        }

        # ---- 切换尺寸（与右键菜单相同的代码路径；不落盘避免覆盖用户设置）----
        orig_scale = app.scale
        app.scale = 2.0
        if "--legacy" in sys.argv:      # 复现修复前的行为
            legacy_recreate(app)
        else:
            app._recreate_window()

        counters.update(anim=0, draw=0, tick=0)
        time.sleep(3.0)
        report["after"] = {"anim": counters["anim"], "draw": counters["draw"],
                           "tick": counters["tick"], "rect": rect(app.hwnd),
                           "hwnd_same": app.hwnd == hwnd0}
        # 单击身体（切换后）
        click_body(2)
        time.sleep(0.6)
        report["after_react"] = {
            "particles": len(app._particles),
            "wobble_until": round(app._wobble_until - time.time(), 2),
            "quote": app._quote,
        }

        # 切换后：双击"想法泡泡"仍应开看板；双击身体不应开看板（走连击互动）
        lay = app._layout()
        # ⚠️ 2026-09-27：气泡改为「锚定立绘实际头顶」（见 WhalePet._bubble_box），
        #    不再固定贴窗口顶部 —— 点击点必须取气泡椭圆的**真实中心**，
        #    沿用旧的 lay["bubble_h"]/2 会落到气泡上方的空白里（用例会挂）。
        _bx, _by, _bw, _bh = app._bubble_box(lay)
        _u.PostMessageW(app.hwnd, 0x0203, MK_LBUTTON,
                        _makelparam(int(lay["W"] / 2), int(_by + _bh / 2)))
        time.sleep(0.4)
        n_after_bubble = dash["n"]
        _u.PostMessageW(app.hwnd, 0x0203, MK_LBUTTON,
                        _makelparam(int(lay["W"] / 2),
                                    int(lay["bubble_h"] + lay["pet_h"] * 0.55)))
        time.sleep(0.4)
        report["dashboard"] = {"bubble_dblclk": n_after_bubble,
                               "after_body_dblclk": dash["n"]}

        # 还原用户原始尺寸设置（测试期间从未写过 settings 文件，这里兜底）
        app.scale = orig_scale
        try:
            app._save_settings()
        except Exception:
            pass
        W._user32.PostQuitMessage(0)

    import threading
    threading.Thread(target=scenario, daemon=True).start()

    # 看门狗：正常情况 tick() 会在 run_seconds 后自行 PostQuitMessage；
    # 若 TICK 定时器随旧窗口一起死掉，计时自检永不触发 → 进程挂死 → 这里兜底报错。
    def watchdog():
        time.sleep(45)
        print("\n==== 缩放切换回归测试结果（看门狗中断）====")
        for k in ("before", "before_react", "after", "after_react"):
            if k in report:
                print(f"{k:14s}: {report[k]}")
        print("FAIL: 应用未能自行退出 —— TICK 定时器已死（bug 复现）")
        sys.stdout.flush()
        os._exit(1)

    threading.Thread(target=watchdog, daemon=True).start()
    app.run()

    print("\n==== 缩放切换回归测试结果 ====")
    for k in ("before", "before_react", "after", "after_react", "dashboard"):
        print(f"{k:14s}: {report.get(k)}")

    ok = True
    b, a = report["before"], report["after"]
    if a["anim"] < 20:
        print("FAIL: 切换后 ANIM 定时器未运行（动画帧已死）"); ok = False
    if a["tick"] < 1:
        print("FAIL: 切换后 TICK 定时器未运行（数据刷新已死）"); ok = False
    if a["rect"] == b["rect"]:
        print("FAIL: 切换后窗口尺寸未变化"); ok = False
    if not a["hwnd_same"]:
        print("FAIL: hwnd 被重建（定时器会随旧窗口销毁）"); ok = False
    if report["after_react"]["particles"] <= 0:
        print("FAIL: 切换后点击身体无粒子（互动反馈失效）"); ok = False
    if report["after_react"]["wobble_until"] <= 0:
        print("FAIL: 切换后点击身体无反应状态（摇摆反馈未生效）"); ok = False
    d = report.get("dashboard", {})
    if d.get("bubble_dblclk") != 1:
        print("FAIL: 切换后双击泡泡未触发看板"); ok = False
    if d.get("after_body_dblclk") != 1:
        print("FAIL: 切换后双击身体误触发看板（应只走互动）"); ok = False
    print("结论：", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
