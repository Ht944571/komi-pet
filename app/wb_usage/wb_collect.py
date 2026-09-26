#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb_collect.py — 多 agent 用量看板·采集模块
================================================================
职责：遍历每个「agent 数据源」（见 agents/），把其会话日志增量解析进 ODS 原始表。

    WorkBuddy  → ~/.workbuddy/projects/*/*.jsonl
    Codex CLI  → ~/.codex/sessions/**/*.jsonl
    （新增源见 agents/__init__.py 的 SOURCES）

**各源只负责「行 → 规范用量行」的翻译**（归一到 agents/base.py 的
CANONICAL_USAGE_FIELDS），落库、断点续采、覆盖检测全部复用本模块的下述机制，
因此新增一个 agent 不需要碰这里的任何逻辑。

入库时写 `agent` 列区分来源；历史行该列为 NULL，视图按 'workbuddy' 解释。

特性：
  - 断点续采：meta 表按文件记录已采行数游标 + 字节偏移，仅解析新增行
  - 覆盖/截断检测（2026-08-31 三轮 P1 重写，快/慢双路径）：
      * fstate:{path}  = dev|inode|size|mtime_ns|ctime_ns 五元组（ns 级，
        ctime_ns 使任何真实写入都会改变状态）→ 完全未变的文件 0 字节跳过
      * 快路径（append，size 变大且 inode 不变）：
          - 只验证「最后已消费行」指纹（llhash，字节级 SHA-256）——更长覆盖时
            该行内容必变 → 检出；纯 append 时该行未变 → 信任前缀
          - 从 bcursor 字节偏移处 seek，只读新增行 → O(增量)，不扫完整历史
          - 每 DEEP_CHECK_EVERY 次 append 强制一次慢路径深校验（兜底
            「中间行改写且 size 变大且末尾不变」这类边界）
      * 慢路径（替换/截短/同长覆盖/周期深校验）：
          - 全读单遍流式，重算 pdigest 锚点（1..anchor 行）SHA-256 比对，
            覆盖全部已消费内容，不依赖首行/末行等易碰撞组合
          - 锚点摘要带行号（"K|sha"），快路径推进游标后锚点保持，慢路径
            比对 1..K 即证明整个已消费前缀未被改写
      * 摘要不一致 / 文件短于游标 / --full → 同一事务中删除该来源旧记录
        （主表+raw 表）、重置游标、全量重采
      * 旧版本库（无 pdigest / 无 bcursor）首次迁移：播种当前状态，
        不重采不删数据；游标之后的新行照常增量
  - 幂等：ods_jsonl_event 唯一键 (file_path, line_no)，INSERT OR IGNORE
  - 变化检测：1s 轮询 stat 五元组比对（PRD 实时性 B 机制）
  - --full 全量重采；--daemon 守护监听；--agent 只采某个源

已知边界（如实声明）：
  1. 快路径依赖「最后已消费行指纹 + 周期深校验」：中间行被改写且伴随
     size 变大、同时末尾已消费行不变时，最长滞后 DEEP_CHECK_EVERY 次
     append（深校验轮）被发现；同尺寸中间改写不走快路径（慢路径全读，无滞后）。
  2. 同一文件 stat 五元组完全不变时被原地改写（同 dev/inode/size/mtime_ns/
     ctime_ns）物理上几乎不可能（真实写入必改 ctime_ns），不做全文件常驻
     哈希的代价换算。

用法：
  python3 wb_collect.py                  # 增量采集全部源
  python3 wb_collect.py --agent codex    # 只采某个源
  python3 wb_collect.py --full           # 全量重采
  python3 wb_collect.py --daemon         # 守护：轮询监听变更
  python3 wb_collect.py --db 路径        # 指定 SQLite 路径
"""
import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
import time

from wb_common import extract_user_prompt          # noqa: F401  （供 WorkBuddy 源使用）
import agents as agent_registry

HOME = os.path.expanduser("~")
DEFAULT_PROJECTS = os.path.join(HOME, ".workbuddy", "projects")   # 兼容旧引用
DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wb_usage_dw.db")

# 快路径深校验周期：每 DEEP_CHECK_EVERY 次 append 强制一次全读验证。
# 权衡：越大 append 越省 IO，但「更长且末尾不变的中间改写」检测滞后越大。
DEEP_CHECK_EVERY = 32

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ods_jsonl_event (
    id TEXT,
    file_path TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    event_type TEXT,
    role TEXT,
    session_id TEXT,
    ts_ms INTEGER,
    cwd TEXT,
    model TEXT,
    request_id TEXT,
    raw_usage_json TEXT,
    user_prompt TEXT,
    ai_title TEXT,
    project TEXT,
    agent TEXT,
    cost_usd REAL,
    PRIMARY KEY (file_path, line_no)
);
CREATE INDEX IF NOT EXISTS idx_ods_session ON ods_jsonl_event(session_id);
CREATE INDEX IF NOT EXISTS idx_ods_ts ON ods_jsonl_event(ts_ms);
CREATE INDEX IF NOT EXISTS idx_ods_ts_evt ON ods_jsonl_event(ts_ms, event_type, role);
CREATE INDEX IF NOT EXISTS idx_ods_sess_ts ON ods_jsonl_event(session_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_ods_rid ON ods_jsonl_event(request_id);
-- 注意：agent 列的索引不写在这里——旧库的 CREATE TABLE IF NOT EXISTS 是空操作，
-- 表里还没有 agent 列，建索引会报 no such column。统一放到 _migrate() 里处理。
-- 原文归档表（raw_json 从主表拆出，2026-08-30 P0-3 瘦身；content_json 无消费方已删）
CREATE TABLE IF NOT EXISTS ods_jsonl_raw (
    file_path TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    raw_json TEXT,
    PRIMARY KEY (file_path, line_no)
);
"""

EMPTY_SHA = hashlib.sha256(b"").hexdigest()   # 空前缀（cursor=0）的摘要


def get_conn(db_path):
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """旧库补 agent 列（SQLite 无 ADD COLUMN IF NOT EXISTS）。

    只加列不回填：SQLite 的 ADD COLUMN 是元数据操作（毫秒级），而已有的 500 万
    行回填会重写整张表（2GB+ 写放大）。历史行 agent 保持 NULL，视图统一按
    'workbuddy' 解释（COALESCE），语义等价且零成本。
    """
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ods_jsonl_event)")]
        if not cols:
            return                                  # 表还不存在（由 SCHEMA 建好后再调）
        if "agent" not in cols:
            conn.execute("ALTER TABLE ods_jsonl_event ADD COLUMN agent TEXT")
            conn.commit()
            print("[collect] 迁移：ODS 增加 agent 列（历史行按 workbuddy 解释）")
        if "cost_usd" not in cols:
            # T2（ccusage 补充源）：成本 USD 只 ccusage 提供，历史行 NULL，视图 COALESCE
            conn.execute("ALTER TABLE ods_jsonl_event ADD COLUMN cost_usd REAL")
            conn.commit()
            print("[collect] 迁移：ODS 增加 cost_usd 列（ccusage 成本维度）")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ods_agent ON ods_jsonl_event(agent)")
        conn.commit()
    except Exception as e:
        print(f"[collect] agent 列迁移跳过：{e}")


def meta_get(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value, updated_at) VALUES(?,?,?)",
        (key, value, time.strftime("%Y-%m-%d %H:%M:%S")),
    )


def _to_int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _stat_files(sources):
    """枚举各源下的 jsonl 文件 → {path: ((dev,ino,size,mtime_ns,ctime_ns), source)}。

    ns 级时间戳：不截断到秒；ctime_ns 保证任何真实写入都改变状态。
    """
    out = {}
    for src in sources:
        try:
            root = src.root()
        except Exception:
            continue
        if not os.path.isdir(root):
            continue
        pattern = os.path.join(root, src.glob_pattern())
        for p in glob.glob(pattern, recursive=True):
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[p] = ((st.st_dev, st.st_ino, st.st_size,
                       st.st_mtime_ns, st.st_ctime_ns), src)
    return out


def _scan_stream(path, cursor, anchor=None):
    """慢路径：单遍二进制流式扫描（不整文件载入内存）。
    返回 (last_no, byte_cursor, last_raw, h_at_anchor, h_final, new_lines)：
      last_no       最后一个非空行的文件行号（空文件为 0）
      byte_cursor   last_no 行行尾的字节偏移（下一行起点）
      last_raw      last_no 行的完整原始字节（含行尾换行，若有）
      h_at_anchor   非空行 1..anchor 的前缀摘要；anchor 为空 / 文件短于
                    anchor / anchor 行缺失时返回 None（视为不匹配）
      h_final       全部非空行（1..last_no）摘要——消费到底后的新 pdigest
      new_lines     [(line_no, raw_bytes)] 行号 > cursor 的非空行
    摘要口径：每个非空行去掉行尾换行后 + b"\\n" 逐行喂入 SHA-256。"""
    h = hashlib.sha256()
    h_at = None
    last_no = 0
    last_raw = b""
    off = 0
    new_lines = []
    with open(path, "rb") as f:
        for i, raw in enumerate(f, start=1):
            off = f.tell()
            if not raw.strip():
                continue
            h.update(raw.rstrip(b"\r\n") + b"\n")
            last_no = i
            last_raw = raw
            if anchor and i == anchor:
                h_at = h.copy().hexdigest()
            elif i > cursor:
                new_lines.append((i, raw))
    if anchor and h_at is None and last_no >= anchor:
        # anchor 落在空行 / 空白行：补一次精确定位（历史锚点必为非空行，
        # 若当前位置已是空行说明前缀被改写 → 保持 None 触发重采）
        h2 = hashlib.sha256()
        with open(path, "rb") as f:
            for i, raw in enumerate(f, start=1):
                if not raw.strip():
                    continue
                if i > anchor:
                    break
                h2.update(raw.rstrip(b"\r\n") + b"\n")
                if i == anchor:
                    h_at = h2.hexdigest()
                    break
    return last_no, off, last_raw, h_at, h.hexdigest(), new_lines


def _insert_lines(conn, path, agent, src, new_lines):
    """把「文件新增行」交给源解析后入库。"""
    if not new_lines:
        return 0
    ctx = None
    # 只有声明了文件级上下文的源（如 Codex）才做预扫描，避免无谓的整文件读
    if type(src).build_context is not agent_registry.AgentSource.build_context:
        try:
            ctx = src.build_context(path)
        except Exception:
            ctx = None
    try:
        rows = src.parse_lines(path, new_lines, ctx)
    except Exception as e:
        print(f"[collect] {src.key} 解析失败 {os.path.basename(path)}: {e}")
        return 0
    return insert_rows(conn, path, src, rows)


def insert_rows(conn, path, src, rows):
    """把「规范行 dict 列表」落库，返回成功插入条数（幂等）。

    文件源与**库型源**共用这一段：ODS 主表 + 原文归档（用行内 _raw），
    保证两条路径的落库语义完全一致（尤其 project / agent 两个派生列）。
    """
    inserted = 0
    # T3「只记计数不存原文」开关：关掉后跳过原文归档，只保留消费层
    # （ods_jsonl_event 的用量计数），看板全部功能不受影响（raw 表本就无读取方）。
    keep_raw = raw_archive_enabled(conn)
    for r in rows:
        raw_obj = r.pop("_raw", None)
        line_no = r["line_no"]
        cwd = r.get("cwd") or ""
        cur = conn.execute(
            """INSERT OR IGNORE INTO ods_jsonl_event
               (id, file_path, line_no, event_type, role, session_id, ts_ms,
                cwd, model, request_id, raw_usage_json,
                user_prompt, ai_title, project, agent, cost_usd)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.get("id") or "", path, line_no,
                r.get("event_type") or "", r.get("role"),
                r.get("session_id") or "", r.get("ts_ms") or 0,
                cwd, r.get("model") or "", r.get("request_id") or "",
                r.get("raw_usage_json"), r.get("user_prompt"),
                r.get("ai_title"),
                src.project_of(cwd) if cwd else "",
                r.get("agent") or src.key,   # 行级覆盖：一个源可承载多个 agent key（ccusage）
                r.get("cost_usd"),
            ),
        )
        # 原文归档（raw_json 拆表，主表瘦身；content_json 无消费方不存）。
        # 受 raw_archive 开关控制：关闭时只落计数（T3 交付物 ②）。
        if keep_raw:
            conn.execute(
                "INSERT OR IGNORE INTO ods_jsonl_raw(file_path, line_no, raw_json) VALUES(?,?,?)",
                (path, line_no, json.dumps(raw_obj, ensure_ascii=False) if raw_obj is not None else None),
            )
        # 只统计**真正插入**的行（OR IGNORE 命中已有键时 rowcount=0）。
        # 早先无条件 +1，导致重复采集也报"新增 N 条"——日志骗人，排查时极易误判。
        inserted += max(0, cur.rowcount)
    return inserted


# ---------------- 数仓维护（P0：原文归档保留策略 + VACUUM）----------------
# 背景：ods_jsonl_raw 只写不读（看板/桌宠口径全部建在 ods_jsonl_event 上），
# 但 WorkBuddy 每行写完整消息正文（均值 ~5.7KB），33 天就堆到 1.7GB 且只增不减。
# 策略：raw 按事件时间做 TTL（原文回溯窗口），消费层**全量保留**；
# auto_vacuum=0 的库删除后必须 VACUUM 才真正缩文件。

RAW_TTL_DAYS_DEFAULT = 14           # 原文回溯窗口（天）。实测数据跨度 33 天时，
                                    # 14 天窗口即可回收 ~1.7GB；口径重放如需更长
                                    # 历史，调大此值（消费层不受影响）
RAW_MAINTAIN_BATCH = 20000          # 分批删除：单事务过大 → WAL 暴涨 + 长锁
RAW_MAINTAIN_ROW_CAP = 2_000_000    # 单次运行删除上限（异常数据兜底）


def raw_archive_enabled(conn):
    """「只记计数不存原文」开关（T3）。读 meta；缺省开启。
    关闭后 insert_rows 跳过 ods_jsonl_raw（原文归档），只保留消费层计数。"""
    return meta_get(conn, "raw_archive") != "0"


def raw_retention(conn, ttl_days=RAW_TTL_DAYS_DEFAULT, batch=RAW_MAINTAIN_BATCH):
    """按事件时间对 ods_jsonl_raw 做 TTL 清理。

    - raw 表没有时间列（PK = file_path+line_no），年龄经主表 ts_ms 联结判定
      （走 idx_ods_ts）；
    - ts_ms=0 的行视为无有效时间，不删（防时间异常的新行被误伤）；
    - 分批删除 + 每批提交（RETURNING LENGTH(raw_json) 精确累计释放字节）；
    - 返回 (deleted_rows, freed_bytes, orphan_rows)。孤儿（raw 无对应 event）
      按构造应为 0，仅上报不处理。
    """
    cutoff = int((time.time() - ttl_days * 86400) * 1000)
    deleted = 0
    freed = 0
    while deleted < RAW_MAINTAIN_ROW_CAP:
        cur = conn.execute(
            """DELETE FROM ods_jsonl_raw WHERE rowid IN (
                 SELECT r.rowid FROM ods_jsonl_raw r
                 JOIN ods_jsonl_event e
                   ON e.file_path = r.file_path AND e.line_no = r.line_no
                 WHERE e.ts_ms > 0 AND e.ts_ms < ?
                 LIMIT ?)
               RETURNING LENGTH(raw_json)""",
            (cutoff, batch),
        )
        rows = cur.fetchall()
        if not rows:
            break
        deleted += len(rows)
        freed += sum(r[0] or 0 for r in rows)
        conn.commit()
    orphans = conn.execute(
        """SELECT COUNT(*) FROM ods_jsonl_raw r WHERE NOT EXISTS (
             SELECT 1 FROM ods_jsonl_event e
             WHERE e.file_path = r.file_path AND e.line_no = r.line_no)"""
    ).fetchone()[0]
    return deleted, freed, orphans


def vacuum_db(conn):
    """回收已删除页（auto_vacuum=0 库的 DELETE 不缩文件，必须 VACUUM）。
    需无其他连接持有写事务；WAL 读连接一般不阻塞，失败时由调用方重试。
    VACUUM 的产出先进 WAL，追加 TRUNCATE checkpoint 让主文件体积立即落地
    （否则要等最后一个连接关闭）。"""
    conn.commit()
    conn.execute("VACUUM")
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


def _meta_update(conn, path, cursor, bcursor, last_raw, state, seq, pdigest):
    """统一写 meta（增量/重采/播种共用）。last_raw 可能为 None（空文件）。"""
    meta_set(conn, f"cursor:{path}", str(cursor))
    meta_set(conn, f"bcursor:{path}", str(bcursor))
    meta_set(conn, f"llhash:{path}",
             hashlib.sha256(last_raw).hexdigest() if last_raw else EMPTY_SHA)
    meta_set(conn, f"lllen:{path}", str(len(last_raw) if last_raw else 0))
    meta_set(conn, f"fstate:{path}", state)
    meta_set(conn, f"seq:{path}", str(seq))
    meta_set(conn, f"pdigest:{path}", pdigest)


def collect_once(db_path=None, sources=None, full=False, verbose=True):
    """增量采集一次。返回 (新事件数, 扫描文件数)。异常路径同样释放连接。
    整个批次在同一事务中提交（含覆盖重采的 DELETE + 重 INSERT）。"""
    db_path = db_path or DEFAULT_DB
    sources = list(sources) if sources else agent_registry.available_sources()
    conn = get_conn(db_path)
    try:
        # 库型源（覆盖了 collect 的）先自行采集；其余走文件游标机制
        inserted = 0
        scanned = 0
        file_sources = []
        for src in sources:
            if type(src).collect is not agent_registry.AgentSource.collect:
                try:
                    n = src.collect(conn, db_path, insert_rows)
                    inserted += int(n or 0)
                    scanned += 1
                    conn.commit()
                except Exception as e:
                    print(f"[collect] {src.key} 库型采集失败：{e}")
            else:
                file_sources.append(src)

        files = _stat_files(file_sources)

        for path in sorted(files):
            (dev, ino, size, mtime_ns, ctime_ns), src = files[path]
            state = f"{dev}|{ino}|{size}|{mtime_ns}|{ctime_ns}"
            prev_state = meta_get(conn, f"fstate:{path}")
            # 快跳过：五元组（ns 级 + ctime）完全未变 → 文件无任何改动，0 字节 IO
            # （性能关键：1s 监听下绝大多数轮次走这里）
            if not full and prev_state == state:
                continue
            cur_key = f"cursor:{path}"
            cursor = _to_int(meta_get(conn, cur_key))
            pd_raw = meta_get(conn, f"pdigest:{path}")
            # pdigest 解析："K|sha"（新格式）或 "sha"（旧库格式，锚点=当前游标）；
            # 缺失（迁移）时锚点=当前游标，供播种校验 1..cursor 是否仍完整
            anchor, prev_digest = None, None
            if pd_raw:
                if "|" in pd_raw:
                    a, s = pd_raw.split("|", 1)
                    anchor, prev_digest = _to_int(a), s
                else:
                    anchor, prev_digest = cursor, pd_raw
            else:
                anchor = cursor
            bcursor = _to_int(meta_get(conn, f"bcursor:{path}"))
            llhash = meta_get(conn, f"llhash:{path}")
            lllen = _to_int(meta_get(conn, f"lllen:{path}"))
            seq = _to_int(meta_get(conn, f"seq:{path}"))

            reset = bool(full)
            ps = None
            if prev_state:
                ps = prev_state.split("|")
            # 替换 / 截短：纯 stat 判定，不读旧内容
            if not reset and ps and len(ps) >= 5:
                if ps[0] != str(dev) or ps[1] != str(ino):
                    reset = True                      # 文件被替换（新 inode/dev）
                elif size < _to_int(ps[2]):
                    reset = True                      # 截短（size 变小）
            # 快路径（append）：inode 未变、size 变大、增量元数据齐全、
            # 未到深校验轮 → 只验证最后已消费行 + 读新增行，不扫完整历史
            if (not reset and not full and ps and len(ps) >= 5
                    and int(ps[0]) == dev and int(ps[1]) == ino
                    and size > _to_int(ps[2])
                    and pd_raw and bcursor is not None
                    and llhash is not None and lllen is not None
                    and seq < DEEP_CHECK_EVERY):
                # —— 快路径 ——
                scanned += 1
                # 验证最后已消费行未被改写（更长覆盖时该行内容必变）
                match = False
                try:
                    with open(path, "rb") as f:
                        if bcursor - lllen >= 0:
                            f.seek(bcursor - lllen)
                            if hashlib.sha256(f.read(lllen)).hexdigest() == llhash:
                                match = True
                except OSError:
                    match = False
                if not match:
                    # 已消费内容被改写 → 同事务删除旧记录 + 全量重采
                    conn.execute("DELETE FROM ods_jsonl_event WHERE file_path=?", (path,))
                    conn.execute("DELETE FROM ods_jsonl_raw WHERE file_path=?", (path,))
                    cursor = 0
                    last_no, bc, last_raw, _h, h_final, new_lines = _scan_stream(path, 0)
                    inserted += _insert_lines(conn, path, src.key, src, new_lines)
                    _meta_update(conn, path, last_no, bc, last_raw, state, 0,
                                 f"{last_no}|{h_final}")
                    continue
                # 匹配 → 只读新增行（从 bcursor 处 seek，物理行号续 cursor）
                new_lines = []
                last_no = cursor
                last_raw = None
                off = bcursor
                try:
                    with open(path, "rb") as f:
                        f.seek(bcursor)
                        line_no = cursor
                        for raw in f:
                            line_no += 1
                            off = f.tell()
                            if not raw.strip():
                                continue
                            new_lines.append((line_no, raw))
                            last_no = line_no
                            last_raw = raw
                except OSError:
                    pass
                inserted += _insert_lines(conn, path, src.key, src, new_lines)
                # 快路径不更新 pdigest（锚点保持），仅推进游标/指纹/计数
                _meta_update(conn, path, last_no, off, last_raw, state,
                             seq + 1, pd_raw)
                continue

            # —— 慢路径（或 reset / 迁移播种）——
            last_no, bc, last_raw, h_at_anchor, h_final, new_lines = \
                _scan_stream(path, cursor, anchor)
            if not reset:
                if prev_digest is None:
                    # 旧版本库迁移（无 pdigest）：播种当前摘要，信任既有数据
                    # 不重采；游标之后的新行照常增量。文件短于游标 → 重采。
                    if not (cursor > 0 and h_at_anchor is not None):
                        reset = True
                elif h_at_anchor is None or h_at_anchor != prev_digest:
                    # 已消费前缀被改写（同长度覆盖 / 中间行改写 / 更长覆盖
                    # 的深校验轮 / 截短至游标之上）→ 全量重采
                    reset = True

            if reset:
                conn.execute("DELETE FROM ods_jsonl_event WHERE file_path=?", (path,))
                conn.execute("DELETE FROM ods_jsonl_raw WHERE file_path=?", (path,))
                cursor = 0
                last_no, bc, last_raw, _h, h_final, new_lines = _scan_stream(path, 0)

            inserted += _insert_lines(conn, path, src.key, src, new_lines)
            scanned += 1
            _meta_update(conn, path, last_no, bc, last_raw, state, 0,
                         f"{last_no}|{h_final}")
            if prev_digest is None and not reset:
                conn.execute("DELETE FROM meta WHERE key=?", (f"bhash:{path}",))
        meta_set(conn, "last_collect", time.strftime("%Y-%m-%d %H:%M:%S"))
        conn.commit()
        # 物化 v_turn_total → dws_turn（2026-09-26 性能修复）。
        # 采集循环几秒一轮，全量物化一次约 4s，所以：**只在真有新数据时**才刷新，
        # 且 refresh_dws_turn 内部还有 30s 节流。读取方（桌宠/看板）只读这张表，
        # 从 1.6~3.7 秒降到 ~0ms。
        if inserted:
            try:
                from wb_dw import refresh_dws_turn
                refresh_dws_turn(conn=conn, verbose=verbose)
            except Exception as _e:
                # 物化失败不能影响采集本身（注意：本模块没有 log_exception，别调用它）
                if verbose:
                    sys.stderr.write(f"[collect] dws_turn 物化失败：{_e}\n")
    finally:
        conn.close()
    if verbose:
        print(f"[collect] 扫描 {scanned} 文件，新增 {inserted} 条用量事件")
    return inserted, scanned


def watch_changes(db_path, sources=None, interval=5, verbose=True, on_change=None):
    """守护：每 interval 秒比对文件 stat 五元组（dev/inode/size/mtime_ns/ctime_ns），
    有变化即增量采集（覆盖/截断判定在 collect_once 内完成）。
    on_change(db_path, inserted) 可选回调：采集到新数据后调用（VIEW 方案无需物化重建）。"""
    sources = list(sources) if sources else agent_registry.available_sources()
    conn = get_conn(db_path)
    known = {}
    for key in (k[0] for k in conn.execute(
            "SELECT key FROM meta WHERE key LIKE 'fstate:%'").fetchall()):
        v = meta_get(conn, key) or ""
        parts = v.split("|")
        if len(parts) == 5:
            path = key[7:]
            try:
                known[path] = tuple(int(x) for x in parts)
            except ValueError:
                pass
    conn.close()
    if verbose:
        print(f"[daemon] 监听 {', '.join(s.root() for s in sources)}，"
              f"每 {interval}s 检查变更（Ctrl+C 退出）")
    while True:
        time.sleep(interval)
        try:
            cur = {p: st for p, (st, _src) in _stat_files(sources).items()}
            changed = any(known.get(p) != st for p, st in cur.items())
            if changed:
                inserted, _ = collect_once(db_path, sources, verbose=verbose)
                if verbose and inserted:
                    print(f"[daemon] 检测到变更，新增 {inserted} 条")
                if inserted and on_change:
                    try:
                        on_change(db_path, inserted)
                    except Exception as e:
                        print(f"[daemon] on_change 失败（下轮重试）: {e}")
                known = {p: st for p, st in cur.items()}
        except Exception as e:
            # 自愈：采集异常不杀死监听线程，下轮继续
            print(f"[daemon] 采集异常（自动继续）: {e}")


def main():
    ap = argparse.ArgumentParser(description="多 agent 用量日志增量采集")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--full", action="store_true", help="全量重采")
    ap.add_argument("--daemon", action="store_true", help="守护监听模式")
    ap.add_argument("--interval", type=int, default=5, help="监听间隔秒数")
    ap.add_argument("--agent", default=None,
                    help=f"只采某个源（可选：{', '.join(agent_registry.source_keys())}）")
    ap.add_argument("--list-agents", action="store_true", help="列出全部源及可用性")
    args = ap.parse_args()

    if args.list_agents:
        for s in agent_registry.all_sources():
            mark = "可用" if s.available() else "未发现"
            print(f"  {s.key:12s} {s.label:14s} [{mark}] {s.root()}")
        return 0

    if args.agent:
        src = agent_registry.get_source(args.agent)
        if src is None:
            print(f"未知数据源 {args.agent}（可选：{', '.join(agent_registry.source_keys())}）")
            return 2
        sources = [src]
    else:
        sources = None

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    if args.daemon:
        watch_changes(args.db, sources, args.interval)
    else:
        collect_once(args.db, sources, full=args.full)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
