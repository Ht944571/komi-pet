# -*- coding: utf-8 -*-
"""
agents.base — 多 agent 数据源接口
==================================

设计要点（为什么这样分层）
--------------------------
看板/数仓的所有聚合都建立在 `ods_jsonl_event.raw_usage_json` 上，而 v_call / v_turn
等 VIEW 是按**固定的 JSON 字段名**提取用量的（见 wb_dw.VIEW_DDL）：

    credit / prompt_tokens / completion_tokens / total_tokens /
    prompt_cache_hit_tokens / prompt_cache_write_tokens /
    prompt_cache_miss_tokens / completion_thinking_tokens

因此**每个 agent 的采集器只需把自家用量「归一」成这个形状**，全部既有视图、
接口、前端图表即可原样复用——不需要为每个 agent 各写一套 SQL。

这就是本模块的核心契约：`parse_lines()` 输出的 `raw_usage_json` 必须是
CANONICAL_USAGE 描述的规范形状（缺失的字段可以不给，视图会用 COALESCE 兜 0）。

新增一个 agent 只需三步：
  1. 在 agents/ 下新建 xxx.py，继承 AgentSource，实现 root() / glob_pattern()
     / build_context() / parse_lines()
  2. 在 agents/__init__.py 的 SOURCES 里注册
  3. 跑 `python wb_collect.py --agent xxx` 验证能采到行
"""

import json
import os

# ---------------------------------------------------------------------------
# 规范用量形状（各 agent 必须归一到这个字段集）
# ---------------------------------------------------------------------------
CANONICAL_USAGE_FIELDS = (
    "credit",                     # 积分（没有积分概念的 agent 不填）
    "prompt_tokens",              # 输入 token
    "completion_tokens",          # 输出 token
    "total_tokens",               # 总 token
    "prompt_cache_hit_tokens",    # 命中缓存的输入 token
    "prompt_cache_write_tokens",  # 写入缓存的输入 token
    "prompt_cache_miss_tokens",   # 未命中缓存的输入 token（可缺，视图会按 prompt-cached-write 回退）
    "completion_thinking_tokens", # 思维链 / reasoning token
)


def canonical_usage(**kw):
    """把 agent 自家的 token 计数整理成规范形状；未提供的字段不写入。"""
    out = {}
    for k, v in kw.items():
        if k not in CANONICAL_USAGE_FIELDS:
            raise KeyError(f"未知用量字段 {k}（规范字段见 CANONICAL_USAGE_FIELDS）")
        if v is None:
            continue
        out[k] = v
    return out


def usage_json(usage):
    """规范用量 → 入库用的 JSON 文本；空 dict 返回 None（视图据此过滤）。"""
    return json.dumps(usage, ensure_ascii=False) if usage else None


def parse_ts_ms(v):
    """容错解析时间戳 → 毫秒整数。

    支持：13 位毫秒 int、10 位秒 int、float 秒、ISO8601 字符串（含 Z）。
    """
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        n = float(v)
        return int(n * 1000) if n < 1e11 else int(n)   # < 1e11 视为秒
    s = str(v).strip()
    if s.isdigit():
        return parse_ts_ms(int(s))
    # ISO8601：2026-08-23T11:59:15.895Z
    try:
        from datetime import datetime, timezone
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def project_of_cwd(cwd):
    """cwd → 项目名（默认取最后一段目录名）。"""
    if not cwd:
        return ""
    return os.path.basename(str(cwd).replace("\\", "/").rstrip("/")) or ""


class AgentSource:
    """一个用量来源（一个"agent"）。

    子类必须覆盖：key / label / root() / build_context() / parse_lines()
    可选覆盖：glob_pattern() / available() / has_credit / project_of()
    """

    key = ""              # 稳定标识，入库写进 ods_jsonl_event.agent
    label = ""            # 展示名
    has_credit = False    # 是否有「积分」概念（前端据此决定是否显示积分列）
    order = 100           # 展示排序

    # ---- 文件发现 ----

    def root(self):
        """数据根目录。"""
        raise NotImplementedError

    def glob_pattern(self):
        """相对 root 的 glob（支持 ** 递归）。默认 projects/<dir>/*.jsonl。"""
        return os.path.join("*", "*.jsonl")

    def available(self):
        """本机是否存在该 agent 的数据目录。"""
        try:
            return os.path.isdir(self.root())
        except Exception:
            return False

    # ---- 行解析 ----

    def build_context(self, path):
        """可选的「文件级上下文」预扫描。

        每行都自带上文的数据源（如 WorkBuddy）不需要实现；
        上下文在文件头的日志格式（如 Codex 的 session_meta）需要。

        返回任意对象，会在 parse_lines 中原样回传。
        """
        return None

    def parse_lines(self, path, new_lines, ctx=None):
        """把新增行解析成待入库行列表。

        参数
          path      源文件绝对路径
          new_lines [(line_no, raw_bytes)]，行号 > 上次游标，按文件顺序
          ctx       build_context() 的返回值

        返回列表，每项是 dict：
          {line_no, id, event_type, role, session_id, ts_ms, cwd, model,
           request_id, raw_usage_json, user_prompt, ai_title}
        （project 由采集器统一按 project_of 补，子类无需关心）
        """
        raise NotImplementedError

    # ---- 可选：库型数据源（SQLite 等，非按行日志）----

    def collect(self, conn, db_path, insert_rows):
        """非文件型数据源的采集入口。

        默认返回 None，表示"本源走文件游标机制"（workbuddy / codex 就是这种）。
        子类**覆盖本方法**即接管采集：采集器检测到覆盖后，会跳过文件枚举与断点续采，
        直接用本方法自行读库、构造规范行，并通过 `insert_rows()` 落库。

        为什么需要它：ZCode 的用量在 SQLite 里（`model_usage` 表），根本没有"文件 +
        行号 + 游标"这套结构。硬套按行契约只会把接口拧歪。

        约定：
          · `conn`       已打开的采集库连接（桌面看板的 ODS 库）
          · `db_path`    看板 ODS 库路径（一般用不到）
          · `insert_rows(path, src, rows)` 落库函数；path 是自造的稳定标识
          · 必须**幂等**：重复采集不得产生重复行（靠 (file_path, line_no) 主键 + OR IGNORE，
            所以 line_no 要用**由源记录 id 派生的稳定整数**，不要用递增序号）
        返回：新增行数
        """
        return None

    # ---- 可选：项目名映射 ----

    def project_of(self, cwd):
        return project_of_cwd(cwd)

    # ---- 工具 ----

    @staticmethod
    def _load(raw):
        """raw 字节 → dict；解析失败返回 None（坏行跳过，不炸整批）。"""
        try:
            d = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return None
        return d if isinstance(d, dict) else None

    @staticmethod
    def base_row(line_no, **kw):
        """构造一条规范的 ODS 行，避免各处手写缺 key。"""
        row = {
            "line_no": line_no,
            "id": "", "event_type": "", "role": None, "session_id": "",
            "ts_ms": 0, "cwd": "", "model": "", "request_id": "",
            "raw_usage_json": None, "user_prompt": None, "ai_title": None,
        }
        row.update(kw)
        return row
