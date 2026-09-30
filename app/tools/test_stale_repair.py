# -*- coding: utf-8 -*-
r"""残留会话兜底与修复测试（2026-09-28 事故回归）

事故：宿主崩溃/被杀不把 working 落回终态 → workbuddy.db 残留 status='working'
→ 桌宠永远显示"会话运行中"。修复分两层：
  A. 显示层兜底：_active_sessions 过滤停更超阈值的残留（wb_hover_core）
  B. 真修复：stale_working_sessions / repair_stale_working（写库回 completed）

验证点：
  1. 活跃会话：新鲜 working 算活跃；停更超阈值不算（ODS 有数据 / 只有 last_activity_at 两种路径）
  2. 时间戳口径：last_activity_at 毫秒与秒（旧数据）都能正确判停更
  3. 保守边界：无任何时间痕迹（last_act=0 且 ODS 无事件）不判残留
  4. query_db 端到端：残留不出现在 active（桌宠/悬浮球自动恢复）
  5. stale_working_sessions：只列残留、带 age_sec；库不可读 → []
  6. repair_stale_working：只修残留、复查 status='working'、新鲜会话不动；二次调用返回 0
  7. 库被锁死（写超时）→ 返回 -1 不抛异常

用法：python tools/test_stale_repair.py
"""

import os
import sys
import time
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_hover_core as CORE                          # noqa: E402
import wb_whale_win as W                              # noqa: E402（只调两个纯方法，不建窗口）

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def make_ods(rows, usage=True):
    """rows: list of (session_id, request_id, ts_ms)；usage=False 造"只有元数据"的行。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE ods_jsonl_event(
        id INTEGER PRIMARY KEY, session_id TEXT, request_id TEXT,
        event_type TEXT, role TEXT, ts_ms INTEGER, project TEXT,
        raw_usage_json TEXT, user_prompt TEXT, ai_title TEXT)""")
    for sid, rid, ts in rows:
        con.execute("""INSERT INTO ods_jsonl_event
            (session_id, request_id, event_type, role, ts_ms, project, raw_usage_json)
            VALUES (?, ?, 'message', 'assistant', ?, '测试项目', ?)""",
                    (sid, rid, ts,
                     '{"credit":0.1,"total_tokens":100}' if usage else None))
    con.commit()
    con.close()
    return path


def make_wb(sessions):
    """sessions: list of (id, title, status, last_activity_at)"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE sessions(
        id TEXT PRIMARY KEY, title TEXT, status TEXT, last_activity_at INTEGER)""")
    for sid, title, status, lat in sessions:
        con.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (sid, title, status, lat))
    con.commit()
    con.close()
    return path


NOW_MS = int(time.time() * 1000)
NOW_S = int(time.time())
STALE = CORE.WB_STALE_AFTER_SEC
OLD_MS = NOW_MS - (STALE + 600) * 1000        # 停更 16 分钟
OLD_S = NOW_S - (STALE + 600)                 # 停更 16 分钟（秒口径，旧数据）
FRESH_MS = NOW_MS - 2_000                     # 2 秒前

_tmp = []


def _keep(path):
    _tmp.append(path)
    return path


# ---- A. 显示层兜底：_active_sessions 过滤残留 ----
print("\n[A] 显示层兜底（_active_sessions 过滤）")
ods_a = _keep(make_ods([
    ("fresh1", "t1", FRESH_MS),
    ("stale1", "t2", OLD_MS),
]))
wb_a = _keep(make_wb([
    ("fresh1", "新鲜会话", "working", NOW_MS),
    ("stale1", "卡死会话", "working", OLD_MS),
    ("done1", "正常结束", "completed", OLD_MS),
]))
conn_a = sqlite3.connect(f"file:{ods_a}?mode=ro", uri=True)
conn_a.row_factory = sqlite3.Row
active_a = CORE._active_sessions(conn_a, wb_a, CORE.ACTIVE_WINDOW_SEC)
conn_a.close()
ids_a = [d["_wb_title"] for d in active_a]
check("A1: 新鲜 working 计入活跃", "新鲜会话" in ids_a, f"got {ids_a}")
check("A2: 停更 16 分钟的 working 不计入活跃", "卡死会话" not in ids_a, f"got {ids_a}")
check("A3: completed 不受影响", len(ids_a) == 1, f"got {ids_a}")

# 只有 last_activity_at、ODS 无事件（新对话刚开 vs 卡死同形态，靠时间区分）
ods_b = _keep(make_ods([]))
wb_b = _keep(make_wb([
    ("justopen", "刚开的新对话", "working", NOW_MS),
    ("deadlong", "卡死无数据", "working", OLD_MS),
    ("deadsec", "卡死秒口径", "working", OLD_S),
    ("notime", "无时间痕迹", "working", 0),
]))
conn_b = sqlite3.connect(f"file:{ods_b}?mode=ro", uri=True)
conn_b.row_factory = sqlite3.Row
active_b = CORE._active_sessions(conn_b, wb_b, CORE.ACTIVE_WINDOW_SEC)
conn_b.close()
ids_b = [d["_wb_title"] for d in active_b]
check("B1: 刚开的新对话（无 ODS 数据）仍算活跃", "刚开的新对话" in ids_b, f"got {ids_b}")
check("B2: last_activity_at 停更（毫秒）判残留", "卡死无数据" not in ids_b, f"got {ids_b}")
check("B3: last_activity_at 停更（秒，旧数据）判残留", "卡死秒口径" not in ids_b, f"got {ids_b}")
# 2026-09-29 改口径：无任何时间痕迹的 working 行**不再**计入。
# 旧口径（宁漏勿误杀）在显示层是个永久漏洞：这种行永远不会"过期"，
# 只要宿主把它留在 working，她就永远在写字（用户报的"一直卡在写字状态"）。
# "刚开的新对话"由 B1 保证不受影响（宿主建行时会写 last_activity_at）。
check("B4: 无任何时间痕迹的 working 行不计入（防永久活跃）",
      "无时间痕迹" not in ids_b, f"got {ids_b}")

# ---- C. query_db 端到端：桌宠/悬浮球自动恢复 ----
print("\n[C] query_db 端到端")
_kpi, active_c, ok_c, latest_c = CORE.query_db(ods_a, wb_a)
check("C1: query_db 的 active 已无残留",
      ok_c and [d["_wb_title"] for d in active_c] == ["新鲜会话"],
      f"got {[d['_wb_title'] for d in active_c]}")
_kpi, active_d, ok_d, _ = CORE.query_db(ods_b, wb_b)
check("C2: 仅剩「刚开的新对话」一条活跃",
      ok_d and sorted(d["_wb_title"] for d in active_d) == ["刚开的新对话"],
      f"got {sorted(d['_wb_title'] for d in active_d)}")

# ---- G. ODS 近窗并入：独立 CLI 会话（ZCode 场景）也触发"运行中" ----
# 2026-09-28 事故：ZCode 会话不在 workbuddy.db（那是宿主自己的会话表），运行踪迹
# 只有 ODS 的 LLM 调用事件。活跃判定必须并入 ODS 近窗（ODS_ACTIVE_SEC）会话。
print("\n[G] ODS 近窗并入（独立 CLI 会话）")
ods_g = _keep(make_ods([("sess_zcode_1", "t1", NOW_MS - 2_000)]))
ods_g_old = _keep(make_ods([("sess_zcode_2", "t1", NOW_MS - 360_000)]))   # 6 分钟前
wb_g_empty = _keep(make_wb([]))          # 宿主库没有这些会话（独立 CLI 的常态）
conn_g = sqlite3.connect(f"file:{ods_g}?mode=ro", uri=True)
conn_g.row_factory = sqlite3.Row
active_g = CORE._active_sessions(conn_g, wb_g_empty, CORE.ACTIVE_WINDOW_SEC)
conn_g.close()
check("G1: 仅存在于 ODS 的会话计入活跃",
      len(active_g) == 1 and active_g[0]["session_id"] == "sess_zcode_1",
      f"got {[(d.get('session_id'), d.get('title')) for d in active_g]}")
conn_g2 = sqlite3.connect(f"file:{ods_g_old}?mode=ro", uri=True)
conn_g2.row_factory = sqlite3.Row
active_g2 = CORE._active_sessions(conn_g2, wb_g_empty, CORE.ACTIVE_WINDOW_SEC)
conn_g2.close()
check("G2: 超出近窗（6 分钟）不并入", active_g2 == [], f"got {active_g2}")
# working 行与 ODS 近窗是同一会话 → 去重不双算
wb_g_dup = _keep(make_wb([("fresh1", "新鲜会话", "working", NOW_MS)]))
conn_g3 = sqlite3.connect(f"file:{ods_a}?mode=ro", uri=True)
conn_g3.row_factory = sqlite3.Row
active_g3 = CORE._active_sessions(conn_g3, wb_g_dup, CORE.ACTIVE_WINDOW_SEC)
conn_g3.close()
check("G3: working 与 ODS 同会话去重（恰好 1 条）",
      len(active_g3) == 1 and active_g3[0]["session_id"] == "fresh1",
      f"got {[(d.get('session_id'), d.get('_wb_title')) for d in active_g3]}")


# ---- D. stale_working_sessions：修复清单 ----
print("\n[D] stale_working_sessions 清单")
stale_d = CORE.stale_working_sessions(ods_a, wb_a)
check("D1: 只列卡死的那条", [v["id"] for v in stale_d] == ["stale1"], f"got {stale_d}")
check("D2: age_sec 合理（> 阈值）",
      stale_d and stale_d[0]["age_sec"] > STALE, f"got {stale_d}")
check("D3: 带标题", stale_d and stale_d[0]["title"] == "卡死会话", f"got {stale_d}")
check("D4: workbuddy.db 不可读 → []",
      CORE.stale_working_sessions(ods_a, "/nonexistent/no.db") == [])
wb_d5 = _keep(make_wb([("x", "旧会话", "completed", OLD_MS)]))
check("D5: 无 working 行 → []", CORE.stale_working_sessions(ods_a, wb_d5) == [])
fresh_only = _keep(make_wb([("fresh1", "新鲜会话", "working", NOW_MS)]))
check("D6: 新鲜会话不进清单", CORE.stale_working_sessions(ods_a, fresh_only) == [])

# ---- E. repair_stale_working：真修复 ----
print("\n[E] repair_stale_working 真修复")
n_e = CORE.repair_stale_working(ods_a, wb_a)
check("E1: 修复行数 = 1", n_e == 1, f"got {n_e}")
con = sqlite3.connect(wb_a)
rows_e = dict(con.execute("SELECT id, status FROM sessions").fetchall())
con.close()
check("E2: 卡死的落回 completed", rows_e.get("stale1") == "completed", f"got {rows_e}")
check("E3: 新鲜会话不动", rows_e.get("fresh1") == "working", f"got {rows_e}")
check("E4: 二次调用返回 0", CORE.repair_stale_working(ods_a, wb_a) == 0)

# 复查保护：清单生成后、写库前会话恢复终态（after_sec=0 让"全该修"，
# 但 status 已不是 working 的行不被触碰）
wb_e2 = _keep(make_wb([("s1", "已自愈", "completed", OLD_MS)]))
check("E5: 已非 working 的行不触碰",
      CORE.repair_stale_working(ods_a, wb_e2, after_sec=0) == 0)

# ---- F. 库锁死 → -1 不抛 ----
# BEGIN IMMEDIATE 拿 RESERVED 锁：读仍可（清单能生成），写被阻塞 → 复现"读得到修不动"
print("\n[F] 写库失败路径")
wb_f = _keep(make_wb([("f1", "卡死", "working", OLD_MS)]))
lock_con = sqlite3.connect(wb_f, timeout=1)
lock_con.execute("BEGIN IMMEDIATE")
try:
    n_f = CORE.repair_stale_working(ods_a, wb_f)
    check("F1: 库被锁死返回 -1（不抛异常）", n_f == -1, f"got {n_f}")
except Exception as e:
    check("F1: 库被锁死返回 -1（不抛异常）", False, f"raised {e}")
finally:
    lock_con.rollback()
    lock_con.close()

# ---- H. 写字态防卡死（2026-09-29 事故：桌宠一直卡在写字状态，怎么都不坐下）----
# 三个独立成因，各配一道闸：
#   ① 判据误判"永久活跃"：零 LLM 调用的 working 行靠 last_activity_at 一直新鲜；
#      或显示层用 900s 阈值 → 一轮对话后还多写 15 分钟。
#   ② 读数持续失败：为了不闪烁保留上次 active → 永远记得有任务。
#   ③ 拖拽状态卡住：`_dragging` 永久 True → tick 的数据刷新整条停摆。
print("\n[H] 写字态防卡死（判据 / 防抖 / 读数 / 拖拽）")
_now_ms = int(time.time() * 1000)

# H1~H3：活跃判据（幽灵会话 / 宽限期 / 显示层阈值收紧）
ods_h = _keep(make_ods([("withold", "t1", _now_ms - 6 * 60 * 1000)]))   # 最后一次调用 6 分钟前
wb_h = _keep(make_wb([
    ("ghost", "幽灵会话", "working", _now_ms - 600_000),      # 零调用 + 行 10 分钟没动
    ("justopen", "刚开的新对话", "working", _now_ms - 5_000),  # 零调用但在宽限内
    ("withold", "很久没调用的会话", "working", _now_ms),       # 有调用但已 6 分钟前
]))
conn_h = sqlite3.connect(f"file:{ods_h}?mode=ro", uri=True)
conn_h.row_factory = sqlite3.Row
active_h = CORE._active_sessions(conn_h, wb_h, CORE.ACTIVE_WINDOW_SEC)
conn_h.close()
titles_h = [d["_wb_title"] for d in active_h]
check("H1: 零调用 + 超宽限的 working 行不计入（幽灵会话）",
      "幽灵会话" not in titles_h, f"got {titles_h}")
check("H2: 零调用但在宽限内仍计入（新对话不丢）",
      "刚开的新对话" in titles_h, f"got {titles_h}")
check("H3: 最后一次调用老于 ACTIVE_STALE_SEC 不计入（不再多写 15 分钟）",
      "很久没调用的会话" not in titles_h,
      f"got {titles_h}（阈值 {CORE.ACTIVE_STALE_SEC}s / 旧的 {CORE.WB_STALE_AFTER_SEC}s 会放行）")
check("H4: 显示层阈值 = ODS 活跃窗（对用户的承诺：最多多写 5 分钟）",
      CORE.ACTIVE_STALE_SEC == CORE.ODS_ACTIVE_SEC == 300
      and CORE.NO_CALL_GRACE_SEC == 90,
      f"ACTIVE_STALE_SEC={CORE.ACTIVE_STALE_SEC} 宽限={CORE.NO_CALL_GRACE_SEC}")

# H5：session-meta 之类的采集元数据不算"活动"（否则残留永远修不掉）
# 造法：该会话在 ODS 里只有一条**刚刚**写入的元数据行，而宿主行已 20 分钟没动。
# 旧口径取 MAX(ts_ms)（不分用量）→ 元数据把年龄拉到 1 秒 → 永远修不掉。
ods_h5 = _keep(make_ods([("metaonly", "t1", _now_ms - 1_000)], usage=False))
wb_h5 = _keep(make_wb([("metaonly", "只有元数据的会话", "working", _now_ms - 1_200_000)]))
check("H5: 只有元数据（无用量）的会话仍判残留",
      [v["id"] for v in CORE.stale_working_sessions(ods_h5, wb_h5)] == ["metaonly"],
      f"got {CORE.stale_working_sessions(ods_h5, wb_h5)}")


class _PetStub:
    """只提供被测方法依赖的字段（不建窗口、不进消息循环）。"""

    def __init__(self):
        self._dragging = False
        self._pressed = False
        self._swallow_up = False
        self._drawn_sig = "x"
        self._read_ok_at = 0.0
        self._n_raw_prev = None
        self._n_stable = 0
        self._n_hold_since = 0.0
        self._fall_debounce_s = W.ACTIVE_FALL_DEBOUNCE_S
        self.events = []

    def _report_event(self, ev, ok=None, detail=""):
        self.events.append(ev)


# H6/H7：活跃数防抖（下降要确认，上升立即）
_st = _PetStub()
_san = W.WhalePet._stable_active_n
check("H6: 活跃数上升立即生效、下降要等确认窗口",
      _san(_st, 2, 1000.0) == 2          # 上升/首次 → 立即
      and _san(_st, 0, 1001.0) == 2      # 刚掉到 0：未确认
      and _san(_st, 0, 1002.5) == 2      # 仍在窗口内
      and _san(_st, 0, 1004.0) == 0      # 超过 ACTIVE_FALL_DEBOUNCE_S → 确认
      and _san(_st, 1, 1004.5) == 1,     # 又来了任务 → 立即
      f"stable={_st._n_stable} hold={_st._n_hold_since}")

# H7：拖拽卡死兜底（左键已抬起但状态还停在拖拽中）
_st2 = _PetStub()
_st2._dragging = True
_st2._pressed = True
_saved_key = W._user32.GetAsyncKeyState
try:
    W._user32.GetAsyncKeyState = lambda vk: 0            # 左键已抬起
    _cleared = W.WhalePet._clear_stuck_drag(_st2)
    _dragging_after_clear = _st2._dragging
    W._user32.GetAsyncKeyState = lambda vk: 0x8000       # 真的还按着
    _st2._dragging = True
    _kept = W.WhalePet._clear_stuck_drag(_st2)
    _dragging_when_held = _st2._dragging
finally:
    W._user32.GetAsyncKeyState = _saved_key
check("H7: 左键已抬起但状态卡在拖拽中 → 清掉并记事件（真按着时不误清）",
      _cleared and _dragging_after_clear is False
      and "drag_stuck_cleared" in _st2.events
      and _kept is False and _dragging_when_held is True,
      f"cleared={_cleared} kept={_kept} events={_st2.events}")

# H8：读数连续失败超上限 → 不再"记着"旧活跃
_st3 = _PetStub()
_rs = W.WhalePet._read_stale
check("H8: 读数连续失败超 READ_STALE_SEC → 判失效",
      _rs(_st3, True, 1000.0) is False
      and _rs(_st3, False, 1030.0) is False      # 30s：还在容忍内（防闪烁）
      and _rs(_st3, False, 1099.0) is True,      # 99s > 90s：放弃记着的活跃
      f"read_ok_at={_st3._read_ok_at}")

# ---- 清理 ----
for p in _tmp:
    try:
        os.unlink(p)
    except Exception:
        pass

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
