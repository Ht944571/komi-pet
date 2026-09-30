#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_diag_active.py — 只读诊断：桌宠为什么在/不在写字态（逐会话给原因）
================================================================================
写字态的**唯一判据**是活跃会话数 n（`wb_hover_core._active_sessions`）。
她"一直卡在写字状态"时，这个脚本一眼看出是哪个会话在撑 n、以及它凭什么算活跃。

它会打印：
  · 宿主库里所有 status='working' 的会话 + 每个会话的判定结论与原因
  · ODS 近窗（ODS_ACTIVE_SEC）内有**带用量调用**的会话
  · `_active_sessions` 实际返回的 n（= 桌宠看到的值，与气泡"运行中"同源）
  · 三个阈值的当前取值（ACTIVE_STALE_SEC / ODS_ACTIVE_SEC / NO_CALL_GRACE_SEC）

只读，不改任何东西。用法：<托管python> app/tools/_diag_active.py
"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_hover_core as C                                  # noqa: E402


def _age(ms_or_s):
    t = C._to_epoch(ms_or_s)
    return (time.time() - t) if t else None


def main():
    p = C.load_paths()
    print("阈值：ACTIVE_STALE_SEC=%s（显示层「还在干活」上限） · ODS_ACTIVE_SEC=%s · "
          "NO_CALL_GRACE_SEC=%s（零调用宽限） · WB_STALE_AFTER_SEC=%s（写库真修复）"
          % (C.ACTIVE_STALE_SEC, C.ODS_ACTIVE_SEC, C.NO_CALL_GRACE_SEC,
             C.WB_STALE_AFTER_SEC))

    conn = sqlite3.connect(f"file:{p['db_path']}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row

    print("\n=== ① 宿主库 status='working' 的会话 ===")
    try:
        wb = sqlite3.connect(f"file:{p['workbuddy_db']}?mode=ro", uri=True, timeout=3)
        wb.row_factory = sqlite3.Row
        rows = wb.execute("SELECT id, title, last_activity_at FROM sessions "
                          "WHERE status='working' ORDER BY last_activity_at DESC").fetchall()
        wb.close()
    except Exception as e:
        rows = []
        print("  读不到宿主库：", e)
    if not rows:
        print("  （无）")
    for r in rows:
        mx = conn.execute("""SELECT MAX(ts_ms) FROM ods_jsonl_event
                             WHERE session_id=? AND raw_usage_json IS NOT NULL""",
                          (r["id"],)).fetchone()[0]
        a_call, a_act = _age(mx), _age(r["last_activity_at"])
        if a_call is not None and a_call <= C.ACTIVE_STALE_SEC:
            why = "✅ 活跃：最近一次 LLM 调用 %.0fs 前" % a_call
        elif a_call is not None:
            why = "❌ 排除：最近一次调用是 %.0f 分钟前（> %ds）" % (a_call / 60, C.ACTIVE_STALE_SEC)
        elif a_act is not None and a_act <= C.NO_CALL_GRACE_SEC:
            why = "✅ 活跃：零调用但行刚动过 %.0fs 前（宽限期 %ds 内）" % (a_act, C.NO_CALL_GRACE_SEC)
        else:
            why = ("❌ 排除：零 LLM 调用，行也已 %.0fs 没动（> %ds 宽限）—— 幽灵会话"
                   % (a_act, C.NO_CALL_GRACE_SEC) if a_act is not None
                   else "❌ 排除：零调用且无任何时间痕迹（防永久活跃）")
        print("  %-24s %-14s 调用=%s → %s"
              % (str(r["id"])[:24], (r["title"] or "")[:14],
                 ("%.0fs 前" % a_call) if a_call is not None else "无", why))

    print("\n=== ② ODS 近 %ds 内有调用的会话 ===" % C.ODS_ACTIVE_SEC)
    now_ms = int(time.time() * 1000)
    found = False
    for sid, n, mx in conn.execute(
            """SELECT session_id, COUNT(*), MAX(ts_ms) FROM ods_jsonl_event
               WHERE raw_usage_json IS NOT NULL AND ts_ms >= ?
               GROUP BY session_id ORDER BY 3 DESC""",
            (now_ms - C.ODS_ACTIVE_SEC * 1000,)):
        found = True
        print("  %-24s 调用 %3d 笔  最新 %.0fs 前" % (str(sid)[:24], n, (now_ms - mx) / 1000))
    if not found:
        print("  （无 —— 没有会话在跑）")

    print("\n=== ③ 桌宠实际看到的活跃数（= 气泡「运行中」同源）===")
    act = C._active_sessions(conn, p["workbuddy_db"], C.ACTIVE_WINDOW_SEC)
    for d in act:
        print("  n+  sid=%s  calls=%s  最新 %.0fs 前"
              % (str(d.get("_sid"))[:24], d.get("api_calls"),
                 (time.time() * 1000 - (d.get("last_ts") or 0)) / 1000))
    print("  → n = %d  %s" % (len(act), "（她在写字）" if act else "（她应该待机/犯困）"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
