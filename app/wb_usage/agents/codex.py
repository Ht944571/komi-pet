# -*- coding: utf-8 -*-
"""
agents.codex — Codex CLI / Codex Desktop 数据源
================================================

Codex 把会话写进 `~/.codex/sessions/<年>/<月>/<日>/rollout-<ts>-<uuid>.jsonl`，
每行一个 JSON：`{timestamp, type, payload}`。

能拿到的用量（实测自本机 4 个 rollout 文件）
-------------------------------------------
`event_msg` / `token_count` 事件带完整的 token 计数：

    payload.info.last_token_usage  = 本次模型调用的增量
    payload.info.total_token_usage = 该会话累计

**实测校验**：`sum(last_token_usage.total_tokens)` 与最后一次
`total_token_usage.total_tokens` 完全相等（4480420 / 102901 / 6759351 三例全对），
所以按「一个 token_count 事件 = 一次调用」入库、由视图求和，口径正确。

映射到规范形状（见 agents/base.py）：

    prompt_tokens              ← input_tokens
    completion_tokens          ← output_tokens
    total_tokens               ← total_tokens
    prompt_cache_hit_tokens    ← cached_input_tokens
    prompt_cache_write_tokens  ← cache_write_input_tokens
    completion_thinking_tokens ← reasoning_output_tokens
    credit                     ← 无（Codex 按 token 计费，没有积分概念）

上下文处理
----------
Codex 的 token_count 行**不自带** session_id / cwd / model / turn_id——这些出现在
文件头的 session_meta、world_state、turn_context 里。断点续采时新行的上文可能
早已消费过，所以这里实现 `build_context()`：解析前先把整文件扫一遍建立上下文
（含 line_no → turn_id 映射），再解析新增行。
rollout 文件很小（本机合计 1.5MB），这个代价可以接受。

只入库两类行（token_count / user_message），不搬全部原始事件——避免重演
WorkBuddy 侧「ODS 只增不减、库涨到 GB 级」的老问题。
"""

import json
import os

from .base import AgentSource, canonical_usage, parse_ts_ms, usage_json


class CodexSource(AgentSource):

    key = "codex"
    label = "Codex CLI"
    has_credit = False          # 无积分概念，前端只显示 token
    order = 20

    def root(self):
        return os.path.join(os.path.expanduser("~"), ".codex", "sessions")

    def glob_pattern(self):
        return os.path.join("**", "*.jsonl")     # sessions/<年>/<月>/<日>/*.jsonl

    # ------------------------------------------------------------------
    # 文件级上下文
    # ------------------------------------------------------------------

    def build_context(self, path):
        ctx = {"session_id": "", "cwd": "", "model": "", "turn_by_line": {}}
        turn_id = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(d, dict):
                        continue
                    t = d.get("type")
                    pl = d.get("payload") or {}

                    if t == "session_meta":
                        ctx["session_id"] = pl.get("session_id") or pl.get("id") or ""
                        ctx["cwd"] = pl.get("cwd") or ""
                    elif t == "turn_context":
                        turn_id = pl.get("turn_id") or turn_id
                        if pl.get("model"):
                            ctx["model"] = pl["model"]
                        if pl.get("cwd") and not ctx["cwd"]:
                            ctx["cwd"] = pl["cwd"]
                    elif t == "world_state":
                        cm = (pl.get("state") or {}).get("collaboration_mode") or {}
                        if cm.get("model"):
                            ctx["model"] = cm["model"]
                    elif t == "event_msg":
                        pt = pl.get("type")
                        if pt == "task_started" and pl.get("turn_id"):
                            turn_id = pl["turn_id"]
                        elif pt == "thread_settings_applied":
                            ts = pl.get("thread_settings") or {}
                            if ts.get("model"):
                                ctx["model"] = ts["model"]
                    ctx["turn_by_line"][line_no] = turn_id
        except OSError:
            pass
        return ctx

    # ------------------------------------------------------------------
    # 行解析
    # ------------------------------------------------------------------

    def parse_lines(self, path, new_lines, ctx=None):
        ctx = ctx or {}
        session_id = ctx.get("session_id", "")
        cwd = ctx.get("cwd", "")
        model = ctx.get("model", "")
        turn_by_line = ctx.get("turn_by_line") or {}

        rows = []
        for line_no, raw in new_lines:
            d = self._load(raw)
            if d is None:
                continue
            if d.get("type") != "event_msg":
                continue                                  # 其余事件不入库（控体积）
            pl = d.get("payload") or {}
            pt = pl.get("type")
            ts_ms = parse_ts_ms(d.get("timestamp"))
            turn_id = pl.get("turn_id") or turn_by_line.get(line_no, "")

            if pt == "token_count":
                info = pl.get("info") or {}
                lu = info.get("last_token_usage") or {}    # 本次调用增量（已实测）
                if not lu:
                    continue
                usage = canonical_usage(
                    prompt_tokens=lu.get("input_tokens"),
                    completion_tokens=lu.get("output_tokens"),
                    total_tokens=lu.get("total_tokens"),
                    prompt_cache_hit_tokens=lu.get("cached_input_tokens"),
                    prompt_cache_write_tokens=lu.get("cache_write_input_tokens"),
                    completion_thinking_tokens=lu.get("reasoning_output_tokens"),
                )
                if not usage.get("prompt_tokens"):
                    continue                               # 视图按 prompt_tokens>0 过滤
                rows.append(self.base_row(
                    line_no, id=f"codex:{session_id}:{line_no}",
                    event_type="usage", role="assistant",
                    session_id=session_id, ts_ms=ts_ms, cwd=cwd, model=model,
                    request_id=turn_id or session_id,
                    raw_usage_json=usage_json(usage),
                    _raw=d,
                ))
            elif pt == "user_message":
                msg = pl.get("message") or ""
                if not msg:
                    continue
                rows.append(self.base_row(
                    line_no, id=f"codex:{session_id}:{line_no}",
                    event_type="message", role="user",
                    session_id=session_id, ts_ms=ts_ms, cwd=cwd, model=model,
                    request_id=turn_id or session_id,
                    user_prompt=msg,
                    _raw=d,
                ))
        return rows

    # ------------------------------------------------------------------
    # 项目名
    # ------------------------------------------------------------------

    def project_of(self, cwd):
        """Codex Desktop 的会话目录形如 …\\Codex\\<日期>\\<工作区名|sk-密钥>。

        密钥目录名对用户没有意义，回退到上一层（日期）。
        """
        base = super().project_of(cwd)
        if base.startswith("sk-"):
            parent = os.path.basename(os.path.dirname(str(cwd).replace("\\", "/").rstrip("/")))
            return parent or base
        return base
