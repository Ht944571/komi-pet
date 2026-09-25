# -*- coding: utf-8 -*-
"""
wb_agent_registry.py — agent 登记册（唯一来源）
================================================================

`assets/_agents.json` 是「**谁在跑 / 配什么配件 / 有哪些能力 / 配置在哪**」的
**唯一声明处**。配件层（`wb_accessories`）与活跃探测层（`wb_agent_presence`）
都从这里读，避免"同一份信息写两处"——本项目反复吃过的亏。

为什么要有这一层：
  原先"哪个 agent 配什么配件"写在 `_acc_persona.json`、"怎么探测"也写在同一个文件的
  `presence` 字段里，而"有哪些能力/开关"根本没有。要做「用户自主选择接入哪些 agent」，
  需要一个**带开关与能力声明**的统一登记处。于是合并成 `_agents.json`。

向后兼容
--------
`_agents.json` 缺失时**回退**到旧的 `_acc_persona.json`（其 `agents[key].presence /
accessory` 结构与新格式兼容），保证老环境的 assets 目录也能跑。

缓存
----
按 (mtime, size) 缓存：`accessories()` 会被每帧调用（配件层要判断显示哪些），
不能每次都读盘；但用户在设置里改了开关要能立刻生效，所以不能永久缓存。
"""

import json
import os
import shutil

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
REGISTRY = os.path.join(ASSETS, "_agents.json")
LEGACY = os.path.join(ASSETS, "_acc_persona.json")

_cache = None          # (mtime_ns, size, data)


def load(force=False):
    """返回 {"agents": {key: spec}}；读不到返回 {"agents": {}}。

    spec 里保证有的键：label / enabled / caps / presence。
    """
    global _cache
    for path in (REGISTRY, LEGACY):
        try:
            st = os.stat(path)
        except OSError:
            continue
        sig = (st.st_mtime_ns, st.st_size)
        if not force and _cache and _cache[0] == path and _cache[1] == sig:
            return _cache[2]
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        agents = data.get("agents") if isinstance(data, dict) else None
        if not isinstance(agents, dict):
            continue
        norm = {}
        for key, spec in agents.items():
            if not isinstance(spec, dict):
                continue
            norm[key] = {
                "label": spec.get("label") or key,
                # 旧格式没有 enabled → 视为启用（保持老行为）
                "enabled": bool(spec.get("enabled", True)),
                "verified": bool(spec.get("verified", True)),
                "order": spec.get("order", 100),
                "accessory": spec.get("accessory"),
                "accent": spec.get("accent"),
                "caps": spec.get("caps") or {},
                "presence": spec.get("presence") or {},
                "config_paths": spec.get("config_paths") or [],
                "log_paths": spec.get("log_paths") or [],
                "notes": spec.get("notes") or "",
            }
        out = {"agents": norm, "source": os.path.basename(path)}
        _cache = (path, sig, out)
        return out
    return {"agents": {}, "source": None}


def all_agents(include_disabled=True):
    """全部登记项（默认含未启用）。"""
    ag = load()["agents"]
    if include_disabled:
        return ag
    return {k: v for k, v in ag.items() if v["enabled"]}


def enabled_keys():
    return [k for k, v in load()["agents"].items() if v["enabled"]]


def accessories():
    """供配件层：{key: {accessory, label, accent}} —— 只含启用的、且有配件声明的。"""
    out = {}
    for key, v in load()["agents"].items():
        if v["enabled"] and v["accessory"]:
            out[key] = {"accessory": v["accessory"],
                        "label": v["label"],
                        "accent": v["accent"]}
    return out


def probes():
    """供活跃探测层：{key: {"procs": [...小写], "ports": [...]}} —— 只含启用的、且配了探针的。"""
    out = {}
    for key, v in load()["agents"].items():
        if not v["enabled"]:
            continue
        pr = v["presence"] or {}
        procs = [str(p).lower() for p in (pr.get("procs") or [])]
        ports = []
        for p in (pr.get("ports") or []):
            try:
                ports.append(int(p))
            except (TypeError, ValueError):
                pass
        if procs or ports:
            out[key] = {"procs": procs, "ports": ports}
    return out


def set_enabled(key, enabled):
    """把某个 agent 的 `enabled` 改成指定布尔值。

    ⚠️ 这是本项目**唯一会写配置文件**的函数，所以刻意保守：
      · 只接受**已登记的 key** + **布尔值**（不接受任意 JSON / 任意字段）
      · 读原始文档只改那一个字段 → `note` / `schema` / 键顺序全部原样保留
      · 先备份 `.bak`，再写临时文件 + `os.replace()`（原子替换，不会写坏）
      · 写完立刻失效缓存（桌宠那边下一轮就能看到新开关）

    只写 `_agents.json`（用户可编辑的登记册），**绝不碰任何 agent 自己的配置文件**
    —— 那是 Clawd 走的路（写 hooks），我们不重复那个代价。
    """
    try:
        with open(REGISTRY, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        return {"ok": False, "error": f"登记册不可读：{type(e).__name__}"}
    if not isinstance(doc, dict) or not isinstance(doc.get("agents"), dict):
        return {"ok": False, "error": "登记册结构异常"}
    if key not in doc["agents"]:
        return {"ok": False, "error": f"未登记的 agent: {key}"}
    if not isinstance(enabled, bool):
        return {"ok": False, "error": "enabled 必须是布尔值"}
    if doc["agents"][key].get("enabled") == enabled:
        return {"ok": True, "data": {"key": key, "enabled": enabled, "changed": False}}
    doc["agents"][key]["enabled"] = enabled
    try:
        shutil.copy2(REGISTRY, REGISTRY + ".bak")
    except OSError:
        pass
    tmp = REGISTRY + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REGISTRY)
    except OSError as e:
        return {"ok": False, "error": f"写入失败：{e.strerror or e}"}
    load(force=True)
    return {"ok": True, "data": {"key": key, "enabled": enabled, "changed": True}}


def source_file():
    """当前生效的登记册文件名（诊断用：确认读的是新表还是回退到旧表）。"""
    return load()["source"]


# ---------------------------------------------------------------------------
# 健康检查（T1 交付物 ①）：verified 是"手填的曾经验证过"，这里是**当下**的
# 自动事实——声明了的路径（config_paths / log_paths）现在还在不在。
# 三值语义：无路径声明 → "unknown"，绝不冤枉没声明路径的 agent。
# ---------------------------------------------------------------------------

import time as _time

_health_cache = None       # (checked_at, data)
HEALTH_TTL_S = 30.0        # 面板轮询远高于路径变化频率，30s 节流足够


def _paths_state(paths):
    """None = 无声明；True = 至少一个存在；False = 声明了但全部缺失。"""
    if not paths:
        return None, 0, 0
    import glob as _glob
    found = 0
    for pat in paths:
        p = os.path.expanduser(str(pat))
        try:
            if any(c in p for c in "*?["):
                if _glob.glob(p, recursive=True):
                    found += 1
            elif os.path.exists(p):
                found += 1
        except Exception:
            continue
    return (True if found else False), found, len(paths)


def health(force=False):
    """全部登记项的当下健康度：{key: {...}}（缓存 HEALTH_TTL_S 秒）。

    health 取值：
      "ok"          路径都在（含部分在）
      "unavailable" 声明了路径但**全部缺失**（agent 被删/没装）→ 面板标"不可用"
      "unknown"     无路径声明（无法自动判定，如实展示，不装懂）
    """
    global _health_cache
    now = _time.time()
    if not force and _health_cache and now - _health_cache[0] < HEALTH_TTL_S:
        return _health_cache[1]
    out = {}
    for key, spec in load()["agents"].items():
        st, found, total = _paths_state((spec.get("config_paths") or [])
                                        + (spec.get("log_paths") or []))
        h = "unknown" if st is None else ("ok" if st else "unavailable")
        out[key] = {"health": h, "path_state": st,
                    "paths_found": found, "paths_total": total}
    _health_cache = (now, out)
    return out
