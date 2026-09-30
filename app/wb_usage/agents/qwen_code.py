# -*- coding: utf-8 -*-
"""
agents.qwen_code — Qwen Code 数据源（整文件 JSON，走 collect() 通道）
================================================================================

Qwen Code 是 Gemini CLI 的分叉，会话记录**不是 jsonl**，而是一个会话一个 JSON 文档：

    ~/.qwen/projects/<项目 slug>/chats/<uuid>.json         ← 会话正文（含每步 tokens）
    ~/.qwen/projects/<项目 slug>/chats/<uuid>.runtime.json ← 运行时信息（pid/work_dir）
    ~/.qwen/tmp/<hash>/chats/*.json                        ← 另一种布局（也存在）

所以本源覆盖 `collect()`（与 zcode / opencode 同一条库型通道）——按行游标那套对
"整文件 JSON" 不适用。幂等仍靠 (file_path, line_no) 主键：`line_no` 由
"会话 id + 消息序号" 派生稳定哈希，重采不会重复。

⚠️ **本机验证状态（2026-09-30）**：`~/.qwen/` 存在、目录结构对得上，但**一个会话文件都没有**
（装上后没实际用过），所以下面的字段映射是按 Gemini CLI 系的公开格式写的，
**尚未用真实样本核对**。为此解析做了**防御式**处理：

  · tokens 块兼容 `tokens` / `usage` 两种键名；
  · 各字段兼容 input/prompt/promptTokens、output/completion/completionTokens、
    cached/cacheRead/cache_read、thoughts/reasoning、total 等写法；
  · **认不出来就不出行**（宁缺勿错）——绝不让脏数据进看板。

等真跑过一轮，用 `python wb_collect.py --agent qwen-code` 核对行数与数字即可。

映射到规范用量形状（见 agents/base.py）——无积分概念，has_credit=False：

    prompt_tokens              ← tokens.input（缺则 0）
    completion_tokens          ← tokens.output
    total_tokens               ← tokens.total（缺则 input+output+thoughts）
    prompt_cache_hit_tokens    ← tokens.cached
    completion_thinking_tokens ← tokens.thoughts
"""

import hashlib
import json
import os

from .base import AgentSource, canonical_usage, usage_json, parse_ts_ms


def _stable_no(text):
    h = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:13]
    return int(h, 16)


def _pick(d, *names):
    """从 dict 里按若干候选键名取第一个能转成数字的值。"""
    for n in names:
        if n in d:
            try:
                v = int(d[n])
                if v >= 0:
                    return v
            except (TypeError, ValueError):
                continue
    return 0


class QwenCodeSource(AgentSource):

    key = "qwen-code"
    label = "Qwen Code"
    has_credit = False
    order = 55

    # ---------- 位置 ----------

    def root(self):
        return os.path.join(os.path.expanduser("~"), ".qwen")

    def available(self):
        # 有 projects/ 或 tmp/ 下的 chats 目录才算"数据目录存在"
        for base in ("projects", "tmp"):
            p = os.path.join(self.root(), base)
            if os.path.isdir(p):
                for dirpath, _dirs, _files in os.walk(p):
                    if os.path.basename(dirpath) == "chats":
                        return True
        return False

    def glob_pattern(self):
        return ""               # 走 collect() 通道，不做按行游标

    # ---------- 变化指纹 ----------

    def _chat_files(self):
        """列出全部会话 JSON（排除 *.runtime.json）。"""
        out = []
        for base in ("projects", "tmp"):
            top = os.path.join(self.root(), base)
            if not os.path.isdir(top):
                continue
            for dirpath, _dirs, files in os.walk(top):
                if os.path.basename(dirpath) != "chats":
                    continue
                for fn in files:
                    if fn.endswith(".json") and not fn.endswith(".runtime.json"):
                        out.append(os.path.join(dirpath, fn))
        return out

    def change_hint(self):
        try:
            files = self._chat_files()
            if not files:
                return ""                       # 空 → 指纹为空串（无变化）
            newest = max(os.path.getmtime(f) for f in files)
            return f"{len(files)}:{int(newest)}"
        except Exception:
            return None

    # ---------- 采集 ----------

    def collect(self, conn, db_path, insert_rows):
        files = self._chat_files()
        if not files:
            return 0
        rows = []
        for path in files:
            try:
                doc = json.load(open(path, encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            sid = (str(doc.get("sessionId") or doc.get("session_id") or "")
                   or os.path.splitext(os.path.basename(path))[0])
            # cwd：同目录的 <uuid>.runtime.json 里有 work_dir（最可靠）
            cwd = self._cwd_of(path)
            msgs = doc.get("messages")
            if not isinstance(msgs, list):
                msgs = []
            title = ""
            turn_n = 0
            for i, m in enumerate(msgs):
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role") or m.get("type") or "").lower()
                txt = _text_of(m)
                if role in ("user", "human"):
                    turn_n += 1
                    if not title and txt:
                        title = txt[:60]
                    if txt:
                        rows.append(self.base_row(
                            _stable_no(f"u:{sid}:{i}"),
                            id=f"qwen:{sid}:u{i}",
                            event_type="message", role="user",
                            session_id=sid,
                            ts_ms=parse_ts_ms(m.get("timestamp") or m.get("time")),
                            cwd=cwd, request_id=f"{sid}:u{turn_n}",
                            user_prompt=txt, ai_title=title or None,
                        ))
                    continue
                # 助手消息：只有认得 tokens 才出行
                tok = m.get("tokens") if isinstance(m.get("tokens"), dict) else None
                if tok is None and isinstance(m.get("usage"), dict):
                    tok = m["usage"]
                if not tok:
                    continue
                inp = _pick(tok, "input", "input_tokens", "prompt", "promptTokens",
                            "prompt_tokens")
                out = _pick(tok, "output", "output_tokens", "completion",
                            "completionTokens", "completion_tokens")
                cached = _pick(tok, "cached", "cacheRead", "cache_read",
                               "cached_tokens", "cache_read_tokens")
                think = _pick(tok, "thoughts", "reasoning", "reasoning_tokens",
                              "completion_thinking_tokens")
                total = _pick(tok, "total", "total_tokens", "totalTokens")
                if inp <= 0 and out <= 0:
                    continue                    # 认不出来 → 宁缺勿错
                usage = canonical_usage(
                    prompt_tokens=inp,
                    completion_tokens=out,
                    total_tokens=total or (inp + out + think),
                    prompt_cache_hit_tokens=cached,
                    completion_thinking_tokens=think,
                )
                rows.append(self.base_row(
                    _stable_no(f"a:{sid}:{i}"),
                    id=f"qwen:{sid}:a{i}",
                    event_type="usage", role="assistant",
                    session_id=sid,
                    ts_ms=parse_ts_ms(m.get("timestamp") or m.get("time")),
                    cwd=cwd,
                    model=str(m.get("model") or ""),
                    request_id=f"{sid}:u{max(turn_n, 1)}",
                    raw_usage_json=usage_json(usage),
                    ai_title=title or None,
                ))
        if not rows:
            return 0
        # 用文件路径做稳定 file_path（去掉前缀差异，打包/换机后仍一致）
        return int(insert_rows(conn, f"{self.root()}::chats", self, rows) or 0)

    def _cwd_of(self, chat_path):
        """同目录 <uuid>.runtime.json 的 work_dir（拿不到就退回空）。"""
        rt = chat_path[:-5] + ".runtime.json"
        try:
            d = json.load(open(rt, encoding="utf-8"))
            return str(d.get("work_dir") or "")
        except Exception:
            pass
        # 退一步：projects/c--users-htft2-xxx 这种 slug 还原（'-' 全替换成分隔符）
        parts = os.path.normpath(chat_path).split(os.sep)
        if "projects" in parts:
            slug = parts[parts.index("projects") + 1]
            guess = slug.replace("-", "/")
            return guess[:2].replace("/", ":") + guess[2:] if len(guess) > 2 else ""
        return ""


def _text_of(m):
    """从消息里取纯文本（兼容 content 列表 / text / parts）。"""
    c = m.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        out = []
        for it in c:
            if isinstance(it, dict) and isinstance(it.get("text"), str):
                out.append(it["text"])
            elif isinstance(it, str):
                out.append(it)
        return "\n".join(out).strip()
    if isinstance(m.get("text"), str):
        return m["text"].strip()
    return ""
