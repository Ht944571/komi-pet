#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_version.py — 版本号的**唯一出处**
================================================================================
打包脚本（`packaging/build_win.py`）与运行时（`--status` / 自更新版本比较）都从这里取，
避免"exe 里写着 1.0.0、清单里写着 1.0.1"这种漂移。

发新版流程：
  1. 改这里的 `VERSION`
  2. `<venv>/python.exe packaging/build_win.py`（自动用它命名 zip）
  3. `<venv>/python.exe packaging/publish_win.py`（生成含 sha256 的 update.json）
"""

# ---- 版本号（语义化：主.次.修；比较只看数字，后缀忽略）----
VERSION = "1.0.0"

# ---- 应用标识（自更新清单里用它区分平台/产物）----
APP_NAME = "古见同学桌宠"
EXE_NAME = APP_NAME + ".exe"
MANIFEST_NAME = "update.json"

# 数据/程序的内部标识（目录名、注册表项都用 ASCII，避免编码坑）
SLUG = "KomiPet"


def as_tuple(v):
    """'1.2.3' / '1.2.3-beta.1' → (1, 2, 3)。非法输入一律当 (0,)。"""
    out = []
    for part in str(v or "").strip().split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        out.append(int(num))
    return tuple(out) if out else (0,)


def is_newer(candidate, current=VERSION):
    """candidate 是否比 current 新（按数字逐段比较，段数不同时短边补 0）。"""
    a, b = as_tuple(candidate), as_tuple(current)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def display():
    return f"v{VERSION}"
