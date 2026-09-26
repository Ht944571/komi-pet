#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb_api.py — 多 agent 用量看板·接口服务（前后端分离）

前端（dashboard.html 纯静态）通过 JSON 接口取数，接口服务读 SQLite（VIEW 层）。
打开页面时自动增量采集+建模（/api/rebuild 手动触发，GET 首页触发自动刷新）。

多 agent：以下接口都接受 `agent=<key>`（默认 `all`）。取值来自 agents 注册表
（workbuddy / codex / codebuddy），未知取值返回空集而非报错。

接口：
  GET  /                  → dashboard.html（静态 UI）
  GET  /chart.umd.min.js  → Chart.js 本地副本
  GET  /api/agents        → 各 agent 合计 + 元信息（label / has_credit）
  GET  /api/kpi?days=&agent=      → KPI 指标
  GET  /api/daily?days=90&agent=  → 每日趋势（day/credit/total_tokens/cache_hit_rate/api_calls/turns）
  GET  /api/models?days=30&agent= → 模型分布（含 credit_pct 与 token_pct）
  GET  /api/projects?agent=       → 项目分布
  GET  /api/clients?agent=        → 客户端分布（WorkBuddy/CodeBuddyIDE/VSCode/…）
  GET  /api/tops?agent=           → TOP 30 会话
  GET  /api/turns?session_id=xx → 某会话全部轮次（含 user_prompt 原文，前端脱敏）
  GET  /api/calls?turn_id=xx    → 某轮次单次调用明细
  POST /api/rebuild       → 重新采集 + 建模
  GET  /api/health        → 最后采集时间 / 条数

用法：python3 wb_api.py [--port 8801] [--db 路径]
"""
import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# 日志行缓冲：launchd 重定向 stdout 到文件时 Python 全缓冲，SIGTERM 退出不 flush → 日志缺失。
# 改为行缓冲，启动/自检日志实时落盘，便于排查。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

import wb_collect
import wb_common
import wb_dw
import agents as agent_registry

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE, "data", "wb_usage_dw.db")
DEFAULT_HTML = os.path.join(BASE, "dashboard.html")


# 对数用开关：强制指定轮次数据源（None=自动：有 dws_turn 就用它）
_FORCE_SRC = None


def get_conn(db_path):
    # 普通连接（非 mode=ro）：同进程已有 WAL 写连接（采集线程），
    # 只读 URI 模式 + WAL + 同进程写连接会 "unable to open database file"（实测）
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA query_only=ON")   # 只读语义，防止误写
    conn.row_factory = sqlite3.Row
    return conn
    # 注意：sqlite3.Connection 的上下文管理器只提交/回滚事务、不关闭连接。
    # 所有调用方必须用 `with closing(get_conn(...)) as c:` 确保成功/异常路径都释放 FD，
    # 否则异常时 traceback 持有栈帧引用，连接要等 GC 才关闭 → FD 持续增长直至
    # "Too many open files" / "unable to open database file"（2026-08-31 P0 修复）。


class Api:
    """只读查询封装（全部来自 VIEW 层 + CodeBuddy 侧表）。"""

    CACHE_TTL = 30.0  # 聚合接口缓存秒数：看板切换范围多数命中缓存；30s 内数据一致可接受

    def __init__(self, db_path):
        self.db_path = db_path
        self._cache = {}

    def _cached(self, key, fn):
        """TTL 缓存：聚合结果 5s 内复用（数据新鲜度不受影响，采集延迟 <5s）。"""
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self.CACHE_TTL:
            return hit[1]
        val = fn()
        self._cache[key] = (now, val)
        return val

    def _day_cond(self, days, col="day"):
        """days: 'today'|'7'|'30'|'all' → 日期条件片段（不含 WHERE，'' 表示不过滤）。
        col 用于多表 JOIN 场景消歧。"""
        if days == "today":
            return f"{col} = date('now','localtime')"
        if days == "all":
            return ""
        n = max(int(days), 1)
        return f"{col} >= date('now','localtime','-{n-1} days')"

    def _days_where(self, days):
        """days: 'today'|'7'|'30'|'all' → SQL WHERE 子句（day 为本地日期）。"""
        cond = self._day_cond(days)
        return f"WHERE {cond}" if cond else ""

    def _agent_cond(self, agent, col="agent"):
        """agent 过滤条件片段（'all'/None/'' = 不过滤）。agent 来自数据源 key，
        取值集合由 agents 注册表决定，不接受任意输入。

        col 用于多表 JOIN 场景消歧（如 v_call c JOIN v_turn t 需写 t.agent）。
        """
        if not agent or agent == "all":
            return ""
        if agent not in agent_registry.source_keys() and agent != "codebuddy":
            # 未知 agent：返回恒假条件（空集）而不是抛错——前端传了脏参数时
            # 表现为「没有数据」，不会把整个接口打成 500。
            return "1 = 0"
        esc = agent.replace("'", "''")
        return f"{col} = '{esc}'"

    def _conds(self, *parts):
        """拼接多个条件片段 → (WHERE 子句, 不含 WHERE 的条件) 。"""
        cs = [p for p in parts if p]
        return (f"WHERE {' AND '.join(cs)}" if cs else ""), " AND ".join(cs)

    def _src(self, conn=None):
        """轮次数据源：优先物化表 dws_turn，其次视图 v_turn_total（见 wb_dw.turn_src）。"""
        if _FORCE_SRC:
            return _FORCE_SRC
        return wb_dw.turn_src(conn, self.db_path)

    def kpi(self, days="30", agent="all"):
        return self._cached(f"kpi:{days}:{agent}", lambda: self._kpi(days, agent))

    def _kpi(self, days="30", agent="all"):
        """KPI 指标：v_turn_total 单条条件聚合（语义与原 4 条查询等价）。

        2026-08-31 性能优化：原实现 4 条 SELECT 各自重新物化 v_turn_total
        （复杂 CTE + UNION ALL 视图），冷查询 ~2.2s；改为单条 SELECT +
        FILTER 条件聚合，SQLite 对单语句的 CO-ROUTINE 只物化一次，冷查询
        ~60ms（实测 4 范围 15 指标与旧查询在快照下完全等价）。
        轮次/会话数 = jsonl 侧；调用/积分 = 合并（含 CodeBuddy）；tokens = jsonl。

        多 agent：agent 条件进入主 WHERE，所有 FILTER 都是该子集的子集，口径自洽。
        """
        where, rest = self._conds(self._day_cond(days), self._agent_cond(agent))
        jw = ("source='jsonl' AND " + rest) if rest else "source='jsonl'"
        cw = ("source='official' AND " + rest) if rest else "source='official'"
        today = "day = date('now','localtime')"
        with closing(get_conn(self.db_path)) as c:
            src = self._src(c)
            row = c.execute(f"""SELECT
                COUNT(*) FILTER (WHERE {jw}),
                COUNT(DISTINCT session_id) FILTER (WHERE {jw}),
                COALESCE(SUM(api_calls),0), COALESCE(SUM(credit),0),
                COALESCE(SUM(total_tokens),0), COALESCE(SUM(cached_tokens),0),
                COALESCE(SUM(miss_tokens),0), COALESCE(SUM(completion_tokens),0),
                COALESCE(SUM(api_calls) FILTER (WHERE {today}),0),
                COALESCE(SUM(credit) FILTER (WHERE {today}),0),
                COALESCE(SUM(total_tokens) FILTER (WHERE {today}),0),
                COALESCE(SUM(api_calls) FILTER (WHERE {cw}),0),
                COALESCE(SUM(credit) FILTER (WHERE {cw}),0),
                COALESCE(SUM(api_calls) FILTER (WHERE source='official' AND {today}),0),
                COALESCE(SUM(credit) FILTER (WHERE source='official' AND {today}),0)
              FROM {src} {where}""").fetchone()
        (turns, n_sess, calls, credit, tokens, cached, miss, comp,
         t_calls, t_credit, t_tokens, cb_calls, cb_credit,
         cb_t_calls, cb_t_credit) = row
        hit_rate = (cached / (cached + miss)) if (cached + miss) > 0 else None
        # 合计口径：credit/api_calls 已含 CodeBuddy（tokens 仅 jsonl）
        return {"ok": True, "data": {
            "total_turns": turns, "total_calls": calls,
            "total_credit": round(credit, 2), "total_tokens": tokens,
            "total_cached_tokens": cached, "total_miss_tokens": miss,
            "total_completion_tokens": comp,
            "total_sessions": n_sess,
            "cache_hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
            "today_calls": t_calls, "today_credit": round(t_credit, 2),
            "today_tokens": t_tokens,
            "cb_requests": cb_calls, "cb_credit": round(cb_credit, 2),
            "cb_today_requests": cb_t_calls, "cb_today_credit": round(cb_t_credit, 2),
        }}

    def daily(self, days="all", agent="all"):
        """每日趋势（聚合缓存）：credit 已并入 CodeBuddy（合计口径）。"""
        return self._cached(f"daily:{days}:{agent}", lambda: self._daily(days, agent))

    def _daily(self, days="all", agent="all"):
        """每日趋势：v_turn_total 按天聚合（jsonl + CodeBuddy 自动对齐天数）。"""
        where, _ = self._conds(self._day_cond(days), self._agent_cond(agent))
        sql = ("""SELECT day, COUNT(*) AS turns, SUM(api_calls) AS api_calls,
                          SUM(credit) AS credit, SUM(total_tokens) AS total_tokens,
                          SUM(cached_tokens) AS cached_tokens,
                          SUM(miss_tokens) AS miss_tokens,
                          SUM(completion_tokens) AS completion_tokens,
                          SUM(thinking_tokens) AS thinking_tokens,
                          CASE WHEN SUM(cached_tokens)+SUM(miss_tokens) > 0
                               THEN 1.0*SUM(cached_tokens)/(SUM(cached_tokens)+SUM(miss_tokens))
                               ELSE NULL END AS cache_hit_rate
                   FROM {src} {where} GROUP BY day ORDER BY day""")
        cb_where, _ = self._conds("source='official'", self._agent_cond(agent))
        with closing(get_conn(self.db_path)) as c:
            if days in ("today", "all", ""):
                rows = c.execute(sql.format(where=where, src=self._src())).fetchall()
            else:
                n = max(int(days), 1)
                rows = c.execute(sql.format(where=where, src=self._src()) + " DESC LIMIT ?", (n,)).fetchall()
                rows = list(reversed(rows))
            # CodeBuddy 按天（前端可查 credit_cb）
            src = self._src(c)
            cbmap = {r["day"]: (r["requests_cb"], r["credit_cb"]) for r in c.execute(
                f"SELECT day, SUM(api_calls) AS requests_cb, SUM(credit) AS credit_cb"
                f" FROM {src} {cb_where} GROUP BY day"
            )}
        data = []
        for r in rows:
            d = dict(r)
            cb_r, cb_c = cbmap.get(d["day"], (0, 0.0))
            d["requests_cb"] = cb_r
            d["credit_cb"] = round(cb_c, 2)
            d["credit"] = round(d["credit"], 2)   # 已含 CodeBuddy（合计）
            data.append(d)
        return {"ok": True, "data": data}

    def models(self, days="30", agent="all"):
        """模型分布（按时间范围过滤）。days: 'today'|'7'|'30'|'all'。
        各模型积分占比以『该时间范围内的合计』为分母，保证与顶部范围统计口径一致。

        多 agent：agent 条件作用于 v_turn（消歧写 t.agent）；无积分概念的 agent
        （如 Codex）credit 恒为 0，占比为 NULL，前端改看 token_pct。
        """
        return self._cached(f"models:{days}:{agent}", lambda: self._models(days, agent))

    def _models(self, days="30", agent="all"):
        # 按轮次的本地日期过滤（v_turn.day，与 /api/kpi /api/daily 同一“日期归属”口径），
        # 再对单次调用按 model 聚合；v_call 无 day 列，故 JOIN v_turn(turn_id 一对一)。
        #
        # 性能：分母（合计）用 WITH 先物化一次，两个占比各引用它 —— 若把子查询
        # 内联写两遍（原实现内联两遍、加 token_pct 后变三遍），SQLite 会重复物化
        # 整个 v_call（26 万行 × json_extract），实测 3.9s → 10.3s。CTE 版一次。
        where, _ = self._conds(self._day_cond(days, col="t.day"),
                               self._agent_cond(agent, col="t.agent"))
        sub = ("SELECT c.model AS model, COUNT(*) AS api_calls, SUM(c.credit) AS credit,"
               " SUM(c.total_tokens) AS total_tokens"
               " FROM v_call c JOIN v_turn t ON t.turn_id = c.turn_id"
               + (" " + where if where else "") + " GROUP BY c.model")
        sql = ("WITH agg AS (" + sub + ")"
               " SELECT model, api_calls, credit, total_tokens,"
               " ROUND(100.0*credit/(SELECT SUM(credit) FROM agg),2) AS credit_pct,"
               " ROUND(100.0*total_tokens/(SELECT SUM(total_tokens) FROM agg),2) AS token_pct"
               " FROM agg ORDER BY credit DESC, total_tokens DESC")
        with closing(get_conn(self.db_path)) as c:
            rows = c.execute(sql).fetchall()
        return {"ok": True, "data": [dict(r) for r in rows]}

    def projects(self, agent="all"):
        """项目分布（聚合缓存）：jsonl 项目 + CodeBuddy 各端（以 client 为项目名，积分并入）。"""
        return self._cached(f"projects:{agent}", lambda: self._projects(agent))

    def clients(self, agent="all"):
        """客户端分布：jsonl → 各 agent 的数据源名；official → 按 client 聚合。"""
        return self._cached(f"clients:{agent}", lambda: self._clients(agent))

    def agents(self):
        """全部 agent 的合计 + 元信息（label / 是否含积分）——看板「按 agent 对比」用。"""
        return self._cached("agents", lambda: self._agents())

    def _agents(self):
        with closing(get_conn(self.db_path)) as c:
            rows = c.execute("SELECT * FROM v_agent").fetchall()
        data = []
        for r in rows:
            d = dict(r)
            src = agent_registry.get_source(d["agent"])
            d["label"] = src.label if src else ("CodeBuddy" if d["agent"] == "codebuddy"
                                                else d["agent"])
            # 有积分概念才显示积分列；未知来源按「有」处理（保守，不隐藏数据）
            d["has_credit"] = bool(src.has_credit) if src else True
            d["order"] = src.order if src else 900
            data.append(d)
        data.sort(key=lambda x: (x["order"], x["agent"]))
        return {"ok": True, "data": data}

    def _projects(self, agent="all"):
        """项目分布：v_turn_total 按 project 聚合（jsonl 项目 + CodeBuddy client 同口径）。"""
        where, _ = self._conds("project != ''", self._agent_cond(agent))
        with closing(get_conn(self.db_path)) as c:
            src = self._src(c)
            rows = c.execute(
                f"""SELECT project, COUNT(*) AS turns, SUM(api_calls) AS api_calls,
                          SUM(credit) AS credit, SUM(total_tokens) AS total_tokens
                   FROM {src} {where}
                   GROUP BY project ORDER BY credit DESC"""
            ).fetchall()
        all_rows = [dict(r) for r in rows]
        tot = sum(r["credit"] for r in all_rows) or 1
        for r in all_rows:
            r["credit_pct"] = round(r["credit"] / tot * 100, 1)
        return {"ok": True, "data": all_rows}

    def _clients(self, agent="all"):
        """客户端分布：jsonl 一律视为 WorkBuddy 本地客户端，official 按 client 字段聚合。"""
        where, _ = self._conds("source='jsonl'", self._agent_cond(agent))
        with closing(get_conn(self.db_path)) as c:
            src = self._src(c)
            wb = c.execute(
                f"SELECT SUM(credit) AS credit FROM {src} {where}"
            ).fetchone()
            # 官方账单没有 agent 列：只在「全部」或明确筛 codebuddy 时纳入
            off = []
            if not agent or agent in ("all", "codebuddy"):
                off = c.execute(
                    "SELECT COALESCE(NULLIF(client,''),'CodeBuddy') AS client, "
                    "SUM(credit) AS credit "
                    "FROM ods_official_request GROUP BY client ORDER BY credit DESC"
                ).fetchall()
        data = []
        if wb and (wb["credit"] or 0) > 0:
            data.append({"client": "WorkBuddy", "credit": wb["credit"]})
        for r in off:
            rd = dict(r)
            if (rd.get("credit") or 0) > 0:
                data.append({"client": rd["client"], "credit": rd["credit"]})
        if not data:
            return {"ok": True, "data": []}
        tot = sum(r["credit"] for r in data) or 1
        for r in data:
            r["credit_pct"] = round(r["credit"] / tot * 100, 1)
        return {"ok": True, "data": data}

    def tops(self, agent="all"):
        """全部会话聚合（聚合缓存）：jsonl 会话 + CodeBuddy 官方请求会话。"""
        return self._cached(f"tops:{agent}", lambda: self._tops(agent))

    def _tops(self, agent="all"):
        """全部会话聚合：v_turn_total 按 session 聚合（两条来源统一）。"""
        where, _ = self._conds(self._agent_cond(agent))
        with closing(get_conn(self.db_path)) as c:
            src = self._src(c)
            rows = c.execute(
                f"""SELECT session_id, project, title, COUNT(*) AS turns,
                          SUM(api_calls) AS api_calls, SUM(credit) AS credit,
                          SUM(total_tokens) AS total_tokens, MAX(last_time) AS last_time,
                          SUM(cached_tokens) AS cached_tokens, SUM(miss_tokens) AS miss_tokens,
                          SUM(completion_tokens) AS completion_tokens,
                          MAX(agent) AS agent
                   FROM {src} {where} GROUP BY session_id"""
            ).fetchall()
        return {"ok": True, "data": [dict(r) for r in rows]}

    def turns(self, session_id):
        with closing(get_conn(self.db_path)) as c:
            rows = wb_dw.session_latest_turns(c, session_id, 500)
        return {"ok": True, "data": rows}

    def calls(self, turn_id):
        with closing(get_conn(self.db_path)) as c:
            if turn_id.startswith("official:"):
                rows = c.execute(
                    """SELECT request_time AS call_time, model, credit,
                              0 AS prompt_tokens, 0 AS completion_tokens, 0 AS total_tokens,
                              0 AS cached_tokens, 0 AS miss_tokens, 0 AS thinking_tokens
                       FROM ods_official_request WHERE request_id=? LIMIT 1""",
                    (turn_id[len("official:"):],),
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT datetime(ts_ms/1000,'unixepoch','localtime') AS call_time,
                              model, credit, prompt_tokens, completion_tokens,
                              total_tokens, cached_tokens, miss_tokens, thinking_tokens
                       FROM v_call WHERE turn_id=? ORDER BY ts_ms""",
                    (turn_id,),
                ).fetchall()
        return {"ok": True, "data": [dict(r) for r in rows]}

    def sessions(self):
        """会话维度（标题/项目），供前端补全会话名（含 CodeBuddy 官方会话）。"""
        with closing(get_conn(self.db_path)) as c:
            rows = c.execute("SELECT session_id, title, project FROM v_session").fetchall()
            cb = c.execute(
                """SELECT 'official:'||request_id AS session_id,
                          COALESCE(NULLIF(substr(user_prompt,1,40),''),'(无输入)') AS title,
                          COALESCE(NULLIF(client,''),'CodeBuddy') AS project
                   FROM ods_official_request WHERE client <> 'WorkBuddy'"""
            ).fetchall()
        return {"ok": True, "data": [dict(r) for r in rows] + [dict(r) for r in cb]}

    def active_sessions(self, window=3):
        """近 N 秒有 LLM 调用的活跃会话及其最新轮次（悬浮球取数，替代直查 DB）。
        直查 ODS 走索引；user_prompt 用 Python 补充（SQL 聚合子查询会 misuse）。"""
        window = max(int(window or 3), 1)
        now_ms = int(time.time() * 1000)
        with closing(get_conn(self.db_path)) as c:
            sids = [r[0] for r in c.execute(
                """SELECT DISTINCT session_id FROM ods_jsonl_event
                   WHERE raw_usage_json IS NOT NULL AND ts_ms >= ?""",
                (now_ms - window * 1000,))]
            out = []
            for sid in sids:
                t = c.execute(
                    """SELECT o.request_id AS turn_id,
                              MIN(o.ts_ms) AS first_ts, MAX(o.ts_ms) AS last_ts,
                              COUNT(*) AS api_calls,
                              ROUND(SUM(COALESCE(json_extract(o.raw_usage_json,'$.credit'),0)),2) AS credit,
                              SUM(COALESCE(json_extract(o.raw_usage_json,'$.total_tokens'),0)) AS total_tokens,
                              MAX(o.project) AS project,
                              (SELECT ai_title FROM ods_jsonl_event
                                WHERE session_id = ? AND ai_title IS NOT NULL
                                ORDER BY ts_ms DESC LIMIT 1) AS title
                       FROM ods_jsonl_event o
                       WHERE o.session_id = ? AND o.raw_usage_json IS NOT NULL
                       GROUP BY o.request_id ORDER BY last_ts DESC LIMIT 1""",
                    (sid, sid)).fetchone()
                if not t:
                    continue
                d = dict(t)
                d["first_time"] = wb_common.ts_to_str(d["first_ts"])
                d["last_time"] = wb_common.ts_to_str(d["last_ts"])
                up = c.execute(
                    """SELECT user_prompt FROM ods_jsonl_event
                       WHERE session_id=? AND event_type='message' AND role='user'
                         AND user_prompt IS NOT NULL AND ts_ms <= ?
                       ORDER BY ts_ms DESC LIMIT 1""",
                    (sid, d["first_ts"])).fetchone()
                d["user_prompt"] = (up[0] if up else "") or ""
                out.append(d)
        out.sort(key=lambda x: x["last_ts"], reverse=True)
        return {"ok": True, "data": out}

    # ---- Agent 登记册（读 + 开关）----

    @staticmethod
    def _registry_module():
        """惰性加载 app/wb_agent_registry.py（在上级目录，需补 sys.path）。"""
        try:
            import wb_agent_registry as REG
            return REG
        except ImportError:
            import sys
            parent = os.path.dirname(BASE)
            if parent not in sys.path:
                sys.path.insert(0, parent)
            try:
                import wb_agent_registry as REG
                return REG
            except Exception:
                return None

    def registry(self):
        """登记册全文，供看板「Agent 接入」面板渲染。

        额外算一个 `has_data`：该 agent 在 ODS 里**是否真有用量数据**。
        它和 `enabled` 是两件事 —— 启用只代表"我允许它参与"，有没有数据取决于
        用户是否真的用过那个 agent。UI 上区分开，避免"开了却没数据"的困惑。
        """
        REG = self._registry_module()
        if REG is None:
            return {"ok": False, "error": "登记册模块不可用（wb_agent_registry.py 缺失）"}
        data = REG.load(force=True)
        with_data = set()
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=3)
            try:
                for (a,) in conn.execute(
                        "SELECT DISTINCT agent FROM ods_jsonl_event WHERE agent IS NOT NULL"):
                    with_data.add(a)
            finally:
                conn.close()
        except Exception:
            pass
        rows = []
        # T1：健康检查（路径当下是否还在）+ 实时活跃层级（L1 hook/L2 日志/L3 进程）
        try:
            hmap = REG.health()
        except Exception:
            hmap = {}
        levels = {}
        try:
            import wb_agent_presence as PRESENCE
            levels = {k: v["level"]
                      for k, v in (PRESENCE.detect_detail() or {}).items()}
        except Exception:
            levels = {}
        for key, spec in data["agents"].items():
            rows.append({
                "key": key, "label": spec["label"],
                "enabled": spec["enabled"], "verified": spec["verified"],
                "order": spec["order"],
                "accessory": spec["accessory"], "accent": spec["accent"],
                "caps": spec["caps"], "presence": spec["presence"],
                "config_paths": spec["config_paths"], "log_paths": spec["log_paths"],
                "notes": spec["notes"], "has_data": key in with_data,
                "health": (hmap.get(key) or {}).get("health", "unknown"),
                "level": levels.get(key, 0),
            })
        rows.sort(key=lambda r: (not r["enabled"], r["order"], r["label"]))
        return {"ok": True, "data": {"agents": rows, "source": data["source"]}}

    def set_agent_enabled(self, key, enabled):
        """改某个 agent 的启用开关（唯一会写配置的接口）。"""
        REG = self._registry_module()
        if REG is None:
            return {"ok": False, "error": "登记册模块不可用"}
        return REG.set_enabled(key, enabled)

    def health(self):
        with closing(get_conn(self.db_path)) as c:
            last = c.execute("SELECT value FROM meta WHERE key='last_collect'").fetchone()
            n_ods = c.execute("SELECT COUNT(*) FROM ods_jsonl_event").fetchone()[0]
        return {"ok": True, "data": {
            "last_collect": last["value"] if last else "",
            "ods_events": n_ods,
        }}


class Handler(BaseHTTPRequestHandler):
    db_path = DEFAULT_DB
    api = None

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        if os.path.exists(path):
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # 禁缓存：每次都取最新文件，避免改了前端浏览器仍显示旧版
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def _error_boundary(self, e):
        """统一异常边界：记录一条简洁可定位的诊断（路径 + 类型 + 位置，不刷 traceback），
        返回结构化 JSON 500，避免连接直接断开导致前端只见 Failed to fetch。"""
        tb = traceback.extract_tb(sys.exc_info()[2])
        loc = f"{os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else "?"
        print(f"[api] 500 {self.path.split('?')[0]}: {type(e).__name__}: {e} ({loc})")
        try:
            self._json({"ok": False, "error": "internal server error"}, 500)
        except Exception:
            pass   # 响应也失败（客户端已断开）时放弃，不再二次抛错

    # 客户端主动断开（刷新页面/取消请求）的典型异常：安静结束，不打印 traceback
    _CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)

    def _client_gone(self):
        """连接已坏，不再尝试写任何响应；标记关闭，静默返回。
        不打日志——页面一次刷新可能并发十几个请求，逐条打印就是另一种风暴。"""
        self.close_connection = True

    def do_GET(self):
        try:
            self._route_get()
        except self._CLIENT_GONE:
            self._client_gone()
        except Exception as e:
            self._error_boundary(e)

    def _route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._file(os.path.join(BASE, "dashboard.html"), "text/html; charset=utf-8")
        elif path == "/chart.umd.min.js":
            self._file(os.path.join(BASE, "data", "chart.umd.min.js"), "application/javascript")
        elif path == "/api/agents":
            self._json(self.api.agents())
        elif path == "/api/kpi":
            self._json(self.api.kpi(qs.get("days", ["30"])[0], qs.get("agent", ["all"])[0]))
        elif path == "/api/daily":
            self._json(self.api.daily(qs.get("days", ["all"])[0], qs.get("agent", ["all"])[0]))
        elif path == "/api/models":
            self._json(self.api.models(qs.get("days", ["30"])[0], qs.get("agent", ["all"])[0]))
        elif path == "/api/projects":
            self._json(self.api.projects(qs.get("agent", ["all"])[0]))
        elif path == "/api/clients":
            self._json(self.api.clients(qs.get("agent", ["all"])[0]))
        elif path == "/api/tops":
            self._json(self.api.tops(qs.get("agent", ["all"])[0]))
        elif path == "/api/sessions":
            self._json(self.api.sessions())
        elif path == "/api/turns":
            sid = qs.get("session_id", [""])[0]
            self._json(self.api.turns(sid))
        elif path == "/api/calls":
            rid = qs.get("turn_id", [""])[0]
            self._json(self.api.calls(rid))
        elif path == "/api/health":
            self._json(self.api.health())
        elif path == "/api/registry":
            self._json(self.api.registry())
        elif path == "/api/active-sessions":
            self._json(self.api.active_sessions(qs.get("window", ["3"])[0]))
        else:
            self._json({"ok": False, "error": f"unknown api {path}"}, 404)

    def do_POST(self):
        try:
            self._route_post()
        except self._CLIENT_GONE:
            self._client_gone()
        except Exception as e:
            self._error_boundary(e)

    def _route_post(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/registry":
            # 唯一会写配置的接口。只允许改 enabled 布尔值（白名单在 wb_agent_registry.set_enabled）
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                self._json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return
            self._json(self.api.set_agent_enabled(body.get("key"), body.get("enabled")))
        elif parsed.path == "/api/rebuild":
            # 不在内部吞异常返回 str(e)（会泄漏细节且绕过统一边界）；
            # 失败时异常向上进入 do_POST 统一边界：一条精简诊断 + 脱敏 JSON 500
            n1, _ = wb_collect.collect_once(self.db_path, verbose=False)
            wb_dw.ensure_views(self.db_path)
            self._json({"ok": True, "data": {"collected": n1, "msg": "增量采集 + 视图就绪"}})
        else:
            self._json({"ok": False, "error": f"unknown api {parsed.path}"}, 404)

    def log_message(self, *a):
        pass


def _watchdog(port, interval=30):
    """自检看门狗：每 interval 秒自连 /api/health，连续失败 2 次则自杀退出。
    解决"进程活着但 HTTP 卡死"的服务假死（launchd KeepAlive 只在进程退出时重启）。"""
    import urllib.request as _ur
    fails = 0
    opener = _ur.build_opener(_ur.ProxyHandler({}))   # 绕过系统代理，直连本机
    while True:
        time.sleep(interval)
        try:
            with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
                ok = bool(json.loads(r.read().decode()).get("ok"))
            fails = 0 if ok else fails + 1
        except Exception:
            fails += 1
        if fails >= 2:
            print(f"[api] 看门狗：连续 {fails} 次自检失败，自杀退出（launchd 自动拉起）")
            os._exit(1)


class QuietServer(ThreadingHTTPServer):
    """ThreadingHTTPServer + 客户端断开静默化。
    handler 返回后 StreamRequestHandler.finish() 刷 wfile 仍可能抛
    BrokenPipe/ConnectionReset → socketserver 默认 handle_error 会打完整
    traceback（页面刷新时的风暴源头之一），这里对断开类异常静默。"""

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, Handler._CLIENT_GONE):
            return   # 客户端主动断开，非服务端错误，安静关闭
        print(f"[api] handler error: {type(exc).__name__}: {exc}")


def serve(port=8801, db_path=None):
    Handler.db_path = db_path or DEFAULT_DB
    Handler.api = Api(Handler.db_path)
    # 启动时先增量采集 + 确保 VIEW 存在（VIEW 实时查询，无需物化重建）
    try:
        wb_collect.collect_once(Handler.db_path, verbose=False)
        wb_dw.ensure_views(Handler.db_path)
        print("[api] 启动时增量采集 + 视图就绪")
    except Exception as e:
        print(f"[api] 启动采集异常: {e}")
    # 内置 5 秒监听线程：检测 jsonl 变更即增量采集 + 联动重建数仓（保证看板读最新）
    def _sync(db, n):
        # VIEW 方案：ODS 实时入库后无需重建，聚合视图自动反映最新数据
        if n > 0:
            print(f"[api] 采集 {n} 条（实时视图，无需重建）")
    threading.Thread(
        target=wb_collect.watch_changes,
        # sources=None → 监听全部已注册数据源（WorkBuddy + Codex + …）
        args=(Handler.db_path, None, 1, False, _sync),  # 1s 监听，压低采集延迟
        daemon=True,
    ).start()
    print("[api] 已启动 1s 变更监听（jsonl 更新 → 实时入库，VIEW 免重建）")
    # 日维护：raw 归档 TTL 每天一次；累计释放 >150MB 自动 VACUUM（P0 数仓膨胀）
    def _maintenance(db):
        while True:
            freed = 0
            try:
                c = wb_collect.get_conn(db)
                today = time.strftime("%Y-%m-%d")
                if wb_collect.meta_get(c, "last_raw_maintain") != today:
                    deleted, freed, orphans = wb_collect.raw_retention(c)
                    wb_collect.meta_set(c, "last_raw_maintain", today)
                    c.commit()
                    print(f"[api] raw 归档日维护：删 {deleted} 行 / 释放 "
                          f"{freed / 1048576:.0f}MB / 孤儿 {orphans}")
                pending = float(wb_collect.meta_get(c, "pending_vacuum_bytes") or 0)
                if pending + freed > 150 * 1048576:
                    before = os.path.getsize(db)
                    wb_collect.vacuum_db(c)
                    wb_collect.meta_set(c, "pending_vacuum_bytes", "0")
                    print(f"[api] VACUUM：{before / 1048576:.0f}MB → "
                          f"{os.path.getsize(db) / 1048576:.0f}MB")
                elif freed:
                    wb_collect.meta_set(c, "pending_vacuum_bytes", str(pending + freed))
                c.close()
            except Exception as e:
                print(f"[api] 日维护异常（下轮重试）: {e}")
            time.sleep(6 * 3600)
    threading.Thread(target=_maintenance, args=(Handler.db_path,), daemon=True).start()
    print("[api] 已启动日维护（raw 归档 TTL + 满 150MB 自动 VACUUM）")
    # 看门狗：HTTP 卡死自检，连续失败自杀 → launchd 拉起（防服务假死）
    threading.Thread(target=_watchdog, args=(port, 30), daemon=True).start()
    print("[api] 已启动 30s 看门狗（自检失败自动重启）")
    # bind 容错：端口被占（多实例竞争）时等待重试，避免崩溃触发 KeepAlive 循环
    import time as _t
    while True:
        try:
            srv = QuietServer(("127.0.0.1", port), Handler)
            break
        except OSError as e:
            print(f"[api] 端口 {port} 被占用，5s 后重试: {e}")
            _t.sleep(5)
    print(f"[api] WorkBuddy 消耗看板接口服务: http://127.0.0.1:{port}")
    srv.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 消耗看板接口服务")
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    serve(args.port, args.db)


if __name__ == "__main__":
    main()
