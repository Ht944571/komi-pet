#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_setup.py — **打包成 exe 之后**的安装 / 卸载 / 状态
================================================================================
为什么不能用根目录的 `install.py`：它是按**仓库目录结构**写死的
（`ROOT/app/...`、`ROOT/app/wb_usage/wb_api.py`），而打包产物里没有仓库结构。

这里只做打包后真正需要的三件事：

  install   → 写 wb_paths.json（落数据目录）+ 注册 HKCU Run 自启（隐藏启动）+ 立即拉起
  uninstall → 删掉本项目注册的 Run 项（**不动数据目录**，用户的设置与数仓保留）
  status    → 打印运行形态 / 目录 / 自启项 / 数据源可用性

注册表只用 **HKCU\\...\\Run**（用户级、免管理员、可脚本化）——
与项目既有约定一致；不用 `schtasks`（本机在程序黑名单里，见 docs/本机网络与下载通道.md）。

自启注册**两条**：守望（拉起桌宠）+ 看板 API。桌宠自身也有 API 守护兜底，
两条一起注册只是让登录后立刻可用，不用等守护的轮询周期。
"""

import argparse
import json
import os
import subprocess
import sys

try:
    import wb_runtime as RT
except ImportError:                                     # 直接跑本文件时补 sys.path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import wb_runtime as RT

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_WATCHER = "KomiPetWhaleWatcher"
RUN_API = "KomiPetUsageApi"
LEGACY_RUN_VALUES = ("WorkBuddyWhaleWatcher",)      # 旧 skill 时代的键，顺手清掉


def init_console():
    """让中文输出在 cmd / PowerShell 里正常显示。

    中文 Windows 的默认控制台代码页是 **936(GBK)**，而 PyInstaller 打出来的程序
    按 UTF-8 写 stdout → 用户看到的是 `�ż�ͬѧ����`。这里把代码页与流的编码
    都统一到 UTF-8；`安装.cmd` / `卸载.cmd` 另外也会 `chcp 65001` 兜一层。
    """
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for n in ("stdout", "stderr"):
        s = getattr(sys, n, None)
        if s is not None and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _ok(m):
    print(f"  [OK]   {m}")


def _warn(m):
    print(f"  [警告] {m}")


def _fail(m):
    print(f"  [错误] {m}")


# ---------- 1. 写路径配置 ----------

def write_paths_config(port=8801):
    cfg = {
        "db_path": RT.db_path(),
        "workbuddy_db": os.path.join(os.path.expanduser("~"), ".workbuddy",
                                     "workbuddy.db"),
        "pos_file": os.path.join(RT.data_dir(), ".hover_pos.json"),
        "dashboard_url": f"http://127.0.0.1:{port}",
        "project_dir": RT.EXE_DIR,
        "skill_dir": RT.EXE_DIR,           # 兼容旧字段名
    }
    p = RT.paths_config_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _ok(f"路径配置已写入 {p}")
    return cfg


# ---------- 2. HKCU Run 自启 ----------

def _entries(port):
    """返回 [(键名, 命令行)]。打包后一律走 wscript 隐藏启动，避免登录闪黑窗。"""
    w = RT.hidden_launcher("watcher")
    a = RT.hidden_launcher("api", extra=["--port", port])
    return [(RUN_WATCHER, w or f'"{sys.executable}" --watcher'),
            (RUN_API, a or f'"{sys.executable}" --api --port {port}')]


def register_autostart(port=8801, start_now=True):
    if os.name != "nt":
        _warn("本脚本只负责 Windows 自启")
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
            for name, value in _entries(port):
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
                _ok(f"已注册自启项 {name}")
            for legacy in LEGACY_RUN_VALUES:
                try:
                    old, _ = winreg.QueryValueEx(k, legacy)
                    winreg.DeleteValue(k, legacy)
                    _ok(f"已清理历史自启项 {legacy}（原指向 {old}）")
                except FileNotFoundError:
                    pass
    except OSError as e:
        _fail(f"写注册表失败：{e}")
        return False

    if start_now:
        for label, kind, kw in (("桌宠守望", "watcher", {}), ("看板服务", "api", {"port": port})):
            try:
                subprocess.Popen(RT.self_cmd(kind, **kw), cwd=RT.EXE_DIR,
                                 stdin=subprocess.DEVNULL,
                                 **RT.detach_kwargs())
                _ok(f"已启动{label}")
            except Exception as e:
                _warn(f"启动{label}失败（下次登录会自启）：{e}")
    return True


def unregister_autostart():
    if os.name != "nt":
        return False
    import winreg
    removed = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            for name in (RUN_API, RUN_WATCHER) + LEGACY_RUN_VALUES:
                try:
                    winreg.DeleteValue(k, name)
                    removed.append(name)
                except FileNotFoundError:
                    pass
    except OSError as e:
        _fail(f"删注册表失败：{e}")
        return False
    if removed:
        _ok(f"已移除自启项 {', '.join(removed)}")
    else:
        print("  本来就没有本项目注册的自启项，无需操作")
    return True


def print_plan(port=8801):
    """打印「将要写入什么」但**不落盘、不改注册表** —— `--check` 用，方便核对。"""
    print("\n[将写入的配置]")
    print(f"  {RT.paths_config_path()}")
    print(f"      db_path = {RT.db_path()}")
    print("  HKCU\\...\\Run")
    for name, value in _entries(port):
        print(f"      [{name}] = {value}")


def show_status(port=8801):
    if os.name != "nt":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE) as k:
            for name in (RUN_API, RUN_WATCHER):
                try:
                    v, _ = winreg.QueryValueEx(k, name)
                    _ok(f"{name} = {v}")
                except FileNotFoundError:
                    _warn(f"{name} 未注册")
    except OSError as e:
        _warn(f"读取自启项失败：{e}")


# ---------- 3. 环境自检 ----------

def preflight():
    issues = []
    _ok(f"Python {sys.version.split()[0]}")
    for n, p in (("立绘资源 assets/", os.path.join(RT.bundle_dir(), "assets", "pet_v3")),
                 ("看板页面 dashboard.html",
                  os.path.join(RT.bundle_dir(), "wb_usage", "dashboard.html"))):
        if os.path.isdir(p) or os.path.isfile(p):
            _ok(f"{n} 就位")
        else:
            issues.append(f"缺少 {n}（{p}）—— 安装包不完整")
    db = RT.db_path()
    if os.path.isfile(db):
        _ok(f"数仓就绪（{os.path.getsize(db) / 1e9:.2f} GB）")
    else:
        _warn(f"数仓尚未生成（{db}）—— 首次打开看板会自动采集")
    # 数据源：任一可用即通过（没有也只是警告，装好 agent 后会自动采集）
    try:
        sys.path.insert(0, os.path.join(RT.bundle_dir(), "wb_usage"))
        import agents as _agents
        avail = _agents.available_sources()
        if avail:
            _ok("数据源就绪：" + " / ".join(s.label for s in avail))
        else:
            _warn("未检测到任何 agent 数据源 —— 看板暂时没有数据"
                  "（装好任一 agent 并产生会话后会自动采集，无需重装）")
    except Exception as e:
        _warn(f"数据源注册表加载失败（{type(e).__name__}: {e}）")
    return issues


def main(argv=None):
    ap = argparse.ArgumentParser(description="古见同学桌宠 · Windows 安装器")
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("action", nargs="?", default="install",
                    choices=("install", "uninstall", "status", "check"))
    args = ap.parse_args(argv)

    init_console()
    print("=" * 60)
    print("  古见同学桌宠 · Windows")
    print("=" * 60)
    print(f"  {RT.runtime_summary()}")

    if args.action == "uninstall":
        print("\n[卸载自启]")
        return 0 if unregister_autostart() else 1

    if args.action == "status":
        print("\n[状态]")
        preflight()
        print()
        show_status(args.port)
        return 0

    print("\n[1/3] 环境自检")
    issues = preflight()
    if args.action == "check":
        print_plan(args.port)
        if issues:
            print()
            for i in issues:
                _fail(i)
            return 1
        print("\n  一切正常，可以安装：古见同学桌宠.exe --install")
        return 0
    if issues:
        for i in issues:
            _fail(i)
        print("\n  存在阻塞项，已中止安装。")
        return 1

    print("\n[2/3] 写入路径配置")
    write_paths_config(args.port)

    print("\n[3/3] 注册登录自启并启动")
    register_autostart(args.port, start_now=True)

    print()
    print(f"完成。看板地址：http://127.0.0.1:{args.port}")
    print("卸载自启：古见同学桌宠.exe --uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())
