#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_win.py — 打 Windows 版安装包（PyInstaller onedir + 控制台子系统）
================================================================================
产出（都在 `dist/`）：

    dist/古见同学桌宠/                      ← 绿色包目录（解压即用）
        ├─ 古见同学桌宠.exe                 双击 = 起桌宠；带子命令见 README
        ├─ _internal/                      运行时（Python + 标准库 + 素材）
        ├─ launch_hidden.vbs               隐藏启动（自启用，避免登录闪黑窗）
        ├─ 安装.cmd / 卸载.cmd              双击即可（有 pause，能看清输出）
        └─ README.txt
    dist/古见同学桌宠-Windows-<版本>.zip     ← 分发包

几个刻意的选择（都有理由，别顺手改）
----------------------------------
1) **`--console` 而不是 `--windowed`**：桌宠守护会拉起看板 API 并把它的 stdout
   重定向到日志文件；`--windowed` 下 PyInstaller 会把 `sys.stdout` 置为 None，
   **API 日志会整个丢掉**。代价是双击 exe 会闪一下控制台 —— 由 `hover.py` 里的
   `RT.free_console()` 立刻摘掉，登录自启则走 `wscript` 隐藏启动，都看不到黑窗。
2) **只带必要素材**：`assets/_gen3`（生成式源图 11MB）、`_whale_backup`（4.4MB）、
   `_gen`、`*.bak`、`_review_*.png` 全部不进包 —— 它们是**开发期中间产物**，
   可重新生成，不该让用户下载。
3) **不带 `wb_usage/data/`**：那里既有 1.2GB 的本机数仓，也有必须保留的
   `chart.umd.min.js`。所以只挑静态文件复制，数仓交给运行时在用户目录里新建。
4) **不带 `b_hover*.py`**（历史圆球形态）：按任务文档要求归档、不随包分发，
   用 `--exclude-module` 排除（`hover.py` 里的 `--classic` 分支在打包版不可用）。

用法：
    <venv>/python.exe packaging/build_win.py            # 完整打包 + 压缩
    <venv>/python.exe packaging/build_win.py --no-zip   # 只打包目录，不压缩
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
STAGE = os.path.join(BUILD, "stage")
ICON = os.path.join(ROOT, "packaging", "app.ico")

# 版本号/应用名的**唯一出处**是 app/wb_version.py（运行时也要用，不能各写一份）
sys.path.insert(0, os.path.join(ROOT, "app"))
import wb_version as V          # noqa: E402

NAME = V.APP_NAME
VERSION = V.VERSION

# 不进包的素材（开发期中间产物，可重新生成）
ASSET_SKIP_DIRS = {"_gen3", "_whale_backup", "_gen", "__pycache__"}
ASSET_SKIP_FILES = {"_review_dark.png", "_review_white.png"}
# 打包时排除的模块
EXCLUDES = ["b_hover", "b_hover_win", "tkinter", "unittest", "test", "lib2to3",
            "distutils", "setuptools", "pip", "pydoc_data", "pytest", "numpy",
            "PIL", "imageio", "imageio_ffmpeg"]

VBS = '''\
' 隐藏启动古见同学桌宠 —— 供「登录自启」使用。
' 为什么需要它：exe 是控制台子系统（这样看板 API 的 stdout 日志才能重定向到文件），
' 直接写进 HKCU\\...\\Run 会每次登录闪一个黑窗口。经 wscript + Run(...,0) 隐藏启动。
' 用法： wscript.exe //nologo "launch_hidden.vbs" <子命令> [其它参数...]
Option Explicit
Dim sh, d, i, args, cmd
Set sh = CreateObject("WScript.Shell")
d = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\\"))
args = ""
For i = 0 To WScript.Arguments.Count - 1
  args = args & " " & WScript.Arguments(i)
Next
cmd = """" & d & "''' + NAME + '''.exe"""
If Len(args) > 0 Then
  cmd = cmd & " --" & Mid(args, 2)
End If
sh.Run cmd, 0, False
'''

INSTALL_CMD = '''\
@echo off
rem 双击安装：写配置 + 注册登录自启 + 立刻启动
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   古见同学桌宠 · Windows 安装
echo ============================================================
"%~dp0''' + NAME + '''.exe" --install %*
echo.
pause
'''

UNINSTALL_CMD = '''\
@echo off
rem 双击卸载：只移除「登录自启」，不动你的设置与用量数据
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   古见同学桌宠 · 卸载登录自启
echo ============================================================
"%~dp0''' + NAME + '''.exe" --uninstall
echo.
pause
'''

README = '''\
古见同学桌宠 · Windows 版 v{ver}
================================================================

她是站在你桌面最上层的一只小桌宠，顺便替你盯着各个 AI agent 的用量。

【怎么用】
  1. 双击「{name}.exe」 —— 桌宠就出来了（不用装 Python，什么都不用装）
  2. 想让它开机自动出现：双击「安装.cmd」（会注册登录自启）
  3. 不想开机自启了：双击「卸载.cmd」
     （只移除自启项，你的设置和用量数据都会留着）

【操作】
  · 左键拖      移动位置（松手会自动贴边）
  · 左键点身体  按部位互动：头→开心 / 脸→惊讶 / 胸腹→害羞~傲娇~石化 / 裙摆→委屈
  · 双击气泡    打开用量看板（浏览器会打开 http://127.0.0.1:8801）
  · 长按身体    憋气喷水
  · 右键菜单    大小 / 气泡开关 / 画质 / 退出
  · 退出        右键托盘图标 → 退出古见同学（窗口本身没有关闭按钮）

【数据放哪】
  默认在  %LOCALAPPDATA%\\KomiPet\\
      .whale_settings.json   你的设置（大小/画质/气泡开关…）
      .whale_pos.json        桌宠位置
      wb_usage_dw.db         用量数仓（首次打开看板时自动采集生成）
      wb_paths.json          路径配置（--install 写入）

  ★ 绿色便携模式：在 exe 同级目录放一个空文件 portable.txt，
    数据就改放  <本目录>\\data\\  —— 塞 U 盘里换台电脑也能带着走。

【自定义更新（可选）】
  本程序支持自更新：发布方给一个 update.json 清单，你指过去即可。

    {name}.exe --set-update-source <地址>   设置更新源（目录或 update.json 的 URL/路径）
    {name}.exe --check-update              看看有没有新版
    {name}.exe --update                    立即更新（下载→校验→替换→自动重启）

  · 地址可以是 http(s)、局域网共享、甚至本地文件夹
  · **不设置就完全不联网**（默认不检查更新，放心）
  · 设置后：桌宠启动会静默检查（每天一次），有新版会冒个气泡提示；
    右键菜单会出现「更新到 vX」，点它才会真的更新
  · 下载的包会按清单里的 sha256 校验，不通过绝不安装
  · 更新只替换程序，**你的设置和用量数据原样保留**

【命令行（可选）】
  {name}.exe --install [--port 8801]    安装自启（等同双击 安装.cmd）
  {name}.exe --uninstall                移除自启
  {name}.exe --status                   查看当前状态与自启项
  {name}.exe --check                    只做环境自检
  {name}.exe --pet                      只起桌宠本体（不注册任何东西）
  {name}.exe --api --port 8801          只起看板服务
  {name}.exe --watcher                  只起守望（它会自己拉起桌宠）

【看板打不开？】
  再次双击「{name}.exe」即可 —— 桌宠会顺手把看板服务拉起来（也会在 60 秒内自动守护）。
'''.format(ver=VERSION, name=NAME)


def log(msg):
    print(msg, flush=True)


def rmtree(p):
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)


def stage_files():
    """把要进包的**数据文件**挑出来放进 stage/（只读资源）。"""
    rmtree(STAGE)
    # 1) assets：裁掉开发期中间产物
    src_assets = os.path.join(APP, "assets")
    dst_assets = os.path.join(STAGE, "assets")
    n = 0
    bytes_ = 0
    for dirpath, dirnames, filenames in os.walk(src_assets):
        dirnames[:] = [d for d in dirnames if d not in ASSET_SKIP_DIRS]
        rel = os.path.relpath(dirpath, src_assets)
        out_dir = os.path.join(dst_assets, rel) if rel != "." else dst_assets
        for fn in filenames:
            if fn in ASSET_SKIP_FILES or fn.endswith((".bak", ".pyc", ".log")):
                continue
            os.makedirs(out_dir, exist_ok=True)
            s = os.path.join(dirpath, fn)
            shutil.copy2(s, os.path.join(out_dir, fn))
            n += 1
            bytes_ += os.path.getsize(s)
    log(f"  assets: {n} 个文件, {bytes_ / 1e6:.1f} MB（已剔除 _gen3/_whale_backup/_gen/*.bak）")

    # 2) wb_usage 的**静态资源**（.py 交给 PyInstaller 当模块打；data/ 里只取 chart.js）
    for rel in (("dashboard.html",), ("data", "chart.umd.min.js")):
        s = os.path.join(APP, "wb_usage", *rel)
        if os.path.isfile(s):
            d = os.path.join(STAGE, "wb_usage", *rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            log(f"  wb_usage/{'/'.join(rel)} → 已入包")
    return dst_assets


def run_pyinstaller(no_icon=False):
    pw = [sys.executable, "-m", "PyInstaller"]
    args = pw + [
        "--noconfirm", "--clean", "--noupx",
        "--onedir", "--console",
        "--name", NAME,
        "--distpath", DIST,
        "--workpath", BUILD,
        "--specpath", BUILD,
        "--paths", APP,
        "--paths", os.path.join(APP, "wb_usage"),
        "--add-data", f"{os.path.join(STAGE, 'assets')}{os.pathsep}assets",
        "--add-data", f"{os.path.join(STAGE, 'wb_usage')}{os.pathsep}wb_usage",
    ]
    if os.path.isfile(ICON) and not no_icon:
        args += ["--icon", ICON]
    else:
        log("  ⚠️ 未找到 packaging/app.ico，使用默认图标")
    for m in EXCLUDES:
        args += ["--exclude-module", m]
    args += [os.path.join(APP, "hover.py")]

    log("  PyInstaller 开始分析依赖…（首次约 1-3 分钟）")
    t0 = time.time()
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       errors="replace")
    dt = time.time() - t0
    tail = (p.stdout or "")[-2500:]
    if p.returncode != 0:
        log("  ❌ PyInstaller 失败，输出尾部：")
        log(tail)
        if p.stderr:
            log((p.stderr or "")[-1500:])
        raise SystemExit(1)
    warn = [l for l in (p.stdout or "").splitlines()
            if "WARNING" in l and "missing module" not in l.lower()]
    log(f"  ✅ PyInstaller 完成（{dt:.0f}s）")
    if warn:
        log(f"  （{len(warn)} 条警告，非致命；首条：{warn[0][:120]}）")
    return p


def write_runtime_files():
    """把 exe 旁边的配套文件补齐：隐藏启动脚本 / 安装卸载 / 说明书。"""
    d = os.path.join(DIST, NAME)
    # ⚠️ .vbs 必须用 **UTF-16(带 BOM)** 写：wscript 默认按 ANSI 读 .vbs，
    #    用 UTF-8 存的话里面的中文路径（古见同学桌宠.exe）会整个乱掉 →
    #    自启/更新后重启会**静默失败**（踩过一次：更新完桌宠没回来、残留没人清）。
    # .cmd 反而要 UTF-8：脚本里先 `chcp 65001`，之后的行按 UTF-8 解析（含中文路径）✓
    with open(os.path.join(d, "launch_hidden.vbs"), "w",
              encoding="utf-16", newline="\r\n") as f:
        f.write(VBS)
    log("  launch_hidden.vbs（UTF-16）")
    for fn, content in (("安装.cmd", INSTALL_CMD), ("卸载.cmd", UNINSTALL_CMD),
                        ("README.txt", README)):
        with open(os.path.join(d, fn), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(content)
        log(f"  {fn}")
    return d


def make_zip(dist_dir):
    out = os.path.join(DIST, f"{NAME}-Windows-{VERSION}")
    log("  正在压缩…")
    p = shutil.make_archive(out, "zip", root_dir=os.path.dirname(dist_dir),
                            base_dir=os.path.basename(dist_dir))
    log(f"  ✅ {p}  ({os.path.getsize(p) / 1e6:.1f} MB)")
    return p


def summary(dist_dir):
    exe = os.path.join(dist_dir, f"{NAME}.exe")
    log("\n=== 产物 ===")
    log(f"  目录 : {dist_dir}")
    log(f"  exe  : {exe}  ({os.path.getsize(exe) / 1e6:.1f} MB)")
    total = 0
    for dp, _, fns in os.walk(dist_dir):
        for fn in fns:
            total += os.path.getsize(os.path.join(dp, fn))
    log(f"  整包 : {total / 1e6:.1f} MB")


def main():
    ap = argparse.ArgumentParser(description="打包 Windows 版")
    ap.add_argument("--no-zip", action="store_true", help="不压缩，只出目录")
    ap.add_argument("--no-icon", action="store_true")
    args = ap.parse_args()

    log("=" * 62)
    log(f"  打包 {NAME} · Windows · v{VERSION}")
    log("=" * 62)
    log(f"  源码 : {APP}")
    log(f"  产物 : {DIST}")

    log("\n[1/4] 裁剪并暂存素材")
    stage_files()

    log("\n[2/4] PyInstaller")
    run_pyinstaller(no_icon=args.no_icon)

    log("\n[3/4] 写配套文件")
    dist_dir = write_runtime_files()

    log("\n[4/4] 压缩")
    zp = None if args.no_zip else make_zip(dist_dir)

    summary(dist_dir)
    if zp:
        log(f"  zip  : {zp}")
    log("\n完成后请务必跑 packaging/verify_win.py 实测 exe 能否真的跑起来。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
