#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb_common.py — WorkBuddy 消耗看板·公共口径模块

集中所有数据口径规则（PRD 4.2.2「指标口径」双写），供采集/建模/看板三模块复用：
  1. extract_text / extract_user_prompt  — user 请求提取（<user_query> 标签 + 注入过滤）
  2. get_usage_fields                    — rawUsage 字段兼容（扩展版/常规版）
  3. mask_text                           — 看板展示层脱敏（截断 + 敏感模式打码）
  4. local_ts / day_of                  — 时间与日期归属（本地时区）
"""
import re
from datetime import datetime

# ---------- 时间 ----------

def ts_to_str(ts_ms):
    """毫秒时间戳 → 本地时区 'YYYY-MM-DD HH:MM:SS'（ts<=0 返回空串）。"""
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def day_of(ts_ms):
    """毫秒时间戳 → 本地时区日期 'YYYY-MM-DD'（PRD 口径：按本地时区归属，非 UTC）。"""
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


# ---------- user_prompt 提取 ----------

def extract_text(content):
    """从 content（str 或 list）提取纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                t = c.get("text") or c.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def extract_user_prompt(content):
    """提取用户真实请求（PRD 口径）：
    1. 优先取 <user_query>...</user_query> 标签内容（新版格式，89%）
    2. 无标签时：纯 system 注入跳过；早期裸文本整段保留
    """
    text = extract_text(content)
    if not text.strip():
        return ""
    m = re.search(r"<user_query>(.*?)</user_query>", text, re.S)
    if m:
        return m.group(1).strip()
    stripped = text.lstrip()
    if stripped.startswith(("<system-reminder", "<user_info")):
        return ""
    return text.strip()


# ---------- rawUsage 字段兼容 ----------

def get_usage_fields(ru):
    """从 rawUsage 提取 (credit, pt, ct, tt, cached, miss, write, thinking)，兼容两种版本。
    返回 None 表示该事件无有效 usage（非 LLM 调用）。
    PRD 口径：
      cached   = prompt_cache_hit_tokens（扩展版）else prompt_tokens_details.cached_tokens
      miss     = prompt_cache_miss_tokens（扩展版）else prompt_tokens - cached - write
      thinking = completion_thinking_tokens else completion_tokens_details.reasoning_tokens
    """
    if not isinstance(ru, dict):
        return None
    pt = ru.get("prompt_tokens") or 0
    if not pt:
        return None
    ct = ru.get("completion_tokens") or 0
    tt = ru.get("total_tokens") or (pt + ct)
    credit = ru.get("credit") or 0.0
    cached = ru.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = ((ru.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    cached = cached or 0
    miss = ru.get("prompt_cache_miss_tokens")
    write = ru.get("prompt_cache_write_tokens") or 0
    if miss is None:
        miss = max(pt - cached - write, 0)
    miss = miss or 0
    thinking = ru.get("completion_thinking_tokens")
    if thinking is None:
        thinking = ((ru.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0
    thinking = thinking or 0
    return credit, pt, ct, tt, cached, miss, write, thinking


# ---------- 展示层脱敏 ----------

# 敏感模式（展示层打码用）
_SENSITIVE_PATTERNS = [
    # 邮箱：user@domain → u***@domain
    (re.compile(r"([\w.+-]{1,2})[\w.+-]*@([\w-]+\.)+[\w-]+"),
     lambda m: m.group(1) + "***@" + m.group(0).split("@")[1]),
    # 手机号：13712348909 → 137****8909
    (re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)"),
     lambda m: m.group(1) + "****" + m.group(3)),
    # 绝对路径：/Users/xxx/yyy → /Users/***/yyy
    (re.compile(r"((?:/Users|/home|/Volumes)/)[^/\s]+(/[^\s]*)?"),
     lambda m: m.group(1) + "***" + (m.group(2) or "")),
    # 疑似密钥/长串 Token（≥24 位字母数字混合）：只留前 6 后 4
    (re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{24,})(?![A-Za-z0-9])"),
     lambda m: m.group(1)[:6] + "***" + m.group(1)[-4:]),
]


def mask_text(text, limit=30):
    """展示层脱敏：截断前 limit 字 + 敏感模式打码。
    存储层不调用本函数（保留全文）。
    """
    if not text:
        return ""
    masked = text
    for pat, repl in _SENSITIVE_PATTERNS:
        try:
            masked = pat.sub(repl, masked)
        except Exception:
            pass
    if len(masked) > limit:
        return masked[:limit] + "…"
    return masked
