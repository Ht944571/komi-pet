#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb_hover_core.py — 悬浮球跨平台公共层（纯标准库，无任何 GUI 依赖）
==================================================================
macOS 版（b_hover.py，Cocoa）与 Windows 版（b_hover_win.py，Win32/GDI+）
共用本模块，保证两端的取数口径、格式化、卡片文案、位置记忆完全一致。

内容：
  1. 路径解析（wb_paths.json → 自动探测 → 家目录默认值）
  2. SQLite 取数：活跃会话（workbuddy.db status='working' 权威状态 + ODS 轮次明细）
  3. 展示格式化（积分/Token/时长/相对时间）
  4. 看板服务健康探测
  5. 球体位置记忆 / 状态自报文件 / 错误日志

改动本文件的 SQL 或字段口径会同时影响两个平台，请先读 references/架构说明.md「数据口径（勿改）」。
"""

import json
import os
import sqlite3
import tempfile
import time
import urllib.request

import wb_runtime as RT          # 运行时环境适配（可写数据目录 / 包内只读资源）

# ---------- 常量 ----------
POLL_SEC = 1              # 最短刷新间隔（秒）
ACTIVE_WINDOW_SEC = 3     # 降级时用：近 N 秒有活动 = 活跃对话
WB_CACHE_TTL = 3.0        # workbuddy.db 查询节流缓存（秒）
# 残留会话兜底（2026-09-28 事故）：宿主崩溃/被杀不会把 working 落回终态，
# 桌宠会永远显示"会话运行中"。working 且数据停更超过此时长 → 判为残留，
# 不计入活跃（真修复=写库，走 repair_stale_working）。
# 15 分钟：真在跑的会话每个 turn 都会有 ODS 事件，15 分钟一笔都没有
# 实际上只在"卡死"时发生；误判的代价也只是气泡提前回落，下一笔事件自愈。
WB_STALE_AFTER_SEC = 900
# 独立 CLI agent（ZCode 等）的会话**不在 workbuddy.db**（那是宿主自己的会话表），
# 它们的运行踪迹只有 ODS 数仓里的 LLM 调用。近窗有调用 = 会话在跑——
# 写字态/气泡"运行中"必须把它算进去，否则宿主静默时桌宠永远不写字。
# 5 分钟：agent 跑长本地工具时几分钟没有新调用属正常，太窄会写字/坐下反复抖；
# 对话结束后她最多多写 5 分钟才回落。
ODS_ACTIVE_SEC = 300
# 显示层"还在干活"的停更上限（2026-09-29 修正）：**与 ODS 活跃窗同宽**。
#   · 原来显示层直接用 WB_STALE_AFTER_SEC(900) → 一轮对话结束后她还多写 15 分钟，
#     用户看到的就是"一直卡在写字状态"；
#   · 300s = 文档里给用户的承诺「最多多写 5 分钟」。
#   · WB_STALE_AFTER_SEC(900) 继续只用于**写库真修复**（那里要保守，宁漏勿误杀）。
ACTIVE_STALE_SEC = 300
# 「刚开的会话」宽限：working 但**一笔 LLM 调用都还没产生**时，只在这段时间内计入。
# 2026-09-29 事故：有个会话整天只有 4 条 session-meta（零 LLM 调用），却在宿主库里
# 一直 status='working'，而宿主偶尔刷 last_activity_at → 兜底分支永远判它"新鲜"
# → n 永久 ≥1 → 桌宠一直写字。过宽限仍无调用 = 光开着没干活，不计入。
NO_CALL_GRACE_SEC = 90
LIVE_LOOKBACK_SEC = 30 * 60   # "本轮"统计回溯窗口：取最近 30 分钟内有数据的 turn（不依赖 working 状态）
TMPDIR = tempfile.gettempdir()
# P1：文件名带项目前缀（komi-）。旧技能目录的遗留进程还在写同名旧文件
# （交接文档 §9 P1），前缀隔离后互不覆盖；重新登录旧进程消失后即成历史。
STATE_FILE = os.path.join(TMPDIR, "komi-wb-hover-state.json")
ERR_LOG = os.path.join(TMPDIR, "komi-wb-hover.err.log")

EVENT_LOG_MAX_BYTES = 1_500_000   # 事件日志轮转阈值（~1.5MB ≈ 2.5 万条，P1 缺轮转）

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------- 路径解析 ----------
def load_paths():
    """返回 dict：db_path / workbuddy_db / pos_file / dashboard_url / skill_dir。

    优先级：wb_paths.json 显式配置（install.py 写入本机真实路径）
           > 按标准目录结构自动探测
           > 家目录默认值。
    """
    cfg = {}
    # 打包后 wb_paths.json 落在**数据目录**；源码运行两者是同一个 app/（行为不变）
    for cfg_path in (RT.paths_config_path(), os.path.join(SCRIPT_DIR, "wb_paths.json")):
        if cfg_path and os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                break
            except Exception:
                cfg = {}
    home = os.path.expanduser("~")

    db = cfg.get("db_path")
    if not db or not os.path.exists(db):
        db = RT.db_path()          # 源码 = app/wb_usage/data/…；打包 = 数据目录

    wb = cfg.get("workbuddy_db")
    if not wb or not os.path.exists(wb):
        wb = os.path.join(home, ".workbuddy", "workbuddy.db")

    pos = cfg.get("pos_file") or os.path.join(RT.data_dir(), ".hover_pos.json")
    dash = (cfg.get("dashboard_url")
            or os.environ.get("WB_DASHBOARD_URL")
            or "http://127.0.0.1:8801")
    return {
        "db_path": db,
        "workbuddy_db": wb,
        "pos_file": pos,
        "dashboard_url": dash,
        "skill_dir": cfg.get("skill_dir") or os.path.dirname(SCRIPT_DIR),
    }


# ---------- 格式化 ----------
def fmt_tokens(n):
    if not n:
        return "0"
    if n >= 1e8:
        return f"{n/1e8:.2f}亿"
    if n >= 1e4:
        return f"{n/1e4:.1f}万"
    return f"{int(n)}"


def fmt_dur(sec):
    sec = max(int(sec or 0), 0)
    if sec < 60:
        return f"{sec}秒"
    if sec < 3600:
        return f"{sec//60}分{sec%60:02d}秒"
    return f"{sec//3600}时{sec%3600//60:02d}分"


def _to_epoch(v):
    """把 last_activity_at 之类字段统一成 epoch 秒（支持毫秒时间戳 / 'YYYY-MM-DD HH:MM:SS'）。"""
    if isinstance(v, (int, float)):
        return v / 1000.0 if v > 1e12 else float(v)
    try:
        return time.mktime(time.strptime(str(v), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def fmt_ago(ts):
    """最后活动时间的相对描述（实时系统时间基准）。"""
    t = _to_epoch(ts)
    if not t:
        return "-"
    diff = time.time() - t
    if diff < 5:
        return "刚刚"
    if diff < 60:
        return f"{int(diff)}秒前"
    if diff < 3600:
        return f"{int(diff//60)}分钟前"
    return time.strftime("%H:%M", time.localtime(t))


def fmt_duration(it):
    """轮次耗时：优先用毫秒时间戳 first_ts/last_ts，回退 first_time/last_time 字符串。"""
    try:
        f = it.get("first_ts")
        t = it.get("last_ts")
        if f and t:
            d = (_to_epoch(t) - _to_epoch(f))
            if 0 <= d < 86400:
                return fmt_dur(d)
            return "-"
        return fmt_dur(_to_epoch(it.get("last_time")) - _to_epoch(it.get("first_time")))
    except Exception:
        return "-"


def fmt_duration_live(it, now=None):
    """实时用时：turn 持续中时用 first_ts → now（每 tick 重绘自动 +1s），已完成时用 first_ts → last_ts。

    设计意图：workbuddy.db 标记 working 时，"本轮"用 first_ts → now，桌宠气泡每秒自动 +1s 实时跳动；
    已结束/无新调用时回退到 first_ts → last_ts（最终耗时）。这样用户能直观看到对话正在思考/已结束。
    """
    if not it:
        return "-"
    f = it.get("first_ts")
    if not f:
        return "-"
    if now is None:
        now = time.time() * 1000
    d = (_to_epoch(now) - _to_epoch(f))
    if d < 0:
        return "0秒"
    if d < 86400:
        return fmt_dur(d)
    return "-"


# ---------- 卡片文案（两端共用，保证显示一致） ----------
def card_text(it, title_max=24, req_max=36):
    """把一条活跃会话记录渲染成面板卡片的三个字符串。

    注意：这里刻意不使用 emoji（⚡⏱🕐）。Windows 端 GDI+ 不做字体回退，
    微软雅黑缺字形会显示豆腐块；为保证两端渲染一致，统一用中文标签。
    """
    title = (it.get("_wb_title") or it.get("title") or it.get("project")
             or "(无标题)")[:title_max]
    act_ts = it.get("_last_act") or it.get("last_ts") or 0
    info = (f"本轮 {(it.get('credit') or 0):.1f} 分 · {fmt_tokens(it.get('total_tokens'))} tok"
            f" · 用时 {fmt_duration(it)} · {fmt_ago(act_ts)}")
    req = (it.get("user_prompt") or it.get("title") or "-").replace("\n", " ")
    req = "「" + req[:req_max] + ("…" if len(req) > req_max else "」")
    return {"title": title, "info": info, "req": req}


# ---------- 看板服务健康 ----------
def api_healthy(dashboard_url):
    try:
        with urllib.request.urlopen(dashboard_url + "/api/health", timeout=0.5) as r:
            return bool(json.loads(r.read().decode()).get("ok"))
    except Exception:
        return False


# ---------- 取数 ----------
_wb_cache = {"ts": 0.0, "sids": [], "key": None}
# 最近一次活跃判定的快照（只读排查用：tools/_diag_active.py 打印它 + 逐会话原因）
_active_debug = {}


def _active_sessions(conn, wb_db, window_sec):
    """活跃会话 = workbuddy.db status='working' ∪ ODS 近窗（ODS_ACTIVE_SEC）有调用的会话。

    例外（2026-09-28 事故兜底）：working 但数据停更超过 WB_STALE_AFTER_SEC 的残留
    （宿主崩溃/被杀不落终态）不计入活跃，否则桌宠永远显示"会话运行中"。
    真修复（写库回终态）走 repair_stale_working。
    并入 ODS 近窗（2026-09-28 事故）：ZCode 等独立 CLI 的会话不在宿主库，
    只有 ODS 收得到它们的调用——不并入的话宿主静默时桌宠永远不写字。

    window_sec 仅在 workbuddy.db 不可读时用于 ODS 时间窗口兜底。
    每个会话的最新轮次用 ODS 补充（积分/tokens/请求）。
    """
    sids, wb_ok = [], False
    now = time.time()
    if _wb_cache["key"] == wb_db and now - _wb_cache["ts"] < WB_CACHE_TTL:
        sids, wb_ok = list(_wb_cache["sids"]), True
    else:
        try:
            wb = sqlite3.connect(f"file:{wb_db}?mode=ro", uri=True, timeout=3)
            wb.row_factory = sqlite3.Row
            for r in wb.execute(
                """SELECT id, title, last_activity_at FROM sessions
                   WHERE status='working' ORDER BY last_activity_at DESC""").fetchall():
                sids.append((r["id"], r["title"] or "", r["last_activity_at"] or 0))
            wb.close()
            wb_ok = True
            _wb_cache.update({"ts": now, "sids": list(sids), "key": wb_db})
        except Exception:
            wb_ok = False

    # 恒定并入 ODS 近窗会话（独立 CLI agent 的唯一运行踪迹；与 working 行按
    # session_id 去重）。原"wb 不可读才走 60s 兜底"由这条更宽的并集取代。
    try:
        seen = {s[0] for s in sids}
        now_ms = int(now * 1000)
        for (sid,) in conn.execute(
                """SELECT DISTINCT session_id FROM ods_jsonl_event
                   WHERE raw_usage_json IS NOT NULL AND ts_ms >= ?""",
                (now_ms - ODS_ACTIVE_SEC * 1000,)).fetchall():
            if sid and sid not in seen:
                sids.append((sid, "", 0))
                seen.add(sid)
    except Exception:
        pass

    active = []
    for sid, wb_title, last_act in sids:
        t = conn.execute(
            """SELECT o.session_id AS session_id,
                      o.request_id AS turn_id,
                      MIN(o.ts_ms) AS first_ts, MAX(o.ts_ms) AS last_ts,
                      COUNT(*) AS api_calls,
                      ROUND(SUM(COALESCE(json_extract(o.raw_usage_json,'$.credit'),0)),2) AS credit,
                      SUM(COALESCE(json_extract(o.raw_usage_json,'$.total_tokens'),0)) AS total_tokens,
                      MAX(o.project) AS project,
                      (SELECT ai_title FROM ods_jsonl_event
                        WHERE session_id = o.session_id AND ai_title IS NOT NULL
                        ORDER BY ts_ms DESC LIMIT 1) AS title
               FROM ods_jsonl_event o
               WHERE o.session_id = ? AND o.raw_usage_json IS NOT NULL
               GROUP BY o.request_id ORDER BY last_ts DESC LIMIT 1""",
            (sid,)).fetchone()
        if t:
            d = dict(t)
            # user_prompt：轮次首个调用之前最近的用户消息（Python 补充，避开 SQL 聚合嵌套）
            up = conn.execute(
                """SELECT user_prompt FROM ods_jsonl_event
                   WHERE session_id=? AND event_type='message' AND role='user'
                     AND user_prompt IS NOT NULL AND ts_ms <= ?
                   ORDER BY ts_ms DESC LIMIT 1""", (sid, d["first_ts"])).fetchone()
            d["user_prompt"] = up[0] if up else ""
        else:
            # working 但 ODS 尚无调用事件（新对话刚开、还没产生 LLM 调用）——
            # ★ 只在宽限期内计入：过时仍无调用 = "光开着没干活"，不计入。
            #   （否则零调用的 working 行 + 宿主偶尔刷 last_activity_at = 永久活跃，
            #     桌宠会一直卡在写字态，见 NO_CALL_GRACE_SEC 的注释）
            _age_act = (time.time() - _to_epoch(last_act)) if last_act else None
            if _age_act is None or _age_act > NO_CALL_GRACE_SEC:
                continue
            d = {"turn_id": "", "first_ts": last_act or 0, "last_ts": last_act or 0,
                 "api_calls": 0, "credit": 0.0, "total_tokens": 0, "project": "",
                 "title": wb_title, "user_prompt": ""}
        d["_wb_title"] = wb_title
        d["_last_act"] = last_act
        d["_sid"] = sid
        # 残留兜底（显示层口径）：最后一条**带用量的调用**老于 ACTIVE_STALE_SEC
        # → 不算"在干活"，不计入活跃（她最多多写 5 分钟）。
        # last_ts 是 ms，_to_epoch 统一口径。
        _ref_ms = d.get("last_ts") or 0
        if not _ref_ms:
            continue                  # 无任何可用的活动参考 → 宁漏（防"永久算活跃"）
        if time.time() - _to_epoch(_ref_ms) > ACTIVE_STALE_SEC:
            continue
        active.append(d)
    active.sort(key=lambda x: x["last_ts"], reverse=True)
    # 记下每个会话被判活跃/排除的原因（排查用；`_active_debug` 只读快照）
    _active_debug.clear()
    _active_debug.update({"ts": now, "kept": [d.get("_sid") for d in active]})
    return active


def stale_working_sessions(db_path, wb_db, after_sec=None):
    """列出卡死的残留会话：status='working' 但数据停更超过 after_sec 秒。

    停更判据：ODS 里该会话最新事件时间；ODS 没有则用 last_activity_at
    （ms/s 自动适配）。两者都没有 → 不判（宁漏勿误杀，比如刚开的新对话）。
    返回 [{"id", "title", "age_sec"}, ...]；库不可读 → []。
    故意不走 _wb_cache：修复动作要的是当下真相，不是 3 秒前的快照。
    """
    if after_sec is None:
        after_sec = WB_STALE_AFTER_SEC
    try:
        wb = sqlite3.connect(f"file:{wb_db}?mode=ro", uri=True, timeout=3)
        rows = wb.execute(
            "SELECT id, title, last_activity_at FROM sessions "
            "WHERE status='working'").fetchall()
        wb.close()
    except Exception:
        return []
    if not rows:
        return []
    last_ev = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        qs = ",".join("?" * len(rows))
        # ★ 只认**带用量**的事件（= 真发生过 LLM 调用）。session-meta 之类的
        #   采集元数据不是"活动"——否则一个零调用的会话靠 session-meta 就能
        #   永远显得"新鲜"，残留永远修不掉（2026-09-29 踩过）。
        for sid, mx in conn.execute(
                f"""SELECT session_id, MAX(ts_ms) FROM ods_jsonl_event
                    WHERE session_id IN ({qs}) AND raw_usage_json IS NOT NULL
                    GROUP BY session_id""",
                [r[0] for r in rows]):
            last_ev[sid] = mx or 0
        conn.close()
    except Exception:
        pass                          # 数仓不可读 → 只按 last_activity_at 判
    now_ms = time.time() * 1000.0
    out = []
    for sid, title, last_act in rows:
        ref_ms = last_ev.get(sid) or (_to_epoch(last_act) * 1000.0)
        if not ref_ms:
            continue
        age = (now_ms - ref_ms) / 1000.0
        if age > after_sec:
            out.append({"id": sid, "title": title or "", "age_sec": int(age)})
    return out


def repair_stale_working(db_path, wb_db, after_sec=None):
    """把残留会话落回终态（status='completed'）——"修复卡死会话"的写库入口。

    只动 stale_working_sessions 认定的行，且 UPDATE 带 status='working' 复查，
    两次查询之间恢复活跃的会话不会被误杀。返回修复行数；库不可写 → -1。
    写库时机：桌宠启动自动一次 + 右键菜单手动；宿主若真在跑同一会话，
    下一笔事件会把状态写回 working（桌宠侧无永久影响）。
    """
    victims = stale_working_sessions(db_path, wb_db, after_sec)
    if not victims:
        return 0
    try:
        wb = sqlite3.connect(wb_db, timeout=3)
        try:
            qs = ",".join("?" * len(victims))
            cur = wb.execute(
                f"""UPDATE sessions SET status='completed'
                    WHERE status='working' AND id IN ({qs})""",
                [v["id"] for v in victims])
            wb.commit()
            return cur.rowcount
        finally:
            wb.close()
    except Exception:
        return -1


def query_db(db_path, wb_db, window_sec=ACTIVE_WINDOW_SEC):
    """直查 ODS（只读）。返回 (None, active_list, ok, latest_turn)。

    ok=False 表示数仓/workbuddy.db 当前不可读——调用方应保留上次值，避免闪烁归零。

    latest_turn: 最近 LIVE_LOOKBACK_SEC 内有真实数据（积分>0 或 tokens>0）的最新 turn。
    用于桌宠气泡"本轮积分/Token/用时"的实时统计——不依赖 workbuddy.db 的 working 状态，
    保证 LLM 调用一到达 ODS，气泡就开始增长；working 状态和数据到达是两个独立时刻，
    用 working-only 数据源会在 waiting 期间一直显示 0（典型问题）。
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
    except Exception:
        return None, None, False, None
    conn.row_factory = sqlite3.Row
    try:
        active = _active_sessions(conn, wb_db, window_sec)
        latest = _latest_turn_with_data(conn, LIVE_LOOKBACK_SEC)
        return None, active, True, latest
    except Exception:
        return None, None, False, None
    finally:
        conn.close()


def _latest_turn_with_data(conn, lookback_sec):
    """查 ODS 最近 lookback_sec 秒内有数据（积分>0 或 tokens>0）的最新 turn。

    不依赖 workbuddy.db 的 working 状态——只要 ODS 收到过 LLM 调用事件就算"有数据"。
    同一 turn 的所有调用事件按 request_id 聚合（与 _active_sessions 一致口径）。
    选 last_ts 最大的那一条（最新），确保气泡数字随对话推进自动滚动。
    """
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - lookback_sec * 1000
    try:
        row = conn.execute(
            """SELECT o.request_id AS turn_id,
                      MIN(o.ts_ms) AS first_ts, MAX(o.ts_ms) AS last_ts,
                      COUNT(*) AS api_calls,
                      ROUND(SUM(COALESCE(json_extract(o.raw_usage_json,'$.credit'),0)),2) AS credit,
                      SUM(COALESCE(json_extract(o.raw_usage_json,'$.total_tokens'),0)) AS total_tokens,
                      MAX(o.project) AS project,
                      (SELECT ai_title FROM ods_jsonl_event
                        WHERE session_id = o.session_id AND ai_title IS NOT NULL
                        ORDER BY ts_ms DESC LIMIT 1) AS title,
                      o.session_id AS session_id
               FROM ods_jsonl_event o
               WHERE o.raw_usage_json IS NOT NULL
                 AND (json_extract(o.raw_usage_json,'$.credit') > 0
                      OR json_extract(o.raw_usage_json,'$.total_tokens') > 0)
                 AND o.ts_ms >= ?
               GROUP BY o.request_id, o.session_id
               ORDER BY MAX(o.ts_ms) DESC LIMIT 1""",
            (since_ms,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    d = dict(row)
    # first_ts 可能早于 since_ms（回溯窗前的旧 turn 还在跑），截断到 since_ms 防止用时爆长
    if d.get("first_ts") and d["first_ts"] < since_ms:
        d["first_ts"] = since_ms
    # user_prompt：turn 首调用之前最近的用户消息
    up = conn.execute(
        """SELECT user_prompt FROM ods_jsonl_event
           WHERE session_id=? AND event_type='message' AND role='user'
             AND user_prompt IS NOT NULL AND ts_ms <= ?
           ORDER BY ts_ms DESC LIMIT 1""", (d["session_id"], d["first_ts"])).fetchone()
    d["user_prompt"] = up[0] if up else ""
    d["_live"] = True  # 标记是 live 模式（用时算到 now，不是 last_ts）
    return d


# ---------- 位置记忆 / 自报 / 日志 ----------
def load_pos(pos_file):
    try:
        with open(pos_file) as f:
            saved = json.load(f)
        if "x" in saved and "y" in saved:
            return float(saved["x"]), float(saved["y"])
    except Exception:
        pass
    return None


def save_pos(pos_file, x, y):
    try:
        with open(pos_file, "w") as f:
            json.dump({"x": x, "y": y}, f)
    except Exception:
        pass


def turn_src(conn, db_path=None):
    """轮次数据源：优先**物化表** `dws_turn`，否则退回视图 `v_turn_total`。

    🔴 为什么必须优先物化表：`v_turn_total` 是多层 VIEW + 每行 `json_extract`
    （基础表 1GB+，视图**无法建索引**），实测**单次 1.6~10.3 秒**；`dws_turn` 是它的
    快照，**同样的查询 12.7ms**（2026-09-30 实测：切换 agent 的那次接续查询 = 10304ms）。
    退回条件：表不存在、或表还是空的（尚未物化）→ 用原视图，避免读到"假空"。

    ⚠️ 任何"顺带查一下轮次"的地方都必须用它 —— 直查视图 = 在 UI 线程上按秒冻。
    """
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='dws_turn'").fetchone() \
           and conn.execute("SELECT 1 FROM dws_turn LIMIT 1").fetchone():
            return "dws_turn"
    except Exception:
        pass
    return "v_turn_total"


def today_timeline(db_path, limit=8):
    """跨 agent 统一时间线（跟随模式 P2 连续锚点）——零新链路，只读轮次表。

    返回 (summary, recent)：
      summary = [(agent_label, 轮次数), ...]   今天各 agent 轮次数（按轮次降序，≤3）
      recent  = [(HH:MM, agent, 标题), ...]    今天最近 limit 轮（按时间降序）
    数据不可读 / 无数据 → ([], [])（调用方回退到旧副标）。
    """
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        try:
            day = conn.execute("SELECT date('now','localtime')").fetchone()[0]
            src = turn_src(conn, db_path)      # ★ 优先物化表，见 turn_src 注释
            summary = conn.execute(
                f"""SELECT agent, COUNT(*) FROM {src}
                   WHERE day = ? AND agent != '' GROUP BY agent
                   ORDER BY 2 DESC LIMIT 3""", (day,)).fetchall()
            recent = conn.execute(
                f"""SELECT agent, first_time,
                          COALESCE(NULLIF(title, ''), NULLIF(project, ''), '')
                     FROM {src} WHERE day = ? AND agent != ''
                     ORDER BY first_time DESC LIMIT ?""", (day, limit)).fetchall()
        finally:
            conn.close()
        summary = [(str(a), int(n)) for a, n in summary if a]
        recent = [(str(t)[11:16] if len(str(t)) >= 16 else str(t), str(a), str(ti))
                  for a, t, ti in recent]
        return summary, recent
    except Exception:
        return [], []


def report_state(pid, visible, n_windows, pos, active_n, db_ok, api_ok):
    """调试自报：写状态文件，供安装脚本/人工排查确认悬浮球真的活着。"""
    try:
        json.dump({
            "pid": pid,
            "platform": "windows" if os.name == "nt" else (
                "macos" if "darwin" in __import__("sys").platform else "other"),
            "viewable": bool(visible),
            "nswindows": n_windows,
            "pos": [pos[0], pos[1]],
            "active_n": active_n,
            "db_ok": db_ok,
            "api_ok": api_ok,
            "ts": time.strftime("%H:%M:%S"),
        }, open(STATE_FILE, "w"))
    except Exception:
        pass


def rotate_if_large(path, max_bytes=EVENT_LOG_MAX_BYTES, keep=1):
    """日志按大小轮转：path → path.N（保留 N 代）。写前调用，异常静默。

    P1：事件日志此前无轮转（曾堆到 4.6 万行）。追加型日志只在写前检查一次
    体积即可——超限就把当前文件改名腾位，下一行自然落到新文件。
    """
    try:
        if os.path.getsize(path) > max_bytes:
            for i in range(keep, 0, -1):
                src = f"{path}.{i - 1}" if i > 1 else path
                dst = f"{path}.{i}"
                if os.path.exists(src):
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.replace(src, dst)
    except OSError:
        pass


def log_exception(prefix=""):
    try:
        import traceback
        with open(ERR_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + prefix + "\n")
            f.write(traceback.format_exc())
    except Exception:
        pass
