# -*- coding: utf-8 -*-
r"""3D 桌宠 P0 收口专项测试（联动关闭 + 实时切换）

⚠️ 需要托管 Python 3.13.12（glfw/moderngl 仅装在那里）：
    C:\Users\htft2\.workbuddy\binaries\python\versions\3.13.12\python.exe tools\test_pet3d_p0.py
其他解释器下自动 SKIP（glfw 缺失）——跳过不是通过，回归时用托管解释器跑。

验证点（对照《项目最终目标与工作交接.md》§7 两个 P0）：
  A. linked_should_quit 纯函数：三值语义（探测失败不判定 / 从未见不退 /
     空窗持续 ≥ grace 才退 / agent 回归清空计时）
  B. 集成：WB_PET_LINKED=1 的 Pet3D，agent 全部消失 → 优雅退出（running=False）
  C. P0-② 实时切换：共享设置 pet_mode 变化 → 优雅退出；切回后不误退
  D. 降级：无 WB_PET_LINKED → 永不联动退出；探测失败 → 不判定
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

try:
    import wb_pet3d as P3                          # noqa: E402  （需 glfw → 托管 python）
    _SKIP = None
except ImportError as e:
    P3, _SKIP = None, f"glfw/moderngl 不可用（{e.name}）——用托管 python 3.13.12 跑"

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


GRACE = 3.0

if P3 is None:
    print(f"SKIP: {_SKIP}")
    print("SKIP: 0 PASS / 0 FAIL（本解释器跑不了 3D，请在托管 python 下重跑）")
    sys.exit(0)

print("\n[A] linked_should_quit 纯函数（三值语义；linked_expected 防孤儿）")
now = 1000.0
q, seen, empty = P3.FOLLOW.linked_should_quit(False, None, set(), now, GRACE)
check("A1: 手动跑（非守望拉起）从未见 agent → 不退", q is False and seen is False)
q, seen, empty = P3.FOLLOW.linked_should_quit(False, None, set(), now + 9.9, GRACE)
check("A1b: 非守望拉起持续 9.9s → 仍不退", q is False)
q, seen, empty = P3.FOLLOW.linked_should_quit(
    False, None, set(), now, GRACE, linked_expected=True)
check("A1c: 守望拉起但从未见 agent → 空窗起点已记",
      q is False and empty == now)
q, seen, empty = P3.FOLLOW.linked_should_quit(
    False, now, set(), now + GRACE + 0.1, GRACE, linked_expected=True)
check("A1d: 守望拉起 + 宽限过 → 退出（防孤儿，对齐 2D B9）", q is True)
q, seen, empty = P3.FOLLOW.linked_should_quit(False, None, {"workbuddy"}, now, GRACE)
check("A2: 首次探测到 agent → seen=True", q is False and seen is True)
q, seen, empty = P3.FOLLOW.linked_should_quit(True, None, set(), now, GRACE)
check("A3: agent 清空 → 记空窗起点，暂不退", q is False and empty == now)
q, seen, empty = P3.FOLLOW.linked_should_quit(
    True, now + 2.0, set(), now + 2.5, GRACE)
check("A5: 空窗未满 grace → 不退", q is False)
q, seen, empty = P3.FOLLOW.linked_should_quit(True, now - 1.0, {"zcode"}, now + 1.0, GRACE)
check("A6: agent 回归 → 清空计时", q is False and empty is None)
q, seen, empty = P3.FOLLOW.linked_should_quit(True, now, None, now + 9.0, GRACE)
check("A7: 探测失败（None）→ 不判定、状态保持", q is False and seen is True)

print("\n[B] 集成：WB_PET_LINKED=1 的 Pet3D，agent 全消失 → 优雅退出")
os.environ["WB_PET_LINKED"] = "1"
app = P3.Pet3D(P3.default_model_path(), scale=0.8)
check("B1: env 读取 → linked_expected=True", app.linked_expected is True)
saved_detect = P3.PRESENCE.detect_once
P3.PRESENCE.detect_once = lambda: set()            # 所有 agent 消失
now = time.time()
app._linked_and_mode_tick(now)
app._linked_and_mode_tick(now + 1.0)
check("B2: 空窗未满 grace → 仍在运行", app.running is True)
app._linked_and_mode_tick(now + GRACE + 0.5)
check("B3: 空窗持续 ≥ grace → 联动退出（running=False）", app.running is False)

print("\n[C] P0-② 实时切换：共享设置 pet_mode 变化 → 优雅退出")
app.running = True
app.linked_expected = False                        # 关掉联动，单测切换路径
shared = P3.SHARED_SETTINGS
saved_shared = None
if os.path.exists(shared):
    with open(shared, encoding="utf-8") as f:
        saved_shared = f.read()
try:
    P3.write_shared_pet_mode("2d")                 # 用户（或菜单）切到 2D
    app._linked_and_mode_tick(now + 10.0)
    check("C1: pet_mode 变为 2d → 3D 优雅退出", app.running is False)
    app.running = True
    P3.write_shared_pet_mode("3d")                 # 切回来
    app._linked_and_mode_tick(now + 11.0)
    check("C2: pet_mode=3d → 不误退", app.running is True)
finally:
    if saved_shared is not None:
        with open(shared, "w", encoding="utf-8") as f:
            f.write(saved_shared)                  # 还原用户真实设置
    else:
        os.remove(shared)
P3.PRESENCE.detect_once = saved_detect

print("\n[D] 托盘入口：switch_to('2d')（写盘 + 退出）")
app2 = P3.Pet3D(P3.default_model_path(), scale=0.8)
app2._report_event = None
stub = {"called": None}
app2.switch_to_orig = app2.switch_to


def _spy_switch(mode):
    stub["called"] = mode
    app2.running = False


app2.switch_to = _spy_switch
tray_stub = type("T", (), {"_on_command": lambda self, cmd: None})()
import wb_tray as TRAY                            # noqa: E402
t = TRAY.Tray.__new__(TRAY.Tray)
t.pet = app2
t._on_command(TRAY.ID_MODE2D)
check("D1: 托盘「切换到 2D」→ switch_to('2d') 被调用", stub["called"] == "2d")
app2.close()

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
