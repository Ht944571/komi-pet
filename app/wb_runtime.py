#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_runtime.py — 运行时环境适配层（源码运行 / PyInstaller 打包后 都要能跑）
================================================================================
为什么需要它
------------
本项目所有**可写**文件（设置 / 位置 / 数仓 / 路径配置）原本都放在**代码旁边**。
源码运行没问题，但打包成 exe 后有两种死法：

  · onefile：代码每次解到临时目录 → 写进去的东西下次启动就没了；
  · onedir：装到 `Program Files` 这类没有写权限的地方 → 设置存不下、数仓建不起来。

所以这里把「可写」与「只读」**彻底分开**：

  只读（assets / dashboard.html / agents 注册表）→ 继续从**包内**读（`bundle_dir()`）
  可写（设置 / 位置 / 数仓 / 路径配置）      → 统一走 `data_dir()`

`data_dir()` 优先级
------------------
  1. 环境变量 `KOMIPET_DATA_DIR`（测试 / 多实例隔离）
  2. exe 同级存在 `portable.txt` → `<exe 同级>\\data`（绿色便携版，塞 U 盘也能跑）
  3. `%LOCALAPPDATA%\\KomiPet`

**源码运行时直接返回 `app/` 目录** —— 与打包前行为**一模一样**，
现有测试、`install.py`、开发流程全部零影响。

`self_cmd()`：打包后"再拉起自己"的唯一正确姿势
--------------------------------------------
打包后没有 `pythonw`，也没有 `.py` 文件，`pythonw xxx.py` 这条路**全部失效**。
所以三处唤起（桌宠拉起 API / 守望拉起桌宠 / 自启注册）统一改走这里：
  源码 → `pythonw` + 脚本；打包 → **自身 exe + 子命令**（`--pet` / `--api` / `--watcher`）。
"""

import os
import sys

# ---------- 1. 运行形态 ----------

FROZEN = bool(getattr(sys, "frozen", False))
"""True = 跑在 PyInstaller 打出来的 exe 里。"""


def bundle_dir():
    """**只读**资源所在目录：源码 = `app/`；打包后 = PyInstaller 的解包目录。

    用 `sys._MEIPASS` 而不是 `__file__`：冻结模块的 `__file__` 指向 PYZ 里的假路径，
    而 `_MEIPASS` 是 PyInstaller 官方保证的"数据文件落点"（onedir 下即 `_internal/`）。
    """
    if FROZEN:
        return getattr(sys, "_MEIPASS", None) or os.path.dirname(
            os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = bundle_dir()                      # 只读资源根（assets / wb_usage 静态文件）
EXE_DIR = (os.path.dirname(os.path.abspath(sys.executable))
           if FROZEN else APP_DIR)          # exe 所在目录（portable.txt 就放这儿）

_DATA_DIR = None


def data_dir():
    """**可写**数据目录（设置 / 位置 / 数仓 / wb_paths.json）。首次调用时创建。"""
    global _DATA_DIR
    if _DATA_DIR:
        return _DATA_DIR
    d = os.environ.get("KOMIPET_DATA_DIR")
    if not d and FROZEN:
        if os.path.isfile(os.path.join(EXE_DIR, "portable.txt")):
            d = os.path.join(EXE_DIR, "data")           # 绿色便携版
        else:
            base = (os.environ.get("LOCALAPPDATA")
                    or os.environ.get("APPDATA")
                    or os.path.expanduser("~"))
            d = os.path.join(base, "KomiPet")
    if not d:
        d = APP_DIR                                     # 源码运行：与打包前一致
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    _DATA_DIR = d
    return d


def db_path():
    """数仓文件路径（可写）。

    ⚠️ 源码运行时**必须**保持 `app/wb_usage/data/wb_usage_dw.db` 不变 ——
    本机那份 1.2GB 的既有数仓就在那儿，改了等于换库。
    """
    if FROZEN:
        return os.path.join(data_dir(), "wb_usage_dw.db")
    return os.path.join(APP_DIR, "wb_usage", "data", "wb_usage_dw.db")


def paths_config_path():
    """`wb_paths.json` 的位置（由 install.py 写、运行时读）。"""
    if FROZEN:
        return os.path.join(data_dir(), "wb_paths.json")
    return os.path.join(APP_DIR, "wb_paths.json")


# ---------- 2. 再拉起自己 ----------

def pythonw():
    """同目录的 pythonw.exe（无控制台）；没有就退回当前解释器。"""
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return cand if os.path.isfile(cand) else sys.executable


def self_cmd(kind, port=None):
    """返回"再拉起一个自己"的完整命令行（list）。

    kind = "pet" | "api" | "watcher" | "install"
      · 源码运行 → pythonw(+脚本)，与打包前**逐字符一致**
      · 打包后   → `[自身exe, "--<kind>"]`，端口等参数继续跟在后面
    """
    if FROZEN:
        cmd = [sys.executable, "--" + kind]
        if kind == "api":
            cmd += ["--port", str(port or 8801)]
        return cmd
    if kind == "pet":
        return [pythonw(), os.path.join(APP_DIR, "hover.py")]
    if kind == "watcher":
        return [pythonw(), os.path.join(APP_DIR, "wb_whale_watcher.py")]
    if kind == "api":
        return [sys.executable, os.path.join(APP_DIR, "wb_usage", "wb_api.py"),
                "--port", str(port or 8801)]
    raise ValueError(f"未知的 self_cmd 类型：{kind}")


VBS_NAME = "launch_hidden.vbs"


def hidden_launcher(kind, extra=()):
    """自启注册用的命令行（HKCU Run）。

    打包后的 exe 是 **console 子系统**（这样 API 的 stdout 日志才能重定向到文件），
    直接写进 Run 会**每次登录闪一个黑窗**。所以打包后经同目录的
    `launch_hidden.vbs` 用 `WScript.Shell.Run(..., 0)` 隐藏启动。

    返回 None 表示"源码运行"——由调用方按原来 `pythonw xxx.py` 的老办法拼（保持行为不变）。
    """
    if not FROZEN:
        return None
    tail = " ".join([kind] + [str(a) for a in extra])
    vbs = os.path.join(EXE_DIR, VBS_NAME)
    if os.path.isfile(vbs):
        return f'wscript.exe //nologo "{vbs}" {tail}'
    return f'"{sys.executable}" --{tail}'       # 兜底：至少能起来（会闪一下）


def detach_kwargs():
    """后台分离启动的 Popen kwargs（不继承控制台 / 独立进程组）。

    保留项目原有的 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`：
    桌宠与守望都不该挂在调用者的控制台上——调用者一退，它们就跟着死。
    """
    if os.name == "nt":
        return {"creationflags": 0x00000008 | 0x00000200,
                "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def free_console():
    """打包后的**桌宠 / 守望**入口调用：把自己从控制台摘掉，双击 exe 不留黑窗。

    - 只在「打包后」且**当前确实挂着控制台**时动手（`DETACHED_PROCESS` 起的子进程
      本来就没有控制台，此函数自动 no-op）。
    - **API 服务不要调**：它的启动/自检日志要打到 stdout。
    - 先 `ShowWindow(hwnd, SW_HIDE)` 再 `FreeConsole()`，避免看得见的一闪。
    """
    if not FROZEN or os.name != "nt":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetConsoleWindow()
        if not h:
            return False
        ctypes.windll.user32.ShowWindow(h, 0)      # SW_HIDE
        k32.FreeConsole()
        return True
    except Exception:
        return False


def runtime_summary():
    """一行摘要，供 --status / 启动日志用。"""
    return (f"形态={'打包exe' if FROZEN else '源码'}  "
            f"资源={APP_DIR}  数据={data_dir()}")
