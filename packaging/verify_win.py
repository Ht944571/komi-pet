#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_win.py — 实测 dist 里打出来的 exe**能不能真的跑起来**
================================================================================
刻意**只做安全验证**：绝不执行 `--install` / `--uninstall` —— 那会动你真实的
`HKCU\\...\\Run`，把你现在开发环境那套自启项覆盖掉。自启命令行改由 `--check`
**打印出来**人工核对（`wb_setup.print_plan`）。

验证项
------
  A. `--check`   控制台输出正常、包内资源找得到、能打印将写入的配置
  B. 数据隔离    所有可写文件都落在 KOMIPET_DATA_DIR；**包目录一个字节都没被写脏**
  C. `--api`     起 HTTP 服务 → `/api/health` 通；数仓建在数据目录里（不是包内）
  D. `--pet`     能起出 `WBWhalePetClass` 窗口（会**临时让位**：先停掉正在跑的源码版桌宠，
                 守望会在几十秒内把它拉回来）

用法： <venv>/python.exe packaging/verify_win.py
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(ROOT, "dist", "古见同学桌宠")
EXE = os.path.join(DIST_DIR, "古见同学桌宠.exe")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail and not cond else ""))


def snapshot(root):
    """记录目录下所有文件的 (相对路径, 大小, mtime) —— 用于比对"有没有被写脏"。"""
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
                out[os.path.relpath(p, root)] = (st.st_size, int(st.st_mtime))
            except OSError:
                pass
    return out


def enum_pet_windows():
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, l):
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if c.value == "WBWhalePetClass" and u.IsWindowVisible(h):
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            found.append((h, pid.value, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    u.EnumWindows(cb, 0)
    return found


def kill_pet():
    """按窗口类名找到桌宠进程并结束（只针对 WBWhalePetClass 的属主）。"""
    k32 = ctypes.windll.kernel32
    n = 0
    for _h, pid, _r in enum_pet_windows():
        hp = k32.OpenProcess(1, False, pid)
        if hp:
            k32.TerminateProcess(hp, 0)
            n += 1
    return n


def main():
    print("=" * 62)
    print("  验证 Windows 打包产物")
    print("=" * 62)
    print(f"  exe  : {EXE}")
    if not os.path.isfile(EXE):
        print("  ❌ 找不到 exe，请先跑 packaging/build_win.py")
        return 1

    data_dir = tempfile.mkdtemp(prefix="komipet-verify-")
    env = dict(os.environ)
    env["KOMIPET_DATA_DIR"] = data_dir
    print(f"  数据 : {data_dir}（KOMIPET_DATA_DIR，**不碰**你真实的 %LOCALAPPDATA%）")
    before = snapshot(DIST_DIR)

    # ---------- A. --check ----------
    print("\n[A] --check（控制台 + 包内资源 + 配置预览）")
    p = subprocess.run([EXE, "--check"], env=env, capture_output=True, text=True,
                       errors="replace", timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        if line.strip():
            print("      " + line[:150])
    check("A1 --check 退出码为 0", p.returncode == 0, f"rc={p.returncode}")
    check("A2 打包形态被识别", "打包exe" in out)
    check("A3 写字帧序列已入包", "写字帧序列 assets/anim/ 就位" in out)
    check("A4 看板页面已入包", "看板页面 dashboard.html 就位" in out)
    check("A5 打印了自启命令行（未写入注册表）",
          "HKCU" in out and "KomiPetWhaleWatcher" in out)

    # ---------- B. 数据隔离 ----------
    print("\n[B] 数据隔离（可写文件必须落在数据目录）")
    after = snapshot(DIST_DIR)
    check("B1 包目录没被写脏", before == after,
          f"多了 {len(set(after) - set(before))} 个文件")
    check("B2 数据目录已创建", os.path.isdir(data_dir))
    check("B3 wb_paths.json 未在包内生成",
          not os.path.isfile(os.path.join(DIST_DIR, "wb_paths.json")))

    # ---------- C. --api ----------
    print("\n[C] --api（HTTP 服务 + 数仓落点）")
    port = 8802
    logp = os.path.join(data_dir, "api.log")
    f = open(logp, "wb")
    proc = subprocess.Popen([EXE, "--api", "--port", str(port)], env=env,
                            cwd=DIST_DIR, stdout=f, stderr=f,
                            stdin=subprocess.DEVNULL,
                            creationflags=0x00000008 | 0x08000000)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    health = None
    for _ in range(40):
        time.sleep(1)
        try:
            with op.open(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                health = json.loads(r.read().decode())
            break
        except Exception:
            pass
    check("C1 /api/health 可访问", health is not None)
    if health is not None:
        print(f"      health = {json.dumps(health, ensure_ascii=False)[:120]}")
    # 首页要触发采集建模（首次可能慢），只取一次、超时放宽
    html_ok, title = False, ""
    try:
        with op.open(f"http://127.0.0.1:{port}/", timeout=90) as r:
            body = r.read().decode("utf-8", "replace")
        html_ok = "<html" in body.lower()
        title = body[body.find("<title>"):body.find("</title>") + 8][:80]
    except Exception as e:
        title = f"{type(e).__name__}"
    check("C2 首页返回打包进去的 dashboard.html", html_ok, title)
    if title:
        print(f"      title = {title}")
    try:
        proc.kill()
    except Exception:
        pass
    time.sleep(1)
    db = os.path.join(data_dir, "wb_usage_dw.db")
    check("C3 数仓建在数据目录（包内没有）", os.path.isfile(db),
          f"{db} 存在={os.path.isfile(db)}")
    check("C4 包内 data/ 没有落数仓",
          not os.path.isfile(os.path.join(DIST_DIR, "_internal", "wb_usage",
                                          "data", "wb_usage_dw.db")))
    try:
        txt = open(logp, encoding="utf-8", errors="replace").read()
        errs = [l for l in txt.splitlines() if "Traceback" in l or "Error" in l]
        check("C5 API 日志无异常", not errs, errs[:1])
    except OSError:
        pass

    # ---------- D. --pet ----------
    print("\n[D] --pet（真实起一个桌宠窗口）")
    # ⚠️ 这里天生有竞态：杀掉桌宠后，**守望会立刻把它拉回来**，打包版就抢不到
    #    单实例锁（"已有桌宠实例在运行，本次启动退出"）—— 那是正确行为，不是 bug。
    #    所以要**重试**：每轮先杀干净、再启动，直到窗口真的属于我们起的那个 pid。
    petlog = os.path.join(data_dir, "pet.log")
    pf = open(petlog, "wb")
    pp, win, n_killed = None, [], 0
    for attempt in range(6):
        n_killed += kill_pet()
        time.sleep(1.5)
        pp = subprocess.Popen([EXE, "--pet"], env=env, cwd=DIST_DIR,
                              stdout=pf, stderr=pf, stdin=subprocess.DEVNULL,
                              creationflags=0x00000008 | 0x08000000)
        win = []
        for _ in range(8):
            time.sleep(1)
            win = [w for w in enum_pet_windows() if w[1] == pp.pid]
            if win:
                break
        if win:
            if attempt:
                print(f"      （第 {attempt + 1} 轮才抢到实例锁 —— 守望抢跑属正常竞态）")
            break
        pp.kill()
        time.sleep(1)
    print(f"      临时停掉正在跑的桌宠 {n_killed} 个（守望会在几十秒内把源码版拉回来）")
    check("D1 桌宠窗口已出现", bool(win), f"看到 {len(win)} 个")
    if win:
        print(f"      窗口={win[0][2]}  pid={win[0][1]}")
    check("D2 窗口尺寸符合布局（宽>100 且高>200）",
          bool(win) and win[0][2][2] > 100 and win[0][2][3] > 200)
    try:
        pp.kill()
    except Exception:
        pass
    time.sleep(1)
    try:
        txt = open(petlog, encoding="utf-8", errors="replace").read()
        bad = [l for l in txt.splitlines()
               if "Traceback" in l or "错误" in l or "失败" in l]
        check("D3 桌宠启动日志无异常", not bad, bad[:1])
        if txt.strip():
            print("      启动日志：")
            for l in txt.splitlines()[-6:]:
                print("        " + l[:140])
    except OSError:
        pass
    check("D4 桌宠运行后包目录仍没被写脏", before == snapshot(DIST_DIR))

    # ---------- 收尾 ----------
    print("\n=== 总结 ===")
    print(f"  PASS {len(PASS)} / FAIL {len(FAIL)}")
    for f_ in FAIL:
        print(f"    FAIL: {f_}")
    shutil.rmtree(data_dir, ignore_errors=True)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
