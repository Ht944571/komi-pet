#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_check_refs.py — 静态自检：删功能之后，有没有留下「调用/读取一个不存在的东西」。

为什么需要（2026-09-28 的教训）
--------------------------------
批量删方法时最容易漏掉调用点，而 **Python 只在运行时才炸**。
那次删掉 `_react()` 后，`_pet_mouse` 里**漏了一处**调用（双击身体那条路径），
`ast.parse` 通过、13 个单元测试全过，却在真机上连抛了几轮
`AttributeError: 'WhalePet' object has no attribute '_react'`（日志里才看到）。

**改完功能删减，跑一遍这个。**

用法
----
    python tools/_check_refs.py                 # 默认检查 app/ 下的运行时代码
    python tools/_check_refs.py wb_whale_win.py

退出码 0 = 干净；1 = 有问题（会列出文件:行号）。
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)

DEFAULT_FILES = ["wb_whale_win.py", "wb_anim.py", "wb_motion.py",
                 "wb_runtime.py", "wb_setup.py", "wb_tray.py",
                 "wb_whale_watcher.py", "wb_follow.py"]


def _collect(tree):
    """返回 (类方法名集合, 已赋值属性集合)。

    已赋值 = `self.x = ...`（含下标赋值的取值端）+ **类体里的 `x = ...`**（类属性）
             + `getattr/setattr(self, "x", ...)` 的常量名。
    """
    methods, assigned = set(), set()
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for m in cls.body:
            if isinstance(m, ast.FunctionDef):
                methods.add(m.name)
            elif isinstance(m, ast.Assign):
                for t in m.targets:                  # 类属性：_fonts = {} / fn = None
                    if isinstance(t, ast.Name):
                        assigned.add(t.id)
            elif isinstance(m, ast.AnnAssign) and isinstance(m.target, ast.Name):
                assigned.add(m.target.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "self" and isinstance(node.ctx, (ast.Store, ast.Del)):
            assigned.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("getattr", "setattr") and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Constant):
            assigned.add(node.args[1].value)
    return methods, assigned


def check_calls(path):
    """self.xxx(...) 里有没有未定义的方法。

    ⚠️ 允许「已赋值的属性」被调用 —— `self.fn = fn` 之后 `self.fn(t)` 是合法的
    （Once 类就是这么用的），不能只认同类方法名。
    """
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    methods, assigned = _collect(tree)
    allowed = methods | assigned
    bad = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"):
            if node.func.attr not in allowed:
                bad.add((node.lineno, node.func.attr))
    return [f"   @{ln}  self.{name}()" for ln, name in sorted(bad)]


def check_attrs(path):
    """self.xxx 里有没有「只读未写」的属性。"""
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    methods, assigned = _collect(tree)
    read = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "self" and isinstance(node.ctx, ast.Load):
            read.setdefault(node.attr, []).append(node.lineno)
    return [f"   self.{k}  @{', '.join(map(str, v[:6]))}"
            for k, v in sorted(read.items(), key=lambda x: x[1][0])
            if k not in assigned and k not in methods]


def main(argv):
    files = argv[1:] or DEFAULT_FILES
    bad_total = 0
    for f in files:
        p = f if os.path.isabs(f) else os.path.join(APP, f)
        if not os.path.isfile(p):
            print(f"跳过（不存在）: {f}")
            continue
        issues = check_calls(p) + check_attrs(p)
        if issues:
            print(f"❌ {os.path.basename(p)}")
            print("\n".join(issues))
            bad_total += len(issues)
        else:
            print(f"✅ {os.path.basename(p)} 干净")
    print("\n总结:", "发现问题 %d 处" % bad_total if bad_total else "全部干净")
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
