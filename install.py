#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install.py — 古见同学桌宠 · 独立项目安装器
================================================================
把「桌宠 + 多 agent 用量看板」注册成开机常驻，一条命令完成，无需管理员。

做四件事：
  1. 环境自检（Python 版本 / **任一 agent 数据源** / 项目文件完整性）
  2. 写 app/wb_paths.json（本机真实路径，供桌宠与看板读取）
  3. 注册登录自启（Windows：HKCU\\...\\Run，纯 winreg，**不用 schtasks**）
       KomiPetUsageApi      → pythonw app/wb_usage/wb_api.py --port 8801
       KomiPetWhaleWatcher  → pythonw app/wb_whale_watcher.py
  4. 清理历史自启项（从 WorkBuddy skill 目录独立出来之前的 WorkBuddyWhaleWatcher）

用法：
  python install.py                # 完整安装（写配置 + 注册自启 + 立即启动）
  python install.py --check        # 只做环境自检，不改动任何东西
  python install.py --no-autostart # 只写配置，不注册自启
  python install.py --port 8802    # 换看板端口
  python install.py --status       # 看当前状态
  python install.py --uninstall    # 移除本项目注册的自启项
"""

import argparse
import json
import os
import subprocess
import sys

# ---------- 目录约定（全部相对本项目，与 WorkBuddy skill 目录无关）----------
ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
WB_USAGE_DIR = os.path.join(APP, "wb_usage")
DB_PATH = os.path.join(WB_USAGE_DIR, "data", "wb_usage_dw.db")
PATHS_CFG = os.path.join(APP, "wb_paths.json")
API_PY = os.path.join(WB_USAGE_DIR, "wb_api.py")
WATCHER_PY = os.path.join(APP, "wb_whale_watcher.py")
HOVER_ENTRY = os.path.join(APP, "hover.py")

HOME = os.path.expanduser("~")

# ---------- HKCU Run 自启项名称 ----------
RUN_API = "KomiPetUsageApi"
RUN_WATCHER = "KomiPetWhaleWatcher"
LEGACY_RUN_VALUES = ("WorkBuddyWhaleWatcher",)          # 旧 skill 的键
LEGACY_SCHTASKS = (r"WorkBuddy\WBBUsageApi", r"WorkBuddy\WBBHoverBall")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

MIN_PY = (3, 8)


def ok(msg):
    print(f"  [OK]   {msg}")


def warn(msg):
    print(f"  [警告] {msg}")


def fail(msg):
    print(f"  [错误] {msg}")


# ---------- 1. 环境自检 ----------

def preflight():
    issues = []
    if sys.version_info < MIN_PY:
        issues.append(f"Python 版本过低：{sys.version.split()[0]}（需 >= {'.'.join(map(str, MIN_PY))}）")
    else:
        ok(f"Python {sys.version.split()[0]}")

    # 数据源自检：**不对 WorkBuddy 硬编码**。
    # 只要任一「已注册 agent 数据源」在本机可用即通过；一个都没有也只警告、不阻塞安装
    # ——用户可以先把桌宠装上，之后再装 agent（装上并产生会话后会自动被采集）。
    projects_root = os.path.join(HOME, ".workbuddy", "projects")   # 兼容旧返回值
    try:
        if os.path.join(APP, "wb_usage") not in sys.path:
            sys.path.insert(0, os.path.join(APP, "wb_usage"))
        import agents as _agents
        avail = _agents.available_sources()
    except Exception as e:
        avail = []
        warn(f"数据源注册表加载失败（{type(e).__name__}: {e}）——看板将没有数据")
    if avail:
        ok("数据源就绪：" + " / ".join(s.label for s in avail))
    else:
        warn("未检测到任何 agent 数据源 —— 看板暂时没有数据。"
             "装好任一 agent 并产生会话后会自动采集，无需重装本程序。")

    for f, name in ((API_PY, "wb_api.py"), (HOVER_ENTRY, "hover.py"),
                    (WATCHER_PY, "wb_whale_watcher.py"),
                    (os.path.join(APP, "wb_whale_win.py"), "wb_whale_win.py"),
                    (os.path.join(APP, "wb_motion.py"), "wb_motion.py")):
        if not os.path.isfile(f):
            issues.append(f"缺少核心文件 {name}（{f}）——请确认项目完整")

    if not os.path.isfile(DB_PATH):
        warn(f"数仓尚未生成（{DB_PATH}）——首次打开看板会自动采集")
    else:
        ok(f"数仓就绪（{os.path.getsize(DB_PATH) / 1e9:.2f} GB）")

    return projects_root, issues


# ---------- 2. 路径配置 ----------

def write_paths_config(port):
    cfg = {
        "db_path": DB_PATH,
        "workbuddy_db": os.path.join(HOME, ".workbuddy", "workbuddy.db"),
        "pos_file": os.path.join(APP, ".hover_pos.json"),
        "dashboard_url": f"http://127.0.0.1:{port}",
        "project_dir": ROOT,
        "skill_dir": ROOT,          # 兼容旧字段名
    }
    with open(PATHS_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    ok(f"路径配置已写入 {os.path.relpath(PATHS_CFG, ROOT)}")
    return cfg


# ---------- 3. Windows：HKCU Run 自启 ----------

def _pythonw():
    """找到与 sys.executable 同目录的 pythonw.exe（无控制台）。"""
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.isfile(cand):
        return cand
    import shutil
    return shutil.which("pythonw") or sys.executable


def _run_key_write(entries):
    """entries: [(name, command_str)] —— 写入 HKCU Run，并清理历史项。"""
    import winreg
    written, removed = [], []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
        for name, value in entries:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
            written.append(name)
        for legacy in LEGACY_RUN_VALUES:
            try:
                old, _ = winreg.QueryValueEx(k, legacy)
                winreg.DeleteValue(k, legacy)
                removed.append((legacy, old))
            except FileNotFoundError:
                pass
    for name in written:
        ok(f"已注册自启项 {name}")
    for name, old in removed:
        ok(f"已清理历史自启项 {name}（原指向 {old}）")
    return True


def _run_key_delete():
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
    except PermissionError as e:
        fail(f"删除失败（权限不足）：{e}")
        return False
    if removed:
        ok(f"已移除自启项 {', '.join(removed)}")
    else:
        print("  本来就没有本项目注册的自启项，无需操作")
    return True


def _run_key_status():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE) as k:
            for name in (RUN_API, RUN_WATCHER):
                try:
                    v, _ = winreg.QueryValueEx(k, name)
                    ok(f"{name} = {v}")
                except FileNotFoundError:
                    warn(f"{name} 未注册")
    except OSError as e:
        warn(f"读取自启项失败：{e}")


def register_autostart(port, start_now=True):
    if os.name != "nt":
        warn("非 Windows 平台：本脚本只负责 Windows 自启。")
        warn("macOS 请自行写 launchd plist（参考 app/b_hover.py 的注释）。")
        return False
    pyw = _pythonw()
    entries = [
        (RUN_API, f'"{pyw}" "{API_PY}" --port {port}'),
        (RUN_WATCHER, f'"{pyw}" "{WATCHER_PY}"'),
    ]
    if not _run_key_write(entries):
        return False
    if start_now:
        for label, cmd in (("看板服务", [pyw, API_PY, "--port", str(port)]),
                           ("桌宠守望", [pyw, WATCHER_PY])):
            try:
                subprocess.Popen(cmd, cwd=APP,
                                 creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP
                ok(f"已启动{label}")
            except Exception as e:
                warn(f"启动{label}失败（下次登录会自启）：{e}")
    return True


# ---------- 4. 收尾提示 ----------

def print_legacy_hint():
    print()
    print("  提示：本项目已改用 HKCU Run 自启，不再使用计划任务。")
    print("        若你的机器上还残留旧 skill 注册的计划任务，请在**普通终端**里执行：")
    for t in LEGACY_SCHTASKS:
        print(f'          schtasks /Delete /TN "{t}" /F')
    print("        （本脚本不代为执行，避免误删；`schtasks /Query /TN \"<名称>\"` 可先确认是否存在）")


def main():
    ap = argparse.ArgumentParser(description="古见同学桌宠 · 独立项目安装器")
    ap.add_argument("--check", action="store_true", help="只做环境自检，不改动任何东西")
    ap.add_argument("--no-autostart", action="store_true", help="只写配置，不注册自启")
    ap.add_argument("--port", type=int, default=8801, help="看板服务端口（默认 8801）")
    ap.add_argument("--status", action="store_true", help="显示当前状态")
    ap.add_argument("--uninstall", action="store_true", help="移除本项目注册的自启项")
    ap.add_argument("--no-start", action="store_true",
                    help="注册后不立即启动（用于只写自启项、由下次登录生效的场景）")
    args = ap.parse_args()

    print("=" * 60)
    print("  古见同学桌宠 · 独立项目")
    print("=" * 60)
    print(f"  项目目录：{ROOT}")

    if args.status:
        print("\n[状态]")
        projects_root, _ = preflight()
        _run_key_status()
        return 0

    if args.uninstall:
        print("\n[卸载自启]")
        return 0 if _run_key_delete() else 1

    print("\n[1/3] 环境自检")
    projects_root, issues = preflight()

    if args.check:
        print("\n[自检结果]")
        if issues:
            for i in issues:
                fail(i)
            return 1
        print("  一切正常，可以安装：python install.py")
        return 0

    if issues:
        print("\n[自检结果]")
        for i in issues:
            fail(i)
        print("\n  存在阻塞项，已中止安装。")
        return 1

    print("\n[2/3] 写入路径配置")
    write_paths_config(args.port)

    print("\n[3/3] 注册登录自启")
    if args.no_autostart:
        print("  已跳过（--no-autostart）")
    else:
        register_autostart(args.port, start_now=not args.no_start)
        print_legacy_hint()

    print()
    print("完成。看板地址：http://127.0.0.1:%d" % args.port)
    print("手动启动：start-pet.cmd（桌宠） / start-dashboard.cmd（看板）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
