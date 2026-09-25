# -*- coding: utf-8 -*-
"""
agents.workbuddy — WorkBuddy 数据源
====================================

WorkBuddy 把每轮会话逐行写进 `~/.workbuddy/projects/<项目目录>/<会话 uuid>.jsonl`，
每行一个事件，用量挂在 `providerData.rawUsage` 上。

**WorkBuddy 是本项目的「规范形状基准」**：其它 agent 的 token 字段都往它这一步对齐
（见 agents/base.py 的 CANONICAL_USAGE_FIELDS），因此这里原样透传 rawUsage 即可。

这里只做「行 → ODS 行」的翻译，文件枚举与断点续采由 wb_collect 负责。
"""

import json
import os

from .base import AgentSource

# wb_common 在上一层目录（wb_usage/），采集器运行时该目录在 sys.path 上
try:
    from wb_common import extract_user_prompt
except ImportError:                                    # pragma: no cover
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from wb_common import extract_user_prompt


class WorkBuddySource(AgentSource):

    key = "workbuddy"
    label = "WorkBuddy"
    has_credit = True
    order = 10

    def root(self):
        return os.path.join(os.path.expanduser("~"), ".workbuddy", "projects")

    def glob_pattern(self):
        # projects/<项目目录>/<会话>.jsonl —— 正好两层，不递归
        return os.path.join("*", "*.jsonl")

    def parse_lines(self, path, new_lines, ctx=None):
        rows = []
        for line_no, raw in new_lines:
            d = self._load(raw)
            if d is None:
                continue
            t = d.get("type") or ""
            role = d.get("role")
            pd = d.get("providerData") or {}
            ru = pd.get("rawUsage")

            user_prompt = None
            if t == "message" and role == "user":
                user_prompt = extract_user_prompt(d.get("content"))

            rows.append(self.base_row(
                line_no,
                id=d.get("id") or "",
                event_type=t,
                role=role,
                session_id=d.get("sessionId") or "",
                ts_ms=d.get("timestamp") or 0,
                cwd=d.get("cwd") or "",
                model=pd.get("model") or "",
                request_id=pd.get("conversationRequestId") or "",
                # WorkBuddy 的 rawUsage 已是规范形状，原样透传
                raw_usage_json=(json.dumps(ru, ensure_ascii=False) if ru else None),
                user_prompt=user_prompt,
                ai_title=(d.get("aiTitle") if t == "ai-title" else None),
                _raw=d,          # 原文归档（采集器写入 ods_jsonl_raw 后剔除）
            ))
        return rows
