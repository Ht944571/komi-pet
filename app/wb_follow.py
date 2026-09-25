# -*- coding: utf-8 -*-
r"""wb_follow.py — 跟随模式（聚焦信号）纯逻辑层
================================================================

设计依据：docs/桌宠跨Agent体验设计.md §2（跟随）§3（无感）§7（取舍）。
本模块**不 import Win32**——前台探测在 wb_whale_win 的动画帧里做（钩子优先、
264ms 轮询兜底，回调/轮询都只拿 hwnd → 进程名/标题），身份解析与去抖状态机
放这里，纯逻辑可单测。

三条铁律（设计文档原文，实现者必读）：
  1. **未知态不要猜**（§2.3）：前台不属于任何 agent → 明确未知，猜错比不猜糟；
  2. **去抖 300–500ms**（§2.4）：一次 Alt+Tab 徽章变化 ≤ 1 次——闪 3 次一定被骂；
  3. **高频往返退化**（§3.3）：同一 agent 5s 内再聚焦 → 纯颜色渐变，不播完整过渡。
"""

import os
import time

try:
    import wb_motion as MOTION
except ImportError:                                  # 允许从任意 cwd 导入
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import wb_motion as MOTION


# ---------------------------------------------------------------------------
# 前台 → agent 推断（设计 §2.1：L1 进程名 / L2 标题关键字 / 都不中 = 未知）
# ---------------------------------------------------------------------------

def hint_map_from_registry(agents):
    """登记册条目 → {key: (procs_frozenset, title_hints_tuple)}（只含启用的）。

    窗口特征来自两处：
      · presence.procs        —— L1：前台窗口所属进程名（小写）
      · presence.title_hints  —— L2：标题关键字（可选；缺省回退 label 小写）
    """
    out = {}
    for key, spec in (agents or {}).items():
        if not spec.get("enabled", True):
            continue
        pr = spec.get("presence") or {}
        procs = frozenset(str(p).lower().replace(".exe", "")
                          for p in (pr.get("procs") or []) if str(p).strip())
        hints = [str(h).lower() for h in (pr.get("title_hints") or [])]
        if not hints:
            hints = [str(spec.get("label") or key).lower()]
        out[key] = (procs, tuple(hints))
    return out


def resolve(hint_map, proc, title):
    """前台进程名 + 窗口标题 → agent key；都不中返回 None（**未知态，不猜**）。

    L1 进程名优先（最准）；L2 标题关键字兜底（Electron/终端类进程名不可分）。
    proc/title 传 None 表示这一轮拿不到（同样按未知处理）。
    """
    if not hint_map:
        return None
    p = (proc or "").lower().replace(".exe", "")
    if p:
        for key, (procs, _hints) in hint_map.items():
            if p in procs:
                return key
    t = (title or "").lower()
    if t:
        for key, (_procs, hints) in hint_map.items():
            if any(h and h in t for h in hints):
                return key
    return None


# ---------------------------------------------------------------------------
# P4 接续（设计 §5）：刚离开的 agent 若"似乎未结束"→ 角标；摘要用户触发才生成
# ---------------------------------------------------------------------------

def handoff_last_turn(db_path, agent_key, now, window_s=None):
    """接续检测：该 agent 最近一轮的结束时刻。

    返回 (last_epoch, title)——最近一轮结束在窗口内（"似乎未结束"）；否则 None。
    last_time 为 v_turn_total 的本地时间字符串（'YYYY-MM-DD HH:MM:SS'）。
    """
    import sqlite3
    window = MOTION.HANDOFF_WINDOW_S if window_s is None else window_s
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        try:
            row = conn.execute(
                """SELECT last_time, COALESCE(NULLIF(title, ''),
                                                 NULLIF(project, ''), '')
                     FROM v_turn_total WHERE agent = ?
                    ORDER BY last_time DESC LIMIT 1""", (agent_key,)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        last_str, title = str(row[0]), str(row[1])
        st = time.strptime(last_str[:19], "%Y-%m-%d %H:%M:%S")
        last = time.mktime(st)
        age = now - last
        if 0 <= age <= window:
            return last, title
        return None
    except Exception:
        return None


def linked_should_quit(seen, empty_since, agents, now, grace_s,
                       linked_expected=False):
    """联动关闭判定（纯函数；三值语义对齐 2D：宁可留着，不误杀）。

    agents = 本轮探测到的活跃 agent 集合；None 表示探测失败（保持原状不判定）。
    linked_expected：守望拉起时为 True——此时即使从未探测到 agent，
    宽限后也自退（防孤儿；2D 的 B9 语义）。
    返回 (quit, seen', empty_since')。
    """
    if agents is None:
        return False, seen, empty_since
    if agents:
        return False, True, None
    if not (seen or linked_expected):
        return False, seen, empty_since          # 手动跑（非守望拉起）不误杀
    if empty_since is None:
        empty_since = now
    return (now - empty_since >= grace_s), seen, empty_since


def cycle_next(keys, current):
    """P3 全局热键的手动聚焦轮换：返回下一个聚焦 key。

    keys 按展示顺序（登记册启用 agent）；current 不在列表 → 从第一个开始；
    无候选 → None（调用方静默忽略）。
    """
    keys = [k for k in (keys or []) if k]
    if not keys:
        return None
    if current in keys:
        return keys[(keys.index(current) + 1) % len(keys)]
    return keys[0]


# ---------------------------------------------------------------------------
# 去抖状态机（设计 §2.4 / §3.3）——事件只在「确认切换」时发，一次 Alt+Tab ≤ 1 条
# ---------------------------------------------------------------------------

class FollowTracker:
    """跟随去抖状态机。

    update(key, now)：
      key = 本轮解析出的前台 agent（None = 未知态原始输入）
      返回 None（无转换）或 {"event": "change"|"return"|"unknown",
                             "key": key|None, "from": 上一个身份}

    语义：
      · 原始输入在去抖窗口内翻来翻去 → 只有**最终停留**者会提交（≤1 条事件）；
      · 原始输入回到当前身份 → 完全无事件（高频往返的极短闪烁不可见）；
      · 确认切走时记录 left_at；同一 agent 在 FOLLOW_RETURN_S 内再聚焦 →
        event="return"（渲染层退化为纯颜色渐变）；
      · 确认进入未知态 → event="unknown"（渲染层：徽章半透明 + 灰）。
    """

    def __init__(self, debounce_s=None, return_s=None):
        self.debounce_s = MOTION.FOLLOW_DEBOUNCE_S if debounce_s is None else debounce_s
        self.return_s = MOTION.FOLLOW_RETURN_S if return_s is None else return_s
        self.current = None            # 已确认身份（None = 未知态）
        self._pending = None           # 去抖中的候选
        self._pending_since = 0.0
        self.left_at = {}              # key → 确认离开的时刻（高频往返判定）

    def force(self, key, now=None):
        """手动聚焦后同步状态机：current/pending 直接置为 key，不产生事件；
        此后跟随从该身份继续（与现实不符的部分会照常去抖提交）。"""
        import time as _t
        now = _t.time() if now is None else now
        self.current = key
        self._pending = key
        self._pending_since = now

    def update(self, key, now):
        if key != self._pending:
            self._pending = key
            self._pending_since = now
        if self._pending == self.current:
            return None                # 原始输入回到已确认身份 → 无事件（去抖含"回归"）
        if now - self._pending_since < self.debounce_s:
            return None                # 去抖窗口内：还没"停留"，不提交
        prev, self.current = self.current, self._pending
        if self._pending is None:
            if prev is not None:
                self.left_at[prev] = now
            return {"event": "unknown", "key": None, "from": prev}
        key = self._pending
        kind = "change"
        left = self.left_at.get(key)
        if left is not None and prev != key and now - left <= self.return_s:
            kind = "return"            # 高频往返：退化为纯颜色渐变
        if prev is not None:
            self.left_at[prev] = now
        return {"event": kind, "key": key, "from": prev}
