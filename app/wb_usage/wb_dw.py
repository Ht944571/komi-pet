#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb_dw.py — WorkBuddy 消耗看板·数仓建模模块（VIEW 实时聚合版）

分层（SQLite 单文件，物化层已于 2026-08-30 移除——API 全走实时 VIEW）：
  ODS  →  v_call / v_msg / v_session / v_turn      （明细/轮次视图）
       →  v_daily / v_model / v_project            （汇总视图）
  CodeBuddy 官方账单（cb_sync_dw.py 写入）：ods_official_request → dws_client_daily

口径（PRD 4.2.2 双写，勿改）：
  - 轮次 turn = conversationRequestId；轮次内 credit/tokens 各分项求和
  - user_prompt 归属 = 轮次首个事件之前最近的用户消息（同会话内）
  - 日期归属 = 轮次 first_time 的本地时区日期（wb_common.day_of）
  - 缓存命中率 = cached ÷ (cached + miss)，分母为 0 记 NULL

VIEW 实时查询，ODS 增量入库后聚合自动反映最新数据，无需物化重建。
用法：python3 wb_dw.py [--db 路径]   （ensure_views 幂等，可重复执行）
"""
import argparse
import os
import sqlite3

from wb_common import day_of, ts_to_str

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wb_usage_dw.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
-- CodeBuddy 官方账单表：核心 schema 预建空表（v_turn_total 依赖），
-- 数据仍由 cb_sync_dw.py --apply 写入（CREATE IF NOT EXISTS 不冲突，官方源保持可选）
CREATE TABLE IF NOT EXISTS ods_official_request (
    request_id   TEXT PRIMARY KEY,
    credit       REAL,
    model        TEXT,
    client       TEXT,
    request_time TEXT,
    day          TEXT,
    user_prompt  TEXT,
    agent_purpose TEXT,
    synced_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ods_off_day ON ods_official_request(day);
CREATE INDEX IF NOT EXISTS idx_ods_off_client ON ods_official_request(client);
"""


# ========== VIEW 层（实时查询，替代物化 rebuild） ==========
VIEW_DDL = """
-- v_call：LLM 调用明细（原 dwd_api_call），usage 从 raw_usage_json JSON 提取
-- 多 agent：agent 列区分来源（NULL 的历史行按 workbuddy 解释——本视图最早的
-- 唯一来源就是 WorkBuddy，语义等价且免回填 500 万行）
CREATE VIEW IF NOT EXISTS v_call AS
SELECT o.session_id, o.request_id AS turn_id, o.ts_ms, o.model, o.cwd,
       COALESCE(o.agent, 'workbuddy') AS agent,
       COALESCE(o.project, '') AS project,
       COALESCE(json_extract(o.raw_usage_json, '$.credit'), 0) AS credit,
       COALESCE(json_extract(o.raw_usage_json, '$.prompt_tokens'), 0) AS prompt_tokens,
       COALESCE(json_extract(o.raw_usage_json, '$.completion_tokens'), 0) AS completion_tokens,
       COALESCE(json_extract(o.raw_usage_json, '$.total_tokens'), 0) AS total_tokens,
       COALESCE(json_extract(o.raw_usage_json, '$.prompt_cache_hit_tokens'),
                json_extract(o.raw_usage_json, '$.prompt_tokens_details.cached_tokens'), 0) AS cached_tokens,
       -- 缺失 prompt_cache_miss_tokens 时按 wb_common 口径回退：prompt - cached - write（≥0）
       CASE WHEN json_extract(o.raw_usage_json, '$.prompt_cache_miss_tokens') IS NOT NULL
            THEN json_extract(o.raw_usage_json, '$.prompt_cache_miss_tokens')
            ELSE MAX(COALESCE(json_extract(o.raw_usage_json, '$.prompt_tokens'), 0)
                 - COALESCE(json_extract(o.raw_usage_json, '$.prompt_cache_hit_tokens'),
                            json_extract(o.raw_usage_json, '$.prompt_tokens_details.cached_tokens'), 0)
                 - COALESCE(json_extract(o.raw_usage_json, '$.prompt_cache_write_tokens'), 0), 0)
       END AS miss_tokens,
       COALESCE(json_extract(o.raw_usage_json, '$.prompt_cache_write_tokens'), 0) AS cache_write_tokens,
       COALESCE(json_extract(o.raw_usage_json, '$.completion_thinking_tokens'),
                json_extract(o.raw_usage_json, '$.completion_tokens_details.reasoning_tokens'), 0) AS thinking_tokens
FROM ods_jsonl_event o
WHERE o.raw_usage_json IS NOT NULL
  AND COALESCE(json_extract(o.raw_usage_json, '$.prompt_tokens'), 0) > 0;

-- v_msg：用户消息（原 dwd_user_msg）
CREATE VIEW IF NOT EXISTS v_msg AS
SELECT session_id, ts_ms, user_prompt FROM ods_jsonl_event
WHERE event_type = 'message' AND role = 'user' AND user_prompt IS NOT NULL;

-- v_session：会话维度（原 dwd_session），title=最后 ai_title，project=ODS.project
CREATE VIEW IF NOT EXISTS v_session AS
SELECT s.session_id,
       COALESCE(t.title, '') AS title,
       COALESCE(s.project, '') AS project,
       COALESCE(s.cwd, '') AS cwd,
       MIN(s.ts_ms) AS first_ts, MAX(s.ts_ms) AS last_ts
FROM ods_jsonl_event s
LEFT JOIN (
    SELECT session_id, ai_title AS title FROM (
        SELECT session_id, ai_title, ts_ms,
               ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY ts_ms DESC) AS rn
        FROM ods_jsonl_event WHERE ai_title IS NOT NULL
    ) WHERE rn = 1
) t ON t.session_id = s.session_id
WHERE s.session_id != ''
GROUP BY s.session_id;

-- v_turn：轮次聚合（原 dws_turn），credit/tokens 求和，user_prompt=轮次前最近用户消息
-- 多 agent：agent 进入分组键（同一 turn_id 理论上不会跨 agent，纳入分组只是更稳妥）
CREATE VIEW IF NOT EXISTS v_turn AS
WITH calls AS (
    SELECT session_id, turn_id, ts_ms, model, project, agent,
           credit, prompt_tokens, completion_tokens, total_tokens,
           cached_tokens, miss_tokens, thinking_tokens
    FROM v_call
),
turns AS (
    SELECT turn_id, session_id, MAX(agent) AS agent,
           MIN(ts_ms) AS first_ts, MAX(ts_ms) AS last_ts,
           COUNT(*) AS api_calls,
           SUM(credit) AS credit,
           SUM(prompt_tokens) AS prompt_tokens,
           SUM(completion_tokens) AS completion_tokens,
           SUM(total_tokens) AS total_tokens,
           SUM(cached_tokens) AS cached_tokens,
           SUM(miss_tokens) AS miss_tokens,
           SUM(thinking_tokens) AS thinking_tokens,
           MAX(project) AS project,
           GROUP_CONCAT(DISTINCT model) AS models
    FROM calls GROUP BY turn_id, agent
)
SELECT t.*,
       date(t.first_ts / 1000, 'unixepoch', 'localtime') AS day,
       datetime(t.first_ts / 1000, 'unixepoch', 'localtime') AS first_time,
       datetime(t.last_ts / 1000, 'unixepoch', 'localtime') AS last_time,
       (SELECT user_prompt FROM v_msg m
         WHERE m.session_id = t.session_id AND m.ts_ms <= t.first_ts
         ORDER BY m.ts_ms DESC LIMIT 1) AS user_prompt,
       COALESCE((SELECT ai_title FROM ods_jsonl_event o2
                  WHERE o2.session_id = t.session_id AND o2.ai_title IS NOT NULL
                  ORDER BY o2.ts_ms DESC LIMIT 1), '') AS title,
       COALESCE((SELECT project FROM ods_jsonl_event o3
                  WHERE o3.session_id = t.session_id AND o3.project != ''
                  ORDER BY o3.ts_ms ASC LIMIT 1), t.project) AS project_final
FROM turns t;

-- v_daily：按天汇总（原 dws_daily / ads_daily）
CREATE VIEW IF NOT EXISTS v_daily AS
SELECT day, COUNT(*) AS turns, SUM(api_calls) AS api_calls,
       SUM(credit) AS credit, SUM(total_tokens) AS total_tokens,
       SUM(cached_tokens) AS cached_tokens, SUM(miss_tokens) AS miss_tokens,
       SUM(completion_tokens) AS completion_tokens, SUM(thinking_tokens) AS thinking_tokens,
       CASE WHEN SUM(cached_tokens) + SUM(miss_tokens) > 0
            THEN 1.0 * SUM(cached_tokens) / (SUM(cached_tokens) + SUM(miss_tokens))
            ELSE NULL END AS cache_hit_rate
FROM v_turn WHERE day != '' GROUP BY day;

-- v_model：按模型汇总（原 dws_model / ads_model）
CREATE VIEW IF NOT EXISTS v_model AS
SELECT model, COUNT(*) AS api_calls, COUNT(DISTINCT turn_id) AS turns,
       SUM(credit) AS credit, SUM(prompt_tokens) AS prompt_tokens,
       SUM(completion_tokens) AS completion_tokens, SUM(total_tokens) AS total_tokens,
       SUM(cached_tokens) AS cached_tokens, SUM(miss_tokens) AS miss_tokens,
       SUM(thinking_tokens) AS thinking_tokens
FROM v_call GROUP BY model;

-- v_project：按项目汇总（原 dws_project / ads_project）
CREATE VIEW IF NOT EXISTS v_project AS
SELECT project_final AS project, COUNT(*) AS turns, SUM(api_calls) AS api_calls,
       SUM(credit) AS credit, SUM(total_tokens) AS total_tokens
FROM v_turn WHERE project_final != '' GROUP BY project_final;

-- v_turn_total：合并口径统一视图（jsonl 轮次 + CodeBuddy 官方请求，source 区分来源）
-- 替代 kpi/daily/projects/tops 四处 Python 手写拼接（2026-08-30 收敛）
-- 多 agent：末尾追加 agent 列（jsonl 侧来自各数据源；official 侧固定 codebuddy）
CREATE VIEW IF NOT EXISTS v_turn_total AS
SELECT turn_id, day, session_id, project_final AS project, title,
       first_time, last_time, user_prompt, api_calls, credit,
       total_tokens, cached_tokens, miss_tokens, completion_tokens, thinking_tokens,
       COALESCE(models, '') AS model_used, 'jsonl' AS source,
       COALESCE(agent, 'workbuddy') AS agent
FROM v_turn
UNION ALL
SELECT 'official:'||request_id AS turn_id, day,
       'official:'||request_id AS session_id,
       COALESCE(NULLIF(client,''),'CodeBuddy') AS project,
       COALESCE(NULLIF(substr(user_prompt,1,40),''),'(无输入)') AS title,
       request_time AS first_time, request_time AS last_time,
       COALESCE(user_prompt,'') AS user_prompt,
       1 AS api_calls, credit, 0 AS total_tokens, 0 AS cached_tokens,
       0 AS miss_tokens, 0 AS completion_tokens, 0 AS thinking_tokens,
       COALESCE(model,'') AS model_used, 'official' AS source,
       'codebuddy' AS agent
FROM ods_official_request
WHERE client <> 'WorkBuddy';

-- ========== 多 agent 维度视图（v_agent*）==========
-- 既有 v_daily / v_model / v_project 保持「全部 agent 合计」语义不变（避免破坏
-- 现有接口口径），按 agent 拆分的需求由下面这几个视图承担。

-- v_agent：每个 agent 的合计（看板「按 agent 对比」的数据源）
CREATE VIEW IF NOT EXISTS v_agent AS
SELECT agent,
       COUNT(*) AS turns,
       SUM(api_calls) AS api_calls,
       SUM(credit) AS credit,
       SUM(total_tokens) AS total_tokens,
       SUM(cached_tokens) AS cached_tokens,
       SUM(miss_tokens) AS miss_tokens,
       SUM(completion_tokens) AS completion_tokens,
       SUM(thinking_tokens) AS thinking_tokens,
       COUNT(DISTINCT session_id) AS sessions,
       MIN(day) AS first_day, MAX(day) AS last_day,
       CASE WHEN SUM(cached_tokens) + SUM(miss_tokens) > 0
            THEN 1.0 * SUM(cached_tokens) / (SUM(cached_tokens) + SUM(miss_tokens))
            ELSE NULL END AS cache_hit_rate
FROM v_turn_total WHERE agent != '' GROUP BY agent;

-- v_agent_daily：agent × 日
CREATE VIEW IF NOT EXISTS v_agent_daily AS
SELECT agent, day, COUNT(*) AS turns, SUM(api_calls) AS api_calls,
       SUM(credit) AS credit, SUM(total_tokens) AS total_tokens,
       SUM(cached_tokens) AS cached_tokens, SUM(miss_tokens) AS miss_tokens,
       SUM(completion_tokens) AS completion_tokens, SUM(thinking_tokens) AS thinking_tokens
FROM v_turn_total WHERE day != '' GROUP BY agent, day;

-- v_agent_model：agent × 模型
CREATE VIEW IF NOT EXISTS v_agent_model AS
SELECT agent, model, COUNT(*) AS api_calls, COUNT(DISTINCT turn_id) AS turns,
       SUM(credit) AS credit, SUM(total_tokens) AS total_tokens
FROM v_call GROUP BY agent, model;

-- v_agent_project：agent × 项目
CREATE VIEW IF NOT EXISTS v_agent_project AS
SELECT agent, project_final AS project, COUNT(*) AS turns, SUM(api_calls) AS api_calls,
       SUM(credit) AS credit, SUM(total_tokens) AS total_tokens
FROM v_turn WHERE project_final != '' GROUP BY agent, project_final;
"""


def get_conn(db_path):
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")      # WAL：读写并发不互锁（1s 高频采集必需）
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    return conn


def ensure_views(db_path=None):
    """创建/更新 VIEW 层 + 回填 ODS.project（Python basename）+ ANALYZE。
    VIEW 实时查询，ODS 入库后聚合自动最新，幂等可重复执行。"""
    db_path = db_path or DEFAULT_DB
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        _ensure_views_conn(conn)
    finally:
        conn.close()
    return True


def _ensure_views_conn(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    # 全新库兜底：ODS 表结构定义在 wb_collect；尚未采集过（表不存在）时按同一
    # schema 建空表，保证 VIEW 层可直接查询（README 直接启动场景不再报 no such table）
    ods_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ods_jsonl_event'").fetchone()
    if not ods_exists:
        from wb_collect import SCHEMA as ODS_SCHEMA
        conn.executescript(ODS_SCHEMA)
    # 迁移：旧库 ODS 表无 project 列 → 补列（SQLite 无 ADD COLUMN IF NOT EXISTS）。
    # 全新库 ODS 表尚不存在（由 wb_collect 创建）时跳过迁移，避免 ALTER 报错
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ods_jsonl_event)")] if ods_exists else []
    if ods_exists and "project" not in cols:
        conn.execute("ALTER TABLE ods_jsonl_event ADD COLUMN project TEXT")
        conn.commit()
    # P0-3 迁移（2026-08-30）：raw_json/content_json 拆到 ods_jsonl_raw（主表 301MB → 消费列瘦身）。
    # 检测到旧列存在才迁移，幂等；DROP COLUMN 自动重写表，行数不变。
    if "raw_json" in cols:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ods_jsonl_raw (
                file_path TEXT NOT NULL, line_no INTEGER NOT NULL,
                raw_json TEXT, PRIMARY KEY (file_path, line_no))""")
        conn.execute(
            "INSERT OR IGNORE INTO ods_jsonl_raw(file_path, line_no, raw_json) "
            "SELECT file_path, line_no, raw_json FROM ods_jsonl_event")
        conn.execute("ALTER TABLE ods_jsonl_event DROP COLUMN raw_json")
        conn.execute("ALTER TABLE ods_jsonl_event DROP COLUMN content_json")
        conn.commit()
        print("[dw] ODS 迁移：raw_json 已拆至 ods_jsonl_raw（主表瘦身）")
    # 回填 project：仅在存在空值时执行（全表置空+重填只在首次/迁移时）
    try:
        need = conn.execute(
            "SELECT COUNT(*) FROM ods_jsonl_event WHERE project IS NULL OR project=''"
        ).fetchone()[0]
        if need > 0:
            conn.execute("DROP TABLE IF EXISTS _tmp_proj_backfill")
            conn.execute("CREATE TEMP TABLE _tmp_proj_backfill(file_path TEXT, line_no INTEGER, project TEXT)")
            rows = conn.execute(
                "SELECT file_path, line_no, cwd FROM ods_jsonl_event WHERE project IS NULL OR project=''"
            ).fetchall()
            import os as _os
            conn.executemany("INSERT INTO _tmp_proj_backfill VALUES(?,?,?)",
                             [(fp, ln, _os.path.basename((cw or "").rstrip("/")) or "")
                              for fp, ln, cw in rows])
            conn.execute(
                """UPDATE ods_jsonl_event SET project = (
                     SELECT b.project FROM _tmp_proj_backfill b
                     WHERE b.file_path = ods_jsonl_event.file_path
                       AND b.line_no = ods_jsonl_event.line_no
                   ) WHERE project IS NULL OR project = ''"""
            )
            conn.execute("DROP TABLE _tmp_proj_backfill")
            conn.commit()
    except Exception:
        pass
    # 视图强制重建：CREATE VIEW IF NOT EXISTS 不会更新已有定义，口径变更必须先 DROP
    for (vname,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name LIKE 'v_%'").fetchall():
        conn.execute(f'DROP VIEW IF EXISTS "{vname}"')
    for stmt in VIEW_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    # ANALYZE 仅首次执行（统计信息不常变，避免每次启动 1.6s）
    try:
        done = conn.execute("SELECT 1 FROM meta WHERE key='analyzed'").fetchone()
        if not done:
            conn.execute("ANALYZE")
            conn.execute("INSERT OR REPLACE INTO meta(key, value, updated_at) VALUES('analyzed','1', datetime('now','localtime'))")
            conn.commit()
    except Exception:
        pass


def session_latest_turns(conn, session_id, limit=10):
    """某会话最新轮次（直查 ODS，session 过滤走索引，避免 v_turn 全量 CTE）。
    返回字段对齐 v_turn：turn_id/first_time/last_time/credit/total_tokens/
    user_prompt/title/project/api_calls/day。"""
    rows = conn.execute("""
        SELECT request_id AS turn_id,
               MIN(ts_ms) AS first_ts, MAX(ts_ms) AS last_ts,
               COUNT(*) AS api_calls,
               ROUND(SUM(COALESCE(json_extract(raw_usage_json,'$.credit'),0)),4) AS credit,
               SUM(COALESCE(json_extract(raw_usage_json,'$.total_tokens'),0)) AS total_tokens,
               SUM(COALESCE(json_extract(raw_usage_json,'$.prompt_tokens'),0)) AS prompt_tokens,
               SUM(COALESCE(json_extract(raw_usage_json,'$.completion_tokens'),0)) AS completion_tokens,
               SUM(COALESCE(json_extract(raw_usage_json,'$.prompt_cache_hit_tokens'),
                            json_extract(raw_usage_json,'$.prompt_tokens_details.cached_tokens'),0)) AS cached_tokens,
               SUM(CASE WHEN json_extract(raw_usage_json,'$.prompt_cache_miss_tokens') IS NOT NULL
                        THEN json_extract(raw_usage_json,'$.prompt_cache_miss_tokens')
                        ELSE MAX(COALESCE(json_extract(raw_usage_json,'$.prompt_tokens'),0)
                             - COALESCE(json_extract(raw_usage_json,'$.prompt_cache_hit_tokens'),
                                        json_extract(raw_usage_json,'$.prompt_tokens_details.cached_tokens'),0)
                             - COALESCE(json_extract(raw_usage_json,'$.prompt_cache_write_tokens'),0), 0)
                   END) AS miss_tokens,
               SUM(COALESCE(json_extract(raw_usage_json,'$.completion_thinking_tokens'),
                            json_extract(raw_usage_json,'$.completion_tokens_details.reasoning_tokens'),0)) AS thinking_tokens,
               MAX(project) AS project,
               GROUP_CONCAT(DISTINCT model) AS model_used
        FROM ods_jsonl_event
        WHERE session_id = ? AND raw_usage_json IS NOT NULL
        GROUP BY request_id
        ORDER BY last_ts DESC LIMIT ?
    """, (session_id, limit)).fetchall()
    # rows 列序：0 turn_id,1 first_ts,2 last_ts,3 api_calls,4 credit,5 total_tokens,
    # 6 prompt_tokens,7 completion_tokens,8 cached_tokens,9 miss_tokens,
    # 10 thinking_tokens,11 project,12 model_used
    out = []
    for r in rows:
        first_ts, last_ts = r[1], r[2]
        title_r = conn.execute(
            "SELECT ai_title FROM ods_jsonl_event WHERE session_id=? AND ai_title IS NOT NULL"
            " ORDER BY ts_ms DESC LIMIT 1", (session_id,)).fetchone()
        up_r = conn.execute(
            "SELECT user_prompt FROM ods_jsonl_event WHERE session_id=? AND event_type='message'"
            " AND role='user' AND user_prompt IS NOT NULL AND ts_ms <= ?"
            " ORDER BY ts_ms DESC LIMIT 1", (session_id, first_ts)).fetchone()
        out.append({
            "turn_id": r[0], "session_id": session_id,
            "day": ts_to_str(first_ts)[:10],
            "first_time": ts_to_str(first_ts), "last_time": ts_to_str(last_ts),
            "user_prompt": (up_r[0] if up_r else "") or "",
            "title": (title_r[0] if title_r else "") or "",
            "project": (r[11] or "") or "",
            "api_calls": r[3], "credit": r[4],
            "prompt_tokens": r[6], "completion_tokens": r[7],
            "total_tokens": r[5], "cached_tokens": r[8],
            "miss_tokens": r[9], "thinking_tokens": r[10],
            "model_used": r[12] or "",
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 数仓视图初始化")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    ensure_views(args.db)
    print("[dw] VIEW 层就绪（ODS 增量入库后聚合自动最新）")


if __name__ == "__main__":
    main()
