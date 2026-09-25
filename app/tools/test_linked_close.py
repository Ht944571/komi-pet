# -*- coding: utf-8 -*-
r"""联动关闭测试：WorkBuddy 应用退出 → 桌宠窗口与进程一并退出。

覆盖：
  · 桌宠侧 _check_linked_close 全部分支（无基线 / 存活建基线 / 单次消失不关 /
    连续消失但未过宽限 / 连续消失且过宽限→关 / 无法判定不关 / 消失后恢复计数清零 / 已关不重复）；
  · 守望侧 should_kill_on_session_end 纯判定（首次不触发 / 会话切换不触发 / 由运行→结束才触发）；
  · 常量与跨模块一致性（WB_PROC_NAME 与守望的 WORKBUDDY_EXE 必须一致）。

用法：python tools/test_linked_close.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_whale_win as W                                   # noqa: E402
import wb_motion as M                                      # noqa: E402
import wb_whale_watcher as WATCH                           # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name if cond else f"{name}  {detail}")
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))


# ============================================================
# Part A：常量与跨模块一致性
# ============================================================
print("\n[A] 常量与一致性")
check("A1 联动关闭总开关存在", hasattr(M, "LINKED_CLOSE_ON") and M.LINKED_CLOSE_ON is True)
check("A2 宽限期为正", getattr(M, "LINKED_CLOSE_GRACE_S", 0) > 0)
check("A3 确认次数 ≥1", getattr(M, "LINKED_CLOSE_CONFIRM_CHECKS", 0) >= 1)
check("A4 探测间隔为正", getattr(M, "LINKED_CLOSE_PROBE_S", 0) > 0)
check("A5 桌宠与守望的宿主进程名一致",
      W.WB_PROC_NAME == WATCH.WORKBUDDY_EXE,
      f"{W.WB_PROC_NAME} vs {WATCH.WORKBUDDY_EXE}")
check("A6 守望含灭杀与纯判定接口",
      hasattr(WATCH, "kill_whale") and hasattr(WATCH, "should_kill_on_session_end"))


# ============================================================
# Part B：桌宠侧 _check_linked_close 状态机
# ============================================================
print("\n[B] 桌宠侧 _check_linked_close")


class PetStub:
    ok_autodismiss_on = True      # 新状态契约：行为开关是实例标志（Stub 默认出厂值）
    linked_close_on = True
    """只保留 _check_linked_close 需要的那几个入口；_wb_alive 可手动设值。"""

    def __init__(self, alive=True, expected=True):
        self._linked_closed = False
        self._linked_expected = expected
        self._wb_seen_once = False
        self._wb_gone_since = 0.0
        self._wb_gone_checks = 0
        self._wb_alive_val = alive
        self.hwnd = 0                       # 假 hwnd：PostMessageW 到 0 安全返回
        self.events = []

    def _wb_alive(self, now):
        return self._wb_alive_val

    def _report_event(self, *a, **k):
        self.events.append((a, k))

    def _linked_quit(self):
        return W.WhalePet._linked_quit(self)


def tick(s, now):
    W.WhalePet._check_linked_close(s, now)


# B1 无基线（既非守望拉起、也从未见过 WorkBuddy）→ 永不自动关
s = PetStub(alive=False, expected=False)
tick(s, 100.0)
check("B1 无基线不关（手动测试友好）",
      s._linked_closed is False and s._wb_gone_checks == 0,
      f"closed={s._linked_closed} checks={s._wb_gone_checks}")

# B2 存活 → 建立基线，不关
s = PetStub(alive=True, expected=True)
tick(s, 100.0)
check("B2 存活建立基线", s._wb_seen_once is True and s._linked_closed is False,
      f"seen={s._wb_seen_once} closed={s._linked_closed}")

# B3 单次"消失"：记录起点，但确认次数不足 → 不关
s._wb_alive_val = False
tick(s, 101.0)
check("B3 单次消失不关（确认次数不足）",
      s._wb_gone_since == 101.0 and s._wb_gone_checks == 1 and s._linked_closed is False,
      f"since={s._wb_gone_since} checks={s._wb_gone_checks}")

# B4 连续消失但未过宽限 → 仍不关
tick(s, 102.0)
check("B4 连续消失但未过宽限不关",
      s._wb_gone_checks == 2 and s._linked_closed is False,
      f"checks={s._wb_gone_checks} closed={s._linked_closed}")

# B5 连续消失且过宽限 → 关（并上报事件）
elapsed = M.LINKED_CLOSE_GRACE_S + 0.5
tick(s, 101.0 + elapsed)
check("B5 连续消失且过宽限 → 关闭",
      s._linked_closed is True, f"closed={s._linked_closed}")
check("B5 上报 linked_close 事件",
      ("linked_close",) in [e[0] for e in s.events],
      f"events={[e[0] for e in s.events]}")

# B6 已关后重复调用不重复上报
ev_before = len(s.events)
tick(s, 101.0 + elapsed + 5.0)
check("B6 已关不重复处理", len(s.events) == ev_before)

# B7 无法判定（None）→ 不关（兜底：宁可留着，不误杀）
s2 = PetStub(alive=True, expected=True)
tick(s2, 200.0)                          # 建基线
s2._wb_alive_val = None
tick(s2, 210.0)
tick(s2, 220.0)
check("B7 无法判定 → 不关", s2._linked_closed is False,
      f"closed={s2._linked_closed}")

# B8 消失后又恢复 → 计数与起点清零
s3 = PetStub(alive=True, expected=True)
tick(s3, 300.0)                          # 基线
s3._wb_alive_val = False
tick(s3, 301.0)                          # 开始消失计时
s3._wb_alive_val = True
tick(s3, 302.0)                          # 恢复
check("B8 恢复后清零",
      s3._wb_gone_since == 0.0 and s3._wb_gone_checks == 0 and s3._linked_closed is False,
      f"since={s3._wb_gone_since} checks={s3._wb_gone_checks}")
s3._wb_alive_val = False
tick(s3, 303.0)
check("B8 恢复后重新计时（不沿用旧起点）",
      s3._wb_gone_since == 303.0 and s3._wb_gone_checks == 1,
      f"since={s3._wb_gone_since} checks={s3._wb_gone_checks}")

# B9 守望拉起（expected=True）但从未见过存活 → 也会在宽限后自退（防孤儿）
s4 = PetStub(alive=False, expected=True)
tick(s4, 400.0)
tick(s4, 400.0 + M.LINKED_CLOSE_GRACE_S + 0.5)
check("B9 守望拉起但从未见存活 → 宽限后自退",
      s4._linked_closed is True, f"closed={s4._linked_closed}")

# B10 总开关关闭 → 行为完全不生效（UI 开关时代：门在实例标志 linked_close_on，
# 常量只是出厂默认——见 tools/test_menu_toggles.py 的持久化往返）
s5 = PetStub(alive=False, expected=True)
s5.linked_close_on = False
tick(s5, 500.0)
tick(s5, 500.0 + M.LINKED_CLOSE_GRACE_S + 1.0)
check("B10 总开关关闭时不联动关", s5._linked_closed is False)


# ============================================================
# Part C：守望侧会话结束判定（纯函数）
# ============================================================
print("\n[C] 守望侧 should_kill_on_session_end（v2：活跃 agent 集合语义）")
# v2（2026-09-25 解耦）：入参从"进程实例 (pid, 创建时间)"改为"活跃 agent 集合"，
# 且 None 专指「探测未知」——未知绝不触发关闭（宁可留着，也不误杀）。
A = frozenset({"workbuddy"})
Z = frozenset({"zcode"})
c = WATCH.should_kill_on_session_end
check("C1 首次探测未完成（未探测→空）不触发", c(None, set()) is False)
check("C2 一直无 agent（空→空）不触发", c(set(), set()) is False)
check("C3 agent 启动（空→有）不触发", c(set(), A) is False)
check("C4 持续活跃（有→同）不触发", c(A, A) is False)
check("C5 换了别的 agent 但仍活跃（有→有）不触发", c(A, Z) is False)
check("C6 **最后一个 agent 退出（有→空）触发**", c(A, set()) is True)
check("C7 探测未知（有→None）不触发、绝不误杀", c(A, None) is False)
check("C8 首次即未知（未探测→None）不触发", c(None, None) is False)


print(f"\n=== 总结 ===\nPASS: {len(PASSED)}\nFAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
