# -*- coding: utf-8 -*-
"""
agents.deepseek_harness — DeepSeek Harness 数据源
================================================================

数据位置：`~/.dsh/sessions/<项目槽>/session-<uuid>/session.jsonl.zstd`
（**zstd 压缩**，且是**追加写**的多帧流 —— 必须流式解压，见 `zstdio.py` 的说明）

为什么走"库型通道"（覆盖 `collect()`）而不走文件游标
---------------------------------------------------
文件的增量机制建立在"文件 + 字节偏移 + 行号"上，而这里是**压缩流**：
没法按行号 seek，每次都必须整份解压。硬套游标只会得到一个永远走慢路径的实现。
所以本模块自己负责扫描与幂等，只借用 `insert_rows()` 落库。

两个必须处理的坑
----------------
**① 同一会话存在两种格式的文件**：实测存在会话目录里同时有
`session.jsonl.zstd` 与 `session.v3.jsonl.zstd`，内容高度重叠。
直接扫会**重复计数**。
→ 解法不是"猜哪个是权威"，而是**让重复自然折叠**：`line_no` 用**内容派生的稳定身份**
（`hash("usage:<sid>:<turn>:<step>")`），两种格式里同一事件算出同一个键，
`INSERT OR IGNORE` 自动去重。既不用赌格式，也不怕以后再加一种。

**② 未变化文件不重解**：解压是开销大头。用 `meta` 存 `size|mtime_ns`，
未变则跳过（等价于文件源里的 `fstate` 快跳）。

映射到规范用量形状（见 agents/base.py）—— DSH 无积分概念，has_credit=False：

    prompt_cache_hit_tokens    ← cacheReadTokens 的**差分**（会话级累计 → 单步增量）
    prompt_tokens              ← inputTokens + 上面的增量
    completion_tokens          ← data.usage.outputTokens
    completion_thinking_tokens ← data.usage.reasoningTokens
    total_tokens               ← prompt + completion（自算）

⚠️ **本数据源最大的坑：同一个 usage 对象里混着两种口径**（2026-09-24 实测）：

  · `inputTokens` / `outputTokens` / `reasoningTokens` = **本步增量**
  · `cacheReadTokens` = **会话级累计**（跨轮持续递增，**从不重置**）
  · `totalTokens`     = 累计，且实测与 in+out 的对不上（差 33）→ **不用**

而且 `inputTokens` **只含新增（未命中缓存）部分**，命中缓存的那部分走 `cacheReadTokens`。
所以单次调用的真实输入 = `inputTokens + 缓存增量`。

两版错误都踩过，记下来免得再犯：
  ① 把 cacheReadTokens 当增量 → 算出"缓存 3.9 亿 > 输入 294 万"；
  ② 改成"轮内累计差分" → 仍错：它在**轮边界不重置**，于是每轮第一步
     都把整个会话的累计值当成了本步增量。

验证口径是否自洽的两条硬指标（必须都成立）：
  · `cached ≤ prompt`（命中不可能超过输入）
  · `total == prompt + completion`

事件 → 行的对应（实测事件类型）
    assistant/message  → 用量（`data.usage`，一个 turn 内可能有多个 step）
    user/message       → 用户提问（**要过滤 source.kind=plugin**，插件注入的也是 user 角色）
    session/title      → ai-title（看板会话名）
    session            → 会话头：id / cwd
"""

import hashlib
import json
import os
import time

from .base import AgentSource, canonical_usage, usage_json

# zstdio 在上一层（wb_usage/）。agents 是**顶层包**（wb_usage 没有 __init__.py），
# 所以不能用 `from .. import`（会 "beyond top-level package"），要显式补 sys.path。
try:
    import zstdio
except ImportError:                                       # pragma: no cover
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import zstdio


def _stable_no(text):
    """内容身份 → 稳定 52 位整数行号（重复计数靠它折叠）。"""
    return int(hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:13], 16)


def _to_int(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


# 这些 source.kind 的 user/message 不是真人提问（是插件/系统注入的回填）
_NON_USER_KINDS = ("plugin", "system", "tool", "sandbox", "approval")


class DeepSeekHarnessSource(AgentSource):

    key = "deepseek-harness"
    label = "DeepSeek Harness"
    has_credit = False          # 按 Token 计费，无积分口径
    order = 25

    def root(self):
        return os.path.join(os.path.expanduser("~"), ".dsh", "sessions")

    def glob_pattern(self):
        return os.path.join("*", "*", "*.jsonl.zstd")

    def available(self):
        return os.path.isdir(self.root())

    # ---- 自己管扫描与幂等 ----

    def _files(self):
        out = []
        root = self.root()
        if not os.path.isdir(root):
            return out
        for slot in os.listdir(root):
            sp = os.path.join(root, slot)
            if not os.path.isdir(sp):
                continue
            for sess in os.listdir(sp):
                d = os.path.join(sp, sess)
                if not os.path.isdir(d):
                    continue
                for fn in os.listdir(d):
                    if fn.endswith(".jsonl.zstd"):
                        out.append(os.path.join(d, fn))
        return sorted(out)

    @staticmethod
    def _meta_get(conn, key):
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    @staticmethod
    def _meta_set(conn, key, val):
        conn.execute("INSERT OR REPLACE INTO meta(key, value, updated_at) VALUES(?,?,?)",
                     (key, val, time.strftime("%Y-%m-%d %H:%M:%S")))

    # ---- 采集 ----

    def collect(self, conn, db_path, insert_rows):
        ok, layer, hint = zstdio.describe()
        if not ok:
            print(f"[dsh] 跳过：{hint}")
            return 0

        files = self._files()
        if not files:
            return 0

        # 所有 DSH 数据落在同一个逻辑 file_path 下，让"同会话两种格式"的重复自动折叠
        logical = os.path.join(self.root(), "_dsh_sessions.jsonl.zstd")
        rows = []
        scanned = 0
        for f in files:
            try:
                st = os.stat(f)
            except OSError:
                continue
            state = f"{st.st_size}|{st.st_mtime_ns}"
            key = f"dshf:{f}"
            if self._meta_get(conn, key) == state:
                continue                       # 未变化：跳过解压（开销大头在此）
            data = zstdio.read_zstd(f)
            if not data:
                continue
            scanned += 1
            got = []
            try:
                got = self._parse(f, data.decode("utf-8", errors="replace"))
            except Exception as e:
                print(f"[dsh] 解析失败 {os.path.basename(f)}: {e}")
            rows += got
            # 只在**确实产出**（或文件真的为空）时记状态。
            # 反例教训：第一版无条件记状态，而当时解压被静默截断只出 203 字节、
            # 解析 0 行 —— 状态照样被写死，导致后续每一轮都"跳过未变化"，永久 0 行。
            # 少记一次状态只是下次重解一遍，代价远小于"永久静默失败"。
            if got:
                self._meta_set(conn, key, state)

        inserted = insert_rows(conn, logical, self, rows) if rows else 0
        if scanned:
            print(f"[dsh] 解压 {scanned} 个会话文件，产出 {len(rows)} 行")
        return inserted

    # ---- 单文件解析 ----

    def _parse(self, path, text):
        sess_id = ""
        cwd = ""
        out = []
        # cacheReadTokens 是**会话级累计**（不随轮重置）→ 用单值维护上一累计即可。
        # 用 dict 包一层只是为了让内层赋值生效（字符串/整数在闭包里不能 rebind）。
        prev = {"cache": 0}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type") or ""
            data = d.get("data") or {}
            ts = _to_int(d.get("time"))

            if t == "session":
                sess_id = d.get("id") or sess_id
                cwd = d.get("cwd") or cwd
                continue
            if not sess_id:
                continue

            if t == "assistant/message":
                u = data.get("usage") or {}
                inp = _to_int(u.get("inputTokens"))
                if inp <= 0:
                    continue
                turn, step = data.get("turn"), data.get("step")
                out_tok = _to_int(u.get("outputTokens"))
                # 会话级累计 → 差分出本步"命中缓存的输入"（重试可能回退，钳到 0）
                cum = _to_int(u.get("cacheReadTokens"))
                cache_delta = cum - prev["cache"] if cum >= prev["cache"] else cum
                prev["cache"] = cum
                # prompt = 新增输入 + 命中缓存的输入（inputTokens 只含新增部分）
                prompt_tok = inp + cache_delta
                usage = canonical_usage(
                    prompt_tokens=prompt_tok,
                    completion_tokens=out_tok,
                    total_tokens=prompt_tok + out_tok,
                    prompt_cache_hit_tokens=cache_delta,
                    completion_thinking_tokens=_to_int(u.get("reasoningTokens")),
                )
                out.append(self.base_row(
                    _stable_no(f"usage:{sess_id}:{turn}:{step}"),
                    id=f"dsh:{sess_id}:{turn}:{step}",
                    event_type="usage", role="assistant",
                    session_id=sess_id, ts_ms=ts, cwd=cwd,
                    model="",                      # 模型在 request/header 里，按需再补
                    request_id=f"{sess_id}:turn-{turn}",
                    raw_usage_json=usage_json(usage),
                    _raw={"type": t, "seq": d.get("seq"), "time": ts,
                          "turn": turn, "step": step, "usage": u},
                ))

            elif t == "user/message":
                src = (data.get("source") or {}).get("kind") or ""
                if data.get("role") != "user" or src.lower() in _NON_USER_KINDS:
                    continue                       # 过滤插件/系统注入（它们也是 user 角色）
                texts = [c.get("text") for c in (data.get("content") or [])
                         if isinstance(c, dict) and c.get("type") == "text" and c.get("text")]
                if not texts:
                    continue
                mid = data.get("id") or f"{sess_id}:{d.get('seq')}"
                out.append(self.base_row(
                    _stable_no(f"prompt:{mid}"),
                    id=f"dsh-msg:{mid}",
                    event_type="message", role="user",
                    session_id=sess_id, ts_ms=ts, cwd=cwd,
                    user_prompt="\n".join(texts),
                    _raw={"type": t, "seq": d.get("seq"), "time": ts,
                          "id": mid, "text": "\n".join(texts)},
                ))

            elif t == "session/title":
                title = data.get("title") or ""
                if not title:
                    continue
                # 身份带上 time：会话标题会多次更新，全保留让 v_session 取最新那条
                # （原先只按 session 折叠，结果留下的是**最早**的标题）
                out.append(self.base_row(
                    _stable_no(f"title:{sess_id}:{d.get('seq')}"),
                    id=f"dsh-title:{sess_id}:{d.get('seq')}",
                    event_type="ai-title", role=None,
                    session_id=sess_id, ts_ms=ts, cwd=cwd,
                    ai_title=title,
                    _raw={"type": t, "session_id": sess_id, "title": title,
                          "seq": d.get("seq")},
                ))
        return out
