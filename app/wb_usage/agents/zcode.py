# -*- coding: utf-8 -*-
"""
agents.zcode — ZCode 数据源（SQLite 库型）
================================================================

与 WorkBuddy / Codex 不同，ZCode 的用量**不在 jsonl 里**，而在 SQLite：
`~/.zcode/cli/db/db.sqlite`。所以本模块**覆盖 `AgentSource.collect()`**，
走"库型数据源"通道（采集器检测到覆盖后跳过文件游标机制，由本模块自行读库）。

这条通道是这次为"收编 ZCode 桌宠"专门加的：原先的按行契约（文件 + 行号 + 游标）
硬套一个关系库只会把接口拧歪。

用到的表（实测本机 schema）
---------------------------
  model_usage  每次模型调用一行：session_id / turn_id / model_id / started_at(ms 文本)
               input_tokens / output_tokens / reasoning_tokens /
               cache_creation_input_tokens / cache_read_input_tokens / computed_total_tokens
  session      会话：id / directory / title / time_updated
  message      data 是 JSON，role 与 semantics.kind='user_prompt' 用来筛真用户输入
  part         data 是 JSON，type='text' 时 text 才是提问正文（提问正文在 part 里，不在 message）

**只读**：以 mode=ro 打开 ZCode 的库。写它会造成 ZCode 数据损坏。

映射到规范用量形状（见 agents/base.py）——ZCode 无积分概念，has_credit=False：

    prompt_tokens              ← input_tokens
    completion_tokens          ← output_tokens
    total_tokens               ← computed_total_tokens（缺失回退 provider_total_tokens）
    prompt_cache_hit_tokens    ← cache_read_input_tokens
    prompt_cache_write_tokens  ← cache_creation_input_tokens
    completion_thinking_tokens ← reasoning_tokens

幂等：`line_no` 用**由源记录 id 派生的稳定 52 位哈希**，而不是递增序号——
采集器靠 (file_path, line_no) 主键去重，递增序号每次重采都会错位产生重复行。
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


class ZCodeSource(AgentSource):

    key = "zcode"
    label = "ZCode"
    has_credit = False          # ZCode 无积分口径（同 Codex，按 Token 计费）
    order = 15

    # ---- 位置 ----

    def db_path(self):
        return os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite")

    def root(self):
        # 库型源没有"数据目录"概念，这里返回上两级目录供 --list-agents 展示
        return os.path.dirname(os.path.dirname(self.db_path()))

    def available(self):
        return os.path.isfile(self.db_path())

    def glob_pattern(self):
        return ""               # 库型源不走文件枚举

    # ---- 采集（覆盖 collect → 走库型通道）----

    def collect(self, conn, db_path, insert_rows):
        zp = self.db_path()
        if not os.path.isfile(zp):
            return 0
        try:
            zc = sqlite3.connect(f"file:{zp.replace(os.sep, '/')}?mode=ro",
                                 uri=True, timeout=5)
            zc.row_factory = sqlite3.Row
        except Exception as e:
            print(f"[zcode] 打不开 ZCode 库（跳过）：{e}")
            return 0

        inserted = 0
        try:
            # 会话表：提供项目目录与标题
            sess = {}
            for r in zc.execute("SELECT id, directory, title, time_updated FROM session"):
                sess[r["id"]] = {"dir": r["directory"] or "",
                                 "title": r["title"] or "",
                                 "updated": _to_int(r["time_updated"])}

            # ① 每次模型调用 → 一行规范用量
            usage_path = f"{zp}::model_usage"
            rows = []
            for r in zc.execute(
                    "SELECT id, session_id, turn_id, model_id, started_at, status,"
                    "       input_tokens, output_tokens, reasoning_tokens,"
                    "       cache_creation_input_tokens, cache_read_input_tokens,"
                    "       computed_total_tokens, provider_total_tokens"
                    "  FROM model_usage"):
                inp = _to_int(r["input_tokens"])
                if inp <= 0:
                    continue                       # 视图按 prompt_tokens>0 过滤
                total = _to_int(r["computed_total_tokens"]) or _to_int(r["provider_total_tokens"])
                usage = canonical_usage(
                    prompt_tokens=inp,
                    completion_tokens=_to_int(r["output_tokens"]),
                    total_tokens=total,
                    prompt_cache_hit_tokens=_to_int(r["cache_read_input_tokens"]),
                    prompt_cache_write_tokens=_to_int(r["cache_creation_input_tokens"]),
                    completion_thinking_tokens=_to_int(r["reasoning_tokens"]),
                )
                sid = r["session_id"] or ""
                rows.append(self.base_row(
                    _stable_no(r["id"]),
                    id=f"zcode:{r['id']}",
                    event_type="usage", role="assistant",
                    session_id=sid,
                    ts_ms=_to_int(r["started_at"]),
                    cwd=(sess.get(sid) or {}).get("dir", ""),
                    model=r["model_id"] or "",
                    request_id=r["turn_id"] or sid,
                    raw_usage_json=usage_json(usage),
                    _raw={k: r[k] for k in r.keys()},
                ))
            inserted += insert_rows(conn, usage_path, self, rows)

            # ② 用户提问 → 一行 message（正文在 part 表里，message 只有语义标记）
            msg_rows = []
            user_ids = set()
            for r in zc.execute("SELECT id, session_id, time_created, data FROM message"):
                try:
                    d = json.loads(r["data"] or "{}")
                except Exception:
                    continue
                sem = d.get("semantics") or {}
                if d.get("role") != "user" or sem.get("kind") != "user_prompt":
                    continue                        # 过滤掉系统注入 / 工具回填
                user_ids.add(r["id"])
                sid = r["session_id"] or ""
                # 取该 message 下 type=text 的 part，拼成正文
                txt = []
                for p in zc.execute(
                        "SELECT data FROM part WHERE message_id=? ORDER BY sequence",
                        (r["id"],)):
                    try:
                        pd = json.loads(p["data"] or "{}")
                    except Exception:
                        continue
                    if pd.get("type") == "text" and pd.get("text"):
                        txt.append(pd["text"])
                if not txt:
                    continue
                msg_rows.append(self.base_row(
                    _stable_no(r["id"]),
                    id=f"zcode-msg:{r['id']}",
                    event_type="message", role="user",
                    session_id=sid,
                    ts_ms=_to_int(r["time_created"]),
                    cwd=(sess.get(sid) or {}).get("dir", ""),
                    user_prompt="\n".join(txt),
                    request_id="",
                    _raw={"id": r["id"], "session_id": sid, "role": "user",
                          "text": "\n".join(txt)},
                ))
            inserted += insert_rows(conn, f"{zp}::message", self, msg_rows)

            # ③ 会话标题 → ai-title 事件（看板靠它显示会话名）
            title_rows = []
            for sid, s in sess.items():
                if not s["title"]:
                    continue
                title_rows.append(self.base_row(
                    _stable_no(f"title:{sid}"),
                    id=f"zcode-title:{sid}",
                    event_type="ai-title", role=None,
                    session_id=sid, ts_ms=s["updated"], cwd=s["dir"],
                    ai_title=s["title"],
                    _raw={"session_id": sid, "title": s["title"]},
                ))
            inserted += insert_rows(conn, f"{zp}::title", self, title_rows)
        finally:
            try:
                zc.close()
            except Exception:
                pass
        return inserted
