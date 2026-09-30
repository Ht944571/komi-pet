# -*- coding: utf-8 -*-
"""
agents.claude_code — Claude Code 数据源（jsonl 文件型）
========================================================

Claude Code 把每个会话写成一个 jsonl：
`~/.claude/projects/<cwd 转义>/<session-uuid>.jsonl`。每行自包含
（sessionId / cwd / timestamp / message.usage 都在行内），**不需要**文件级
上下文（对比 codex.py 的 build_context）。

行格式（公开且稳定，全生态 Claude 系分支的共同祖先格式）
---------------------------------------------------------
  assistant 行: {type:"assistant", message:{id, model, role, usage:{
                   input_tokens, output_tokens,
                   cache_creation_input_tokens, cache_read_input_tokens}},
                 sessionId, cwd, requestId, timestamp(ISO8601 Z), uuid,
                 isApiErrorMessage?}
  user 行:      {type:"user", message:{role:"user", content:str|[{type:"text"|"tool_result",...}]},
                 sessionId, cwd, timestamp, isMeta?}
  summary 行:   {type:"summary", summary:"会话标题", leafUuid}   ← 无 timestamp/sessionId

映射到规范形状（见 agents/base.py）：

    prompt_tokens              ← input + cache_read + cache_creation（**总输入**）
    prompt_cache_hit_tokens    ← cache_read_input_tokens
    prompt_cache_write_tokens  ← cache_creation_input_tokens
    completion_tokens          ← output_tokens
    total_tokens               ← 总输入 + output

⚠️ 口径差异（有意为之，勿"统一"）：workbuddy/codex/zcode 的 prompt_tokens 是
**非缓存输入**；Claude 的非缓存输入在全缓存命中时是 **0**，而 0 会被视图的
`prompt_tokens>0` 过滤丢行（真实调用！）——所以本源用 Anthropic 官方口径的
"总输入"做 prompt_tokens，且必然 >0。看板做跨 agent 对比时知悉。

只入库三类行（assistant 用量 / 真用户提问 / summary 标题），不搬其余原始事件
（工具结果、系统注入一律不采——控体积，见 codex.py 同款决策）。

本机未安装 Claude Code 时 available()=False，采集自动跳过、零开销；装上即生效。
写字态/气泡的触发链在 wb_hover_core._active_sessions 的 ODS 近窗并入，**数据一进
ODS 即自动触发，无需改桌宠代码**。
"""

import json
import os

from .base import AgentSource, canonical_usage, parse_ts_ms, usage_json


class ClaudeCodeSource(AgentSource):

    key = "claude-code"
    label = "Claude Code"
    has_credit = False          # 按 token 订阅/计费，无积分概念
    order = 25

    def root(self):
        return os.path.join(os.path.expanduser("~"), ".claude", "projects")

    def glob_pattern(self):
        return os.path.join("**", "*.jsonl")     # projects/<cwd 转义>/<uuid>.jsonl

    # ------------------------------------------------------------------
    # 行解析（行自包含，无文件级上下文）
    # ------------------------------------------------------------------

    def parse_lines(self, path, new_lines, ctx=None):
        rows = []
        for line_no, raw in new_lines:
            if isinstance(raw, str):            # 容错：测试/静态调用传 str
                raw = raw.encode("utf-8")
            d = self._load(raw)
            if not isinstance(d, dict):
                continue
            t = d.get("type")
            session_id = d.get("sessionId") or ""
            ts_ms = parse_ts_ms(d.get("timestamp"))
            cwd = d.get("cwd") or ""

            if t == "assistant":
                if d.get("isApiErrorMessage"):
                    continue                    # API 报错的合成行，无真实用量
                msg = d.get("message") or {}
                u = msg.get("usage") or {}
                if not isinstance(u, dict):
                    continue
                inp = _int(u.get("input_tokens"))
                out = _int(u.get("output_tokens"))
                cr = _int(u.get("cache_read_input_tokens"))
                cw = _int(u.get("cache_creation_input_tokens"))
                total_in = inp + cr + cw        # Anthropic 口径的总输入
                if total_in + out <= 0:
                    continue                    # 空行（心跳/合成），非真实调用
                usage = canonical_usage(
                    prompt_tokens=total_in,
                    completion_tokens=out,
                    total_tokens=total_in + out,
                    prompt_cache_hit_tokens=cr,
                    prompt_cache_write_tokens=cw,
                )
                rows.append(self.base_row(
                    line_no, id=f"claude:{session_id}:{line_no}",
                    event_type="usage", role="assistant",
                    session_id=session_id, ts_ms=ts_ms, cwd=cwd,
                    model=msg.get("model") or "",
                    request_id=d.get("requestId") or msg.get("id") or session_id,
                    raw_usage_json=usage_json(usage),
                    _raw=d,
                ))

            elif t == "user":
                if d.get("isMeta"):
                    continue                    # 系统注入（命令输出/上下文回填）
                msg = d.get("message") or {}
                if msg.get("role") != "user":
                    continue
                prompt = _user_text(msg.get("content"))
                if not prompt:
                    continue                    # 纯 tool_result 行，无正文
                rows.append(self.base_row(
                    line_no, id=f"claude:{session_id}:{line_no}",
                    event_type="message", role="user",
                    session_id=session_id, ts_ms=ts_ms, cwd=cwd,
                    user_prompt=prompt,
                    _raw=d,
                ))

            elif t == "summary":
                title = (d.get("summary") or "").strip()
                if not title:
                    continue
                rows.append(self.base_row(
                    line_no, id=f"claude:summary:{line_no}",
                    event_type="ai-title", role=None,
                    session_id=d.get("leafUuid") or "",
                    ts_ms=0,                     # summary 行无时间戳，仅作标题元数据
                    ai_title=title,
                    _raw=d,
                ))
        return rows


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _user_text(content):
    """user 行 content → 纯文本。str 直取；数组只拼 type=text 的段，忽略 tool_result。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for seg in content:
            if isinstance(seg, dict) and seg.get("type") == "text" and seg.get("text"):
                parts.append(seg["text"])
        return "\n".join(parts).strip()
    return ""
