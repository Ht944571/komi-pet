# -*- coding: utf-8 -*-
"""
agents.opencode — opencode 数据源（SQLite 库型）
================================================================================

opencode（sst/opencode）的用量**不在 jsonl 里**，而在一个 SQLite 库：
`~/.local/share/opencode/opencode.db`（实测本机 19MB、活跃写入中）。
所以本模块**覆盖 `AgentSource.collect()`**，走"库型数据源"通道
（采集器检测到覆盖后跳过文件游标机制，由本模块自行读库）——与 `agents/zcode.py` 同一条路。

用到的表（实测本机 schema，2026-09-30）
--------------------------------------
  session_v2      会话：id / project_id / title / directory / model / agent /
                  tokens_* / cost / time_created / time_updated
  session_message 会话内消息：id / session_id / type / seq / time_created / data(JSON)
                  · type='assistant' → data.tokens 就是**每次模型调用的用量**
                  · type='user'      → data.text 是用户提问正文
                  · type='idle'      → 一轮结束的标记（用它切"轮次"）
  project         id / worktree —— 工作目录（= cwd，项目名从它派生）

**只读**：以 mode=ro 打开 opencode 的库。写它会造成 opencode 数据损坏
（而且它随时在写，只能读快照）。

映射到规范用量形状（见 agents/base.py）——opencode 无积分概念，has_credit=False：

    prompt_tokens              ← tokens.input
    completion_tokens          ← tokens.output
    total_tokens               ← input + output + reasoning
    prompt_cache_hit_tokens    ← tokens.cache.read
    prompt_cache_write_tokens  ← tokens.cache.write
    completion_thinking_tokens ← tokens.reasoning

轮次口径（与 workbuddy / zcode 一致）：**一次用户提问 + 它之后的若干次调用 = 一轮**。
opencode 的消息流是 user → assistant×N → idle，所以按 (session_id, seq) 顺序扫，
把 assistant 归属到**最近一条 user 消息**的 id 上（找不到 user 就用 session_id 兜底）。
不这么做的话 417 条 assistant 会各算一轮，看板上的"轮次"会虚高十几倍。

幂等：`line_no` 用**由源记录 id 派生的稳定 52 位哈希**（同 zcode）——采集器靠
(file_path, line_no) 主键去重，用递增序号每次重采都会错位产生重复行。
"""

import hashlib
import json
import os
import sqlite3

from .base import AgentSource, canonical_usage, usage_json


def _stable_no(text):
    """源记录 id → 稳定的 52 位整数行号（幂等去重的前提）。"""
    h = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:13]
    return int(h, 16)


def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class OpenCodeSource(AgentSource):

    key = "opencode"
    label = "opencode"
    has_credit = False          # 无积分概念（按 token 计费）
    order = 70                  # 与 assets/_agents.json 里的 order 对齐

    # ---------- 位置 ----------

    def db_path(self):
        return os.path.join(os.path.expanduser("~"), ".local", "share",
                            "opencode", "opencode.db")

    def root(self):
        # 库型源没有"数据目录"概念，返回上一级供 --list-agents 展示
        return os.path.dirname(self.db_path())

    def available(self):
        return os.path.isfile(self.db_path())

    def glob_pattern(self):
        return ""               # 库型源不走文件枚举

    def change_hint(self):
        """变化指纹：两张表的 MAX(rowid)（O(1)，毫秒级）。

        opencode 没有 jsonl 文件可 stat，`watch_changes` 只能靠这个指纹感知
        "它又跑了一轮"，否则会被别的源的活动与否牵着走（ZCode 饿死事故的老问题）。
        """
        zp = self.db_path()
        if not os.path.isfile(zp):
            return None
        try:
            zc = sqlite3.connect(f"file:{zp.replace(os.sep, '/')}?mode=ro",
                                 uri=True, timeout=3)
            try:
                a = zc.execute("SELECT COALESCE(MAX(rowid),0) FROM session_message").fetchone()[0]
                b = zc.execute("SELECT COALESCE(MAX(rowid),0) FROM session_v2").fetchone()[0]
                return f"{a}:{b}"
            finally:
                zc.close()
        except Exception:
            return None

    # ---------- 采集 ----------

    def _open(self):
        zp = self.db_path()
        if not os.path.isfile(zp):
            return None, zp
        try:
            # ⚠️ 必须 mode=ro：opencode 正在用它，写会损坏它的库
            zc = sqlite3.connect(f"file:{zp.replace(os.sep, '/')}?mode=ro",
                                 uri=True, timeout=5)
            zc.row_factory = sqlite3.Row
            return zc, zp
        except Exception as e:
            print(f"[opencode] 打不开库（跳过）：{e}")
            return None, zp

    def collect(self, conn, db_path, insert_rows):
        zc, zp = self._open()
        if zc is None:
            return 0
        try:
            # 会话 → 工作目录（project.worktree）/ 标题 / 模型
            proj = {r["id"]: (r["worktree"] or "") for r in
                    zc.execute("SELECT id, worktree FROM project")}
            sess = {}
            for r in zc.execute("""SELECT id, project_id, title, model, directory
                                     FROM session_v2"""):
                sess[r["id"]] = {
                    "cwd": proj.get(r["project_id"]) or r["directory"] or "",
                    "title": r["title"] or "",
                    "model": _model_name(r["model"]),
                }

            # ⚠️ 必须按 (session_id, seq) 顺序：轮次归属靠"最近一条 user 消息"
            msgs = list(zc.execute("""SELECT id, session_id, type, seq, time_created, data
                                        FROM session_message
                                       ORDER BY session_id, seq"""))
        finally:
            zc.close()

        usage_path = f"{zp}::session_message"
        rows = []
        cur_user = {}                       # session_id → 最近一条 user 消息 id
        for r in msgs:
            sid = r["session_id"] or ""
            mtype = r["type"] or ""
            try:
                d = json.loads(r["data"] or "{}")
            except Exception:
                d = {}
            s = sess.get(sid) or {}

            if mtype == "user":
                cur_user[sid] = r["id"]
                txt = (d.get("text")
                       or (d.get("metadata") or {}).get("displayText") or "")
                rows.append(self.base_row(
                    _stable_no("u:" + r["id"]),
                    id=f"opencode:{r['id']}",
                    event_type="message", role="user",
                    session_id=sid,
                    ts_ms=_to_int(d.get("time", {}).get("created")) or (r["time_created"] or 0),
                    cwd=s["cwd"],
                    model=s["model"],
                    request_id=r["id"],
                    user_prompt=(txt or None),
                    ai_title=s["title"],
                ))
                continue

            if mtype != "assistant":
                continue                    # idle / system / synthetic 不产出行

            tok = d.get("tokens") or {}
            cache = tok.get("cache") or {}
            inp = _to_int(tok.get("input"))
            out = _to_int(tok.get("output"))
            if inp <= 0 and out <= 0:
                continue                    # 视图按 prompt_tokens>0 过滤，空行不入库
            reason = _to_int(tok.get("reasoning"))
            usage = canonical_usage(
                prompt_tokens=inp,
                completion_tokens=out,
                total_tokens=inp + out + reason,
                prompt_cache_hit_tokens=_to_int(cache.get("read")),
                prompt_cache_write_tokens=_to_int(cache.get("write")),
                completion_thinking_tokens=reason,
            )
            rows.append(self.base_row(
                _stable_no("a:" + r["id"]),
                id=f"opencode:{r['id']}",
                event_type="usage", role="assistant",
                session_id=sid,
                ts_ms=_to_int(d.get("time", {}).get("created")) or (r["time_created"] or 0),
                cwd=s["cwd"],
                model=_model_name(d.get("model")) or s["model"],
                # 轮次 = 最近一条用户提问（同 zcode 的 turn_id 语义）
                request_id=cur_user.get(sid) or sid,
                raw_usage_json=usage_json(usage),
                ai_title=s["title"],
                cost_usd=(d.get("cost") or None),
            ))

        n = insert_rows(conn, usage_path, self, rows)
        return n


def _model_name(raw):
    """session_v2.model 是 JSON 文本（或 dict）；assistant.data.model 是 dict。

    统一成 `provider/model` 形式的展示名（providerID 缺失就只给 id）。
    """
    if not raw:
        return ""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return raw
    if not isinstance(raw, dict):
        return ""
    mid = raw.get("id") or raw.get("modelID") or ""
    prov = raw.get("providerID") or raw.get("providerId") or ""
    return f"{prov}/{mid}" if (prov and mid) else (mid or "")
