# -*- coding: utf-8 -*-
"""
agents — 多 agent 用量数据源注册表
====================================

看板不再只吃 WorkBuddy 一家：每个「agent」（用量来源）实现一个 AgentSource
（见 base.py），在这里注册，采集器遍历全部已注册源。

新增一个 agent：
  1. agents/xxx.py 里写 class XxxSource(AgentSource)
  2. 下面 SOURCES 里加进去
  3. python wb_collect.py --agent xxx 验证

约定
----
`key` 会写进 `ods_jsonl_event.agent` 列，是数据链路上的稳定标识，**不要随意改**
（改了就等同于换了一个 agent）。历史行 agent 为 NULL，视图按 'workbuddy' 解释。
"""

from .base import (AgentSource, CANONICAL_USAGE_FIELDS,
                   canonical_usage, usage_json, parse_ts_ms, project_of_cwd)
from .workbuddy import WorkBuddySource
from .codex import CodexSource
from .zcode import ZCodeSource
from .deepseek_harness import DeepSeekHarnessSource
from .ccusage import CcusageSource
from .claude_code import ClaudeCodeSource
from .opencode import OpenCodeSource
from .qwen_code import QwenCodeSource

# 注册表：越靠前越先采集（order 小的排前面）
SOURCES = (
    WorkBuddySource(),      # jsonl 文件源
    ZCodeSource(),          # SQLite 库源（走 collect() 覆盖通道）
    DeepSeekHarnessSource(),  # zstd 压缩流源（走 collect() 覆盖通道）
    CodexSource(),          # jsonl 文件源
    ClaudeCodeSource(),     # jsonl 文件源（本机未装时 available()=False 自动跳过）
    CcusageSource(),        # ccusage 覆盖面补充源（collect() 通道；无 Node 自动跳过）
    OpenCodeSource(),       # SQLite 库源（走 collect() 覆盖通道）
    QwenCodeSource(),       # 整文件 JSON 源（走 collect() 覆盖通道）
)

BY_KEY = {s.key: s for s in SOURCES}


def all_sources():
    """全部已注册源（含本机不可用的）。"""
    return list(SOURCES)


def available_sources():
    """本机数据目录存在的源。"""
    return [s for s in SOURCES if s.available()]


def get_source(key):
    """按 key 取源；未注册返回 None。"""
    return BY_KEY.get(key)


def source_keys():
    return [s.key for s in SOURCES]


__all__ = [
    "AgentSource", "CANONICAL_USAGE_FIELDS", "canonical_usage", "usage_json",
    "parse_ts_ms", "project_of_cwd",
    "WorkBuddySource", "CodexSource", "ZCodeSource", "DeepSeekHarnessSource",
    "CcusageSource", "ClaudeCodeSource", "OpenCodeSource", "QwenCodeSource",
    "SOURCES", "BY_KEY", "all_sources", "available_sources", "get_source",
    "source_keys",
]
