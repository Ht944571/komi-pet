# -*- coding: utf-8 -*-
r"""P2 收尾：行为开关（OK 自动收起 / 联动关闭）UI 化 + 持久化 专项测试

交接说明 §9 P2：「OK 自动消失 / 联动关闭没有 UI 开关，只能改 wb_motion.py 的
*_ON 后重启」→ 现在右键菜单可切、写进 .whale_settings.json、重启恢复。

验证点：
  A. 默认值来自 wb_motion 常量（出厂默认唯一出处）
  B. 开关方法：翻转实例标志 + 写设置文件
  C. 持久化往返：重启（重建实例）后从设置恢复
  D. 行为生效：关掉后 _ok_focus_check 不触发自动收起、_check_linked_close 短路
  E. wb_motion 常量不被运行时污染（红线 2：动效数字唯一出处不被 UI 改写）

用法：python tools/test_menu_toggles.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_motion as MOTION                     # noqa: E402
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


TMP = os.path.join(HERE, "_tmp_toggle_test")
os.makedirs(TMP, exist_ok=True)
settings_file = os.path.join(TMP, ".whale_settings.json")


class _SettingsPatched:
    """把 SETTINGS_FILE 指到临时文件（实例的读写都发生在这个窗口期内）。"""

    def __enter__(self):
        self.saved = W.SETTINGS_FILE
        W.SETTINGS_FILE = settings_file
        return self

    def __exit__(self, *exc):
        W.SETTINGS_FILE = self.saved
        return False


def make_app(settings=None):
    with _SettingsPatched():
        if settings is not None:
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        elif os.path.exists(settings_file):
            os.remove(settings_file)
        return W.WhalePet(run_seconds=2)


print("\n[A] 默认值来自 wb_motion 常量")
app = make_app()
check("A1: ok_autodismiss 默认 = MOTION.OK_AUTODISMISS_ON",
      app.ok_autodismiss_on == MOTION.OK_AUTODISMISS_ON)
check("A2: linked_close 默认 = MOTION.LINKED_CLOSE_ON",
      app.linked_close_on == MOTION.LINKED_CLOSE_ON)

print("\n[B] 开关方法：翻转 + 持久化")
with _SettingsPatched():
    app._toggle_ok_autodismiss()
    app._toggle_linked_close()
    saved = json.load(open(settings_file, encoding="utf-8"))
check("B1: 两个开关都被翻转",
      app.ok_autodismiss_on is False and app.linked_close_on is False)
check("B2: 翻转后设置文件已写盘",
      saved.get("ok_autodismiss") is False and saved.get("linked_close") is False)

print("\n[C] 持久化往返：重建实例（模拟重启）")
app2 = make_app(saved)
check("C1: 重启后从设置恢复（False）",
      app2.ok_autodismiss_on is False and app2.linked_close_on is False)
with _SettingsPatched():
    app2._toggle_ok_autodismiss()
    app2._toggle_linked_close()
    saved2 = json.load(open(settings_file, encoding="utf-8"))
check("C2: 再翻转 → 回到 True 且写盘",
      saved2.get("ok_autodismiss") is True and saved2.get("linked_close") is True)

print("\n[D] 行为生效：关掉后对应路径短路")
now = time.time()
# D1/D2：OK 自动收起
app2._bub_mode = W.BUBBLE_OK
app2._ok_entered_at = now - 10
app2._ok_fg_at_entry = True
app2._wb_fg_prev = False
app2._is_wb_focused = lambda now: True          # 前台焦点不依赖真实桌面状态
dismissed = []
app2._ok_auto_dismiss = lambda now=None: dismissed.append(1)
app2.ok_autodismiss_on = False
app2._ok_focus_check(now)
check("D1: 关闭自动收起 → 满足焦点条件也不触发 dismiss", not dismissed)
app2.ok_autodismiss_on = True
app2._ok_focus_check(now)
check("D2: 打开后同条件触发 dismiss", bool(dismissed))
# D3/D4：联动关闭
app3 = make_app({"linked_close": False})
app3._linked_expected = True
app3._wb_seen_once = True
app3._wb_alive = lambda now: False              # 强造"agent 已全部消失"
app3._wb_gone_since = time.time() - 10          # 宽限已过（只测开关的短路作用）
quit_flag = []
app3._linked_quit = lambda: quit_flag.append(1)
app3.linked_close_on = False
for _ in range(3):
    app3._check_linked_close(time.time())
check("D3: 关闭联动 → agent 全消失也不退出", not quit_flag)
app3.linked_close_on = True
for _ in range(3):
    app3._check_linked_close(time.time())
check("D4: 打开联动 → 连续消失判定后退出", bool(quit_flag))
app.close()
app2.close()
app3.close()

print("\n[E] wb_motion 常量不被运行时污染（红线 2）")
check("E1: 常量保持出厂值",
      MOTION.OK_AUTODISMISS_ON is True and MOTION.LINKED_CLOSE_ON is True)

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
