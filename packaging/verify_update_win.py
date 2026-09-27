#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_update_win.py — 自更新的**端到端实测**
================================================================================
在临时目录里模拟一台"客户机"，走完真实流程：

    解压旧版 → 指向更新源 → 检查 → 下载/校验/解压/替换 → 重启 → 版本真的变了

全程**不碰**你机器上正在跑的那只桌宠，也**不动**你真实的 `%LOCALAPPDATA%\\KomiPet`：

  · 客户机装在临时目录，数据目录用 `KOMIPET_DATA_DIR` 引到临时目录
  · 结束"同名进程"时按**可执行文件名**匹配 —— 你开发机上的桌宠跑在 `pythonw.exe` 下，
    名字不同，不会被误杀

用法：
    <python> packaging/verify_update_win.py --old <旧版zip> --new <新版zip>
    <python> packaging/verify_update_win.py --auto      # 自动挑 dist/ 里最新的两个版本
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, os.path.join(ROOT, "app"))
import wb_version as V          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if not cond else ""))


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def find_zips():
    if not os.path.isdir(DIST):
        return []
    out = [os.path.join(DIST, f) for f in os.listdir(DIST)
           if f.startswith(V.APP_NAME + "-Windows-") and f.endswith(".zip")]

    def ver(p):
        base = os.path.basename(p)
        s = base[len(V.APP_NAME) + len("-Windows-"):-4]
        return V.as_tuple(s)

    return sorted(out, key=ver)


def extract_to(zip_path, dest):
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    for name in os.listdir(dest):
        cand = os.path.join(dest, name)
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, V.EXE_NAME)):
            return cand
    raise SystemExit(f"❌ 解压后找不到 {V.EXE_NAME}")


def run(exe, args, env, timeout=180):
    p = subprocess.run([exe] + args, env=env, capture_output=True, text=True,
                       errors="replace", timeout=timeout,
                       cwd=os.path.dirname(exe))
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def version_of(exe, env):
    """跑 --check 从输出里抠出版本号。"""
    try:
        rc, out = run(exe, ["--check"], env, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    for line in out.splitlines():
        if "版本 v" in line:
            return line.split("版本 v")[-1].strip()
    return None


def kill_by_image(name):
    """按可执行文件名结束进程（**只匹配打包版**，开发机的 pythonw 不受影响）。"""
    k32 = ctypes.windll.kernel32
    TH32CS_SNAPPROCESS = 2
    INVALID = ctypes.c_void_p(-1).value

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID or not snap:
        return 0
    n = 0
    try:
        e = PE32()
        e.dwSize = ctypes.sizeof(PE32)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.decode("mbcs", "replace").lower() == name.lower():
                h = k32.OpenProcess(1, False, int(e.th32ProcessID))
                if h:
                    k32.TerminateProcess(h, 0)
                    k32.CloseHandle(h)
                    n += 1
            ok = k32.Process32Next(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", help="旧版 zip")
    ap.add_argument("--new", help="新版 zip")
    ap.add_argument("--auto", action="store_true", help="自动取 dist/ 里最新的两个版本")
    ap.add_argument("--keep", action="store_true", help="保留临时目录（排查用）")
    args = ap.parse_args()

    if args.auto or not (args.old and args.new):
        zs = find_zips()
        if len(zs) < 2:
            print(f"❌ dist/ 里至少要有两个版本的 zip（现在 {len(zs)} 个）。"
                  f"先改 app/wb_version.py 的 VERSION 再 build 一次。")
            return 1
        old_zip, new_zip, _ = zs[-2], zs[-1], None
    else:
        old_zip, new_zip = args.old, args.new
    for z in (old_zip, new_zip):
        if not os.path.isfile(z):
            print(f"❌ 找不到 {z}")
            return 1

    old_ver = V.as_tuple(os.path.basename(old_zip).split("-Windows-")[-1][:-4])
    new_ver = V.as_tuple(os.path.basename(new_zip).split("-Windows-")[-4:][0][:-4]) \
        if False else V.as_tuple(os.path.basename(new_zip).split("-Windows-")[-1][:-4])
    old_s = ".".join(map(str, old_ver))
    new_s = ".".join(map(str, new_ver))

    print("=" * 62)
    print("  自更新端到端实测")
    print("=" * 62)
    print(f"  旧版: {os.path.basename(old_zip)}  (v{old_s})")
    print(f"  新版: {os.path.basename(new_zip)}  (v{new_s})")

    tmp = tempfile.mkdtemp(prefix="komi-upd-")
    client = extract_to(old_zip, os.path.join(tmp, "client"))
    data = os.path.join(tmp, "data")
    src = os.path.join(tmp, "source")
    os.makedirs(data, exist_ok=True)
    os.makedirs(src, exist_ok=True)
    exe = os.path.join(client, V.EXE_NAME)
    env = dict(os.environ)
    env["KOMIPET_DATA_DIR"] = data
    print(f"  客户机: {client}")
    print(f"  数据  : {data}")

    # 数据目录放个哨兵：更新**不该**动它
    sentinel = os.path.join(data, "SETTINGS_SENTINEL.txt")
    open(sentinel, "w", encoding="utf-8").write("别动我")
    shutil.copy2(new_zip, os.path.join(src, os.path.basename(new_zip)))
    man_path = os.path.join(src, V.MANIFEST_NAME)

    def write_manifest(sha):
        json.dump({"version": new_s, "notes": "端到端测试",
                   "win": {"url": os.path.basename(new_zip), "sha256": sha,
                           "size": os.path.getsize(new_zip)}},
                  open(man_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # ---------- 0. 起点 ----------
    print("\n[0] 起点")
    v0 = version_of(exe, env)
    check("0.1 客户机装的是旧版", v0 == old_s, f"读到 v{v0}，期望 v{old_s}")
    rc, out = run(exe, ["--set-update-source", src], env, timeout=60)
    check("0.2 设置更新源成功", rc == 0 and "已写入" in out, out.strip()[:80])

    # ---------- 1. 负向：sha256 不符必须拒绝 ----------
    print("\n[1] 负向用例：sha256 不符必须拒绝")
    write_manifest("0" * 64)
    rc, out = run(exe, ["--update"], env, timeout=300)
    check("1.1 sha256 不符 → --update 失败", rc != 0, f"rc={rc}")
    check("1.2 报错里点明校验失败", "sha256" in out.lower() or "校验" in out, out.strip()[-120:])
    check("1.3 版本没被改动", version_of(exe, env) == old_s)

    # ---------- 2. 检查更新 ----------
    print("\n[2] 检查更新")
    write_manifest(sha256_file(new_zip))
    rc, out = run(exe, ["--check-update"], env, timeout=120)
    check("2.1 检查到新版本", f"UPDATE_AVAILABLE={new_s}" in out, out.strip()[-160:])

    # ---------- 3. 执行更新 ----------
    print("\n[3] 执行更新（下载 → 校验 → 解压 → 交给新版本替换 → 重启）")
    rc, out = run(exe, ["--update"], env, timeout=300)
    check("3.1 --update 正常返回", rc == 0, f"rc={rc}  {out.strip()[-160:]}")
    logp = os.path.join(data, "update", "update.log")
    check("3.2 写了更新日志", os.path.isfile(logp))
    if os.path.isfile(logp):
        txt = open(logp, encoding="utf-8", errors="replace").read()
        for line in txt.splitlines()[-6:]:
            print("      " + line[:130])

    # ---------- 4. 等替换完成 ----------
    print("\n[4] 等新版本落地（最多 90 秒）")
    v1, t0 = None, time.time()
    while time.time() - t0 < 90:
        v1 = version_of(exe, env)
        if v1 == new_s:
            break
        time.sleep(3)
    check("4.1 客户机已变成新版", v1 == new_s, f"读到 v{v1}，期望 v{new_s}")
    check("4.2 更新耗时合理（<90s）", v1 == new_s)

    # ---------- 5. 数据与清理 ----------
    print("\n[5] 数据保留与现场清理")
    check("5.1 用户数据没被更新动过", os.path.isfile(sentinel)
          and open(sentinel, encoding="utf-8").read() == "别动我")
    # 清理要等"更新进程"退出（它自己就占着 staged 里的 exe），所以这里轮询等
    staged = os.path.join(data, "update", "staged")
    gone, t0 = False, time.time()
    while time.time() - t0 < 40:
        if (not os.path.isdir(staged)) or (not os.listdir(staged)):
            gone = True
            break
        time.sleep(2)
    check("5.2 更新残留已被清理（40s 内）", gone, f"仍存在 {staged}")
    # 失败时把替换进程的日志打出来（它跑在新版本里，最容易看出卡在哪一步）
    alog = os.path.join(data, "update", "apply.log")
    if os.path.isfile(alog) and (not gone or v1 != new_s):
        print("      ---- apply.log ----")
        for line in open(alog, encoding="utf-8", errors="replace").read().splitlines()[-14:]:
            print("      " + line[:130])
    check("5.3 包内仍是新版 exe",
          os.path.getsize(os.path.join(client, V.EXE_NAME)) > 100000)

    # ---------- 收尾 ----------
    print("\n[6] 收尾（只结束**打包版**的同名进程，不动你开发机上 pythonw 那只）")
    print(f"      结束了 {kill_by_image(V.EXE_NAME)} 个测试进程")
    time.sleep(1)
    if args.keep:
        print(f"      （--keep）临时目录保留在 {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== 总结 ===")
    print(f"  PASS {len(PASS)} / FAIL {len(FAIL)}")
    for f_ in FAIL:
        print(f"    FAIL: {f_}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
