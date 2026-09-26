# -*- coding: utf-8 -*-
"""对数脚本：确认「物化表 dws_turn」与「原视图 v_turn_total」在**看板 API 的每个端点**上
返回完全一致的数据。一致才允许把数据源切到物化表（快 ~300 倍）。

做法（不是复制 SQL，而是调**真实的 API 方法**，所以覆盖最全）：
    for src in ("v_turn_total", "dws_turn"):
        wb_api._FORCE_SRC = src          # 强制走该数据源
        api._cache.clear()               # 清 TTL 缓存，否则第二轮会命中第一轮的结果
        逐个调用端点方法 → 收集 JSON 结果
    两份结果逐字段深度比对，列出所有差异

用法：python tools/reconcile_dws_turn.py
退出码：0=完全一致；1=有差异（会打印差异位置与前几处）
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "wb_usage"))

import wb_api                                  # noqa: E402

DB_FALLBACK = os.path.join(APP, "wb_usage", "data", "wb_usage_dw.db")

# 需要覆盖的端点（全部会读轮次层的；args 为调用参数）
CASES = [
    ("kpi", {}),
    ("kpi", {"days": "today"}),
    ("daily", {"days": "all"}),
    ("daily", {"days": "today"}),
    ("daily", {"days": "7"}),
    ("projects", {}),
    ("projects", {"agent": "workbuddy"}),
    ("clients", {}),
    ("sessions", {}),
    ("tops", {}),
    ("agents", {}),
    ("models", {}),
    ("models", {"days": "7"}),
    ("models", {"days": "30", "agent": "workbuddy"}),
]


def collect(src):
    wb_api._FORCE_SRC = src
    api = wb_api.Api(wb_api.DEFAULT_DB if hasattr(wb_api, "DEFAULT_DB") else None)
    out = {}
    for name, kw in CASES:
        fn = getattr(api, name)
        key = name + (":" + json.dumps(kw, sort_keys=True) if kw else "")
        try:
            api._cache.clear()          # 每个用例都从零算，避免 TTL 缓存干扰
            out[key] = fn(**kw)
            out[key] = json.loads(json.dumps(out[key], default=str, ensure_ascii=False))
        except Exception as e:
            out[key] = {"__error__": f"{type(e).__name__}: {e}",
                        "__tb__": traceback.format_exc()[-400:]}
    return out


def diff(a, b, path="", acc=None, limit=40):
    """深度比对；返回差异列表 [(路径, 左, 右)]"""
    if acc is None:
        acc = []
    if len(acc) >= limit:
        return acc
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        acc.append((path, a, b)); return acc
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            diff(a.get(k), b.get(k), f"{path}.{k}", acc, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            acc.append((f"{path}.len", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", acc, limit)
    else:
        if isinstance(a, float) and isinstance(b, float):
            if abs(a - b) > 1e-6:
                acc.append((path, a, b))
        elif a != b:
            acc.append((path, a, b))
    return acc


def fingerprint():
    """数据指纹：用来确认两次采集之间底层数据没有变化。

    为什么需要：dws_turn 是**快照**，而 v_turn_total 是**实时视图**。
    用户只要在这期间产生新用量，两边天然就会不同 —— 那是"过期"，不是"语义不一致"，
    会误判成数据源不等价。所以对比前后都取指纹，变了就重来。
    """
    import sqlite3
    db = getattr(wb_api, "DEFAULT_DB", None) or DB_FALLBACK
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        r = c.execute("SELECT COUNT(*), COALESCE(SUM(credit),0), "
                      "COALESCE(SUM(total_tokens),0), COALESCE(MAX(last_time),'') "
                      "FROM v_turn_total").fetchone()
        return tuple(r)
    finally:
        c.close()


def main():
    print("=== 对数：v_turn_total（视图） vs dws_turn（物化表）===")
    print("（调的是看板 API 的真实方法，覆盖 kpi/daily×3/projects/clients/sessions/tops/agents/models）\n")

    import wb_dw
    db = getattr(wb_api, "DEFAULT_DB", None) or DB_FALLBACK

    # ⓪ 先自检**自动模式**（_FORCE_SRC=None，即真实线上路径）。
    #   为什么必须先做：对数时强制指定数据源，会绕过 _src() 的自动判定 ——
    #   一旦自动判定那条分支本身有 bug（实测踩过：漏了个 import，端点全 500），
    #   强制模式的对数照样全绿，把问题放过去。
    print("── 步骤 0：自动模式自检（真实路径）──")
    wb_api._FORCE_SRC = None
    api0 = wb_api.Api(db)
    broken = []
    for name, kw in CASES:
        api0._cache.clear()
        try:
            getattr(api0, name)(**kw)
        except Exception as e:
            broken.append((name, f"{type(e).__name__}: {e}"))
    if broken:
        print("❌ 自动模式下这些端点报错（日志/看板会 500）—— 先修它们：")
        for n, e in broken:
            print(f"     {n}: {e[:90]}")
        return 1
    print(f"   ✅ {len(CASES)} 个端点在自动模式下全部正常\n")

    for attempt in range(1, 4):
        # 1) 强制刷新快照，让两边尽量读同一份数据
        wb_dw.refresh_dws_turn(db_path=db, force=True, verbose=False)
        fp0 = fingerprint()
        old = collect("v_turn_total")
        new = collect("dws_turn")
        fp1 = fingerprint()
        if fp0 == fp1:
            print(f"[第 {attempt} 次] 对比期间数据未变，指纹 {fp0[0]} 轮 —— 本次结果有效\n")
            break
        print(f"[第 {attempt} 次] ⚠️ 对比期间数据变了（{fp0[0]}→{fp1[0]} 轮），重来\n")
    else:
        print("⚠️ 连续 3 次都在对比期间遇到新数据；结果仅供参考\n")
    wb_api._FORCE_SRC = None

    print(f"{'端点':34} {'结果'}")
    bad = 0
    for k in old:
        if isinstance(old[k], dict) and "__error__" in old[k]:
            print(f"  {k:32} ⚠️ 旧源报错: {old[k]['__error__'][:60]}")
            bad += 1
            continue
        if isinstance(new[k], dict) and "__error__" in new[k]:
            print(f"  {k:32} ❌ 新源报错: {new[k]['__error__'][:60]}")
            bad += 1
            continue
        d = diff(old[k], new[k], k)
        if d:
            print(f"  {k:32} ❌ 有 {len(d)} 处差异")
            for path, x, y in d[:4]:
                print(f"       {path}: {str(x)[:40]}  ≠  {str(y)[:40]}")
            bad += 1
        else:
            n = len(old[k].get("data") or []) if isinstance(old[k], dict) else 0
            print(f"  {k:32} ✅ 完全一致" + (f"（{n} 行）" if n else ""))

    print()
    if bad:
        print(f"❌ 有 {bad} 个端点不一致 —— **不要切换数据源**")
        return 1
    print("✅ 全部端点逐字段一致 —— 可以安全切换数据源到 dws_turn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
