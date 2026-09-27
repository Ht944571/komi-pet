#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_update.py — 自更新（检查 / 下载 / 校验 / 替换 / 重启）
================================================================================
给"以后还会一直发新版"的绿色包用。设计上守四条：

1. **失败绝不影响正在跑的桌宠** —— 全程有超时与兜底；任何一步出错都只是"这次没更新成"，
   桌宠照常跑、设置与数仓一个字节都不动。
2. **不需要任何服务器** —— 更新源就是一个 `update.json` 清单的地址，放
   HTTP 静态站 / 局域网共享 / 本地文件夹（`file://`）都行。清单里的包地址可以是相对路径。
3. **默认零联网** —— 没配更新源就完全不发请求（隐私安全）。配了之后才自动检查。
4. **可自证** —— 下载后按清单里的 sha256 校验；版本号比较走 `wb_version`；
   更新完由新版本自己覆盖老版本并重启守望。

⚠️ 替换为什么不由当前进程做
--------------------------
正在运行的 exe 与 `_internal/*.dll`、`*.pyd` 都被 Windows 锁住，**当前进程改不了自己**。
所以流程是：下载 → 解压到 `<数据目录>/update/staged/` → **用"新版本的 exe"** 起一个
`--apply-update` 进程（它跑在 staged 目录，不受 install 目录的锁影响）→ 当前程序退出。
新进程负责等旧实例退干净、覆盖、重启守望。这样连"覆盖逻辑"用的都是新代码。

配套的文件布局
-------------
```
<数据目录>/update/
    update.log         全过程日志（出问题先看它）
    update_source.txt  <-- 更新源（--set-update-source 写入；一行 URL 或路径）
    download.zip       最近一次下载的包（起不来时可手动解压回滚）
    staged/            解压出来的新版本（跑完会提示清理）
    pending_cleanup.txt 下次启动时删掉的目录
```
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from urllib.parse import urlparse, urljoin, unquote

import wb_runtime as RT
import wb_version as V

TIMEOUT_META = 12          # 取清单超时（秒）
TIMEOUT_DL = 180           # 下载包超时
MAX_ZIP_BYTES = 400 * 1024 * 1024     # 包体积上限，防呆
KILL_GRACE_S = 20.0        # 等旧实例退出的最长时间

SOURCE_FILE = "update_source.txt"
STAGED_NAME = "staged"
ZIP_NAME = "download.zip"
CLEANUP_MARK = "pending_cleanup.txt"


class UpdateError(Exception):
    """更新过程中的可预期失败（网络/清单/校验），调用方按"这次没更新成"处理即可。"""


# ============================================================
# 1. 路径与日志
# ============================================================

def update_dir():
    d = os.path.join(RT.data_dir(), "update")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(update_dir(), "update.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ============================================================
# 2. 更新源
# ============================================================

def source():
    """更新源。优先级：环境变量 > wb_paths.json 的 update_url > 数据目录/exe 旁的 txt。"""
    s = (os.environ.get("KOMIPET_UPDATE_URL") or "").strip()
    if s:
        return s
    for cfg_path in (RT.paths_config_path(), os.path.join(RT.APP_DIR, "wb_paths.json")):
        try:
            if cfg_path and os.path.isfile(cfg_path):
                with open(cfg_path, encoding="utf-8") as f:
                    u = (json.load(f) or {}).get("update_url")
                if u:
                    return str(u).strip()
        except Exception:
            pass
    for p in (os.path.join(RT.EXE_DIR, SOURCE_FILE),
              os.path.join(RT.data_dir(), SOURCE_FILE)):
        try:
            if os.path.isfile(p):
                t = open(p, encoding="utf-8").read().strip()
                if t:
                    return t
        except OSError:
            pass
    return ""


def set_source(url):
    """写下更新源（`--set-update-source`）。返回落盘路径。"""
    p = os.path.join(RT.data_dir(), SOURCE_FILE)
    with open(p, "w", encoding="utf-8") as f:
        f.write((url or "").strip() + "\n")
    return p


# ============================================================
# 3. 取数（http/https/file/本地路径/UNC 通吃）
# ============================================================

def _is_local_host(host):
    h = (host or "").lower()
    return (h in ("localhost", "127.0.0.1", "::1")
            or h.startswith(("192.168.", "10.", "172.", "169.254."))
            or h.endswith(".local"))


def _open(loc, timeout):
    """统一打开。http(s) 走系统代理；本地主机/局域网直连（代理会把内网也带走）。"""
    if loc.startswith(("http://", "https://")):
        p = urlparse(loc)
        if _is_local_host(p.hostname):
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            op = urllib.request.build_opener()
        req = urllib.request.Request(loc, headers={"User-Agent": f"KomiPet/{V.VERSION}"})
        return op.open(req, timeout=timeout)
    path = loc
    if path.startswith("file://"):
        path = urllib.request.url2pathname(urlparse(loc).path)
    path = unquote(path)
    return open(path, "rb")


def fetch_bytes(loc, timeout=TIMEOUT_META, limit=MAX_ZIP_BYTES):
    with _open(loc, timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError(f"文件过大（>{limit // 1048576}MB），已拒绝")
    return data


def _resolve(base, rel):
    """清单里的相对路径按"清单所在位置"解析（静态站/本地目录都能这么用）。"""
    if not rel:
        raise UpdateError("清单里没有包地址")
    if rel.startswith(("http://", "https://", "file://")) or os.path.isabs(rel):
        return rel
    if base.startswith(("http://", "https://")):
        return urljoin(base if base.endswith("/") else base + "/", rel)
    return os.path.join(os.path.dirname(base), rel)


# ============================================================
# 4. 清单
# ============================================================

def load_manifest(src=None):
    """取并校验清单。`src` 可以是清单文件本身，也可以是一个目录（自动拼 update.json）。"""
    src = (src or source()).strip()
    if not src:
        raise UpdateError("未配置更新源（用 --set-update-source 设一个，或设环境变量 "
                          "KOMIPET_UPDATE_URL）")
    loc = src
    if not loc.lower().endswith(".json"):
        loc = loc.rstrip("/\\") + "/" + V.MANIFEST_NAME
    try:
        raw = fetch_bytes(loc, timeout=TIMEOUT_META, limit=2 * 1024 * 1024)
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"取更新清单失败：{type(e).__name__} {e}")
    try:
        m = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise UpdateError(f"更新清单不是合法 JSON：{e}")
    if not (m.get("version") or "").strip():
        raise UpdateError("更新清单缺少 version 字段")
    plat = m.get("win") or {}
    m["_url"] = _resolve(loc, plat.get("url") or m.get("url"))
    m["_sha256"] = (plat.get("sha256") or m.get("sha256") or "").strip().lower()
    m["_size"] = plat.get("size") or m.get("size") or 0
    m["_source_loc"] = loc
    return m


def check(src=None):
    """检查有没有新版。返回 dict（不抛异常）：
    {available, manifest, msg, src}"""
    try:
        src_ = (src or source()).strip()
        if not src_:
            return {"available": False, "manifest": None,
                    "msg": "未配置更新源（默认不联网）", "src": ""}
        m = load_manifest(src_)
        if V.is_newer(m["version"]):
            return {"available": True, "manifest": m, "src": src_,
                    "msg": f"发现新版本 v{m['version']}（当前 {V.display()}）"}
        return {"available": False, "manifest": m, "src": src_,
                "msg": f"已是最新（{V.display()}）"}
    except UpdateError as e:
        return {"available": False, "manifest": None, "src": src or "",
                "msg": str(e)}
    except Exception as e:                                   # 兜底：绝不把异常抛给 UI
        return {"available": False, "manifest": None, "src": src or "",
                "msg": f"检查更新失败：{type(e).__name__} {e}"}


# ============================================================
# 5. 下载与解压
# ============================================================

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(m, progress=None):
    """把新版本下到 <数据目录>/update/download.zip 并校验 sha256。返回 zip 路径。"""
    dst = os.path.join(update_dir(), ZIP_NAME)
    log(f"开始下载 {m['_url']}")
    try:
        with _open(m["_url"], TIMEOUT_DL) as r, open(dst, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0) if hasattr(r, "headers") else 0
            got = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if got > MAX_ZIP_BYTES:
                    raise UpdateError("包体积超出上限，已中止")
                if progress:
                    try:
                        progress(got, total)
                    except Exception:
                        pass
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"下载失败：{type(e).__name__} {e}")
    size = os.path.getsize(dst)
    log(f"下载完成 {size / 1e6:.1f} MB → {dst}")
    if m["_sha256"]:
        got = _sha256_file(dst)
        if got != m["_sha256"]:
            os.remove(dst)
            raise UpdateError(f"sha256 校验失败（期望 {m['_sha256'][:12]}…，实得 {got[:12]}…）")
        log("sha256 校验通过")
    else:
        log("⚠️ 清单里没写 sha256，跳过校验（建议发布时补上）")
    return dst


def extract(zip_path):
    """解压到 staged/，返回**含 exe 的那一层**目录。"""
    staged = os.path.join(update_dir(), STAGED_NAME)
    shutil.rmtree(staged, ignore_errors=True)
    os.makedirs(staged, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                raise UpdateError(f"压缩包损坏（{bad}）")
            z.extractall(staged)
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"解压失败：{type(e).__name__} {e}")
    # zip 里通常有一层顶层目录（古见同学桌宠/…），找到 exe 所在层
    root = staged
    if not os.path.isfile(os.path.join(root, V.EXE_NAME)):
        for name in os.listdir(staged):
            cand = os.path.join(staged, name)
            if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, V.EXE_NAME)):
                root = cand
                break
    if not os.path.isfile(os.path.join(root, V.EXE_NAME)):
        raise UpdateError(f"包结构不对：里面找不到 {V.EXE_NAME}")
    log(f"解压完成 → {root}")
    return root


# ============================================================
# 6. 应用（在新版本的 exe 里跑）
# ============================================================

def _pids_by_image(name, exclude_self=True):
    """按**可执行文件名**枚举进程 PID（纯 ctypes，零依赖）。"""
    import ctypes
    import ctypes.wintypes as wt
    k32 = ctypes.windll.kernel32
    TH32CS_SNAPPROCESS = 0x00000002
    MAX_PATH = 260
    INVALID = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_char * MAX_PATH)]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID or not snap:
        return []
    out = []
    self_pid = os.getpid()
    try:
        e = PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            nm = e.szExeFile.decode("mbcs", "replace")
            if nm.lower() == name.lower() and not (exclude_self and e.th32ProcessID == self_pid):
                out.append(int(e.th32ProcessID))
            ok = k32.Process32Next(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def _kill(pid):
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(1, False, pid)          # PROCESS_TERMINATE
    if h:
        k32.TerminateProcess(h, 0)
        k32.CloseHandle(h)
        return True
    return False


def _copy_tree(src, dst, skip_top=("data", "portable.txt")):
    """把新版覆盖到安装目录；并删掉安装目录里"新版已经没有"的文件（含 _internal）。"""
    os.makedirs(dst, exist_ok=True)
    n_copy = 0
    for dirpath, dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        if rel == ".":
            dirnames[:] = [d for d in dirnames if d not in skip_top]
        for fn in filenames:
            if rel == "." and fn in skip_top:
                continue
            s = os.path.join(dirpath, fn)
            d = os.path.join(dst, rel, fn) if rel != "." else os.path.join(dst, fn)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            last = None
            for _ in range(5):                  # 刚被杀的进程可能还占着一两秒
                try:
                    shutil.copy2(s, d)
                    last = None
                    break
                except OSError as e:
                    last = e
                    time.sleep(0.6)
            if last:
                raise UpdateError(f"覆盖失败（文件被占用？）：{d}  {last}")
            n_copy += 1
    # 清理陈旧文件（安装目录有、新版没有）
    n_del = 0
    for dirpath, dirnames, filenames in os.walk(dst):
        rel = os.path.relpath(dirpath, dst)
        if rel == "." or rel.split(os.sep)[0] in skip_top:
            continue
        for fn in filenames:
            relf = os.path.relpath(os.path.join(dirpath, fn), dst)
            if not os.path.exists(os.path.join(src, relf)):
                try:
                    os.remove(os.path.join(dirpath, fn))
                    n_del += 1
                except OSError:
                    pass
    return n_copy, n_del


def relaunch(install_dir):
    """重启守望（它会拉起桌宠；桌宠再守护看板）。优先走隐藏启动，避免闪黑窗。"""
    vbs = os.path.join(install_dir, RT.VBS_NAME)
    try:
        if os.path.isfile(vbs):
            subprocess.Popen(["wscript.exe", "//nologo", vbs, "watcher"],
                             cwd=install_dir, stdin=subprocess.DEVNULL,
                             **RT.detach_kwargs())
            log("已通过 launch_hidden.vbs 重启守望")
            return True
        exe = os.path.join(install_dir, V.EXE_NAME)
        subprocess.Popen([exe, "--watcher"], cwd=install_dir,
                         stdin=subprocess.DEVNULL, **RT.detach_kwargs())
        log("已直接重启守望（未找到 vbs）")
        return True
    except Exception as e:
        log(f"❌ 重启守望失败：{type(e).__name__} {e}（下次登录会自启，或手动双击 exe）")
        return False


def mark_cleanup(path):
    try:
        with open(os.path.join(update_dir(), CLEANUP_MARK), "w", encoding="utf-8") as f:
            f.write(path)
    except OSError:
        pass


def cleanup_pending():
    """启动时清理上一轮更新留下的 staged 残留。

    ⚠️ **一次往往删不干净**：更新进程刚从 staged 里跑起来，它退出要一两秒，
    这期间文件还锁着。所以重试几轮；**只有真删掉了才移除标记**，
    否则留到下次启动继续删（宁可多试几次，也别在用户盘上留垃圾）。
    """
    mark = os.path.join(update_dir(), CLEANUP_MARK)
    if not os.path.isfile(mark):
        return False
    try:
        target = open(mark, encoding="utf-8").read().strip()
    except OSError:
        return False
    if not target or not os.path.isdir(target):
        try:
            os.remove(mark)
        except OSError:
            pass
        return True
    for _ in range(6):                       # 约 12 秒
        shutil.rmtree(target, ignore_errors=True)
        if not os.path.isdir(target):
            try:
                os.remove(mark)
            except OSError:
                pass
            log(f"已清理上一轮更新残留：{target}")
            return True
        time.sleep(2.0)
    log(f"⚠️ 更新残留暂时删不掉（文件仍被占用），下次启动会再试：{target}")
    return False


def apply_update(staged_root, install_dir):
    """**在 staged 里的新版本 exe 中执行**：等旧实例退干净 → 覆盖 → 重启。"""
    log("=" * 50)
    log(f"开始应用更新：{staged_root}  →  {install_dir}")
    # ① 停掉安装目录里的所有实例（桌宠 / 守望 / 看板 API，同名 exe）
    deadline = time.time() + KILL_GRACE_S
    killed = set()
    while time.time() < deadline:
        pids = _pids_by_image(V.EXE_NAME)
        if not pids:
            break
        for pid in pids:
            if pid not in killed:
                log(f"结束旧实例 pid={pid}")
                killed.add(pid)
            _kill(pid)
        time.sleep(0.5)
    time.sleep(1.0)                                  # 让文件句柄真正释放
    if _pids_by_image(V.EXE_NAME):
        log("⚠️ 仍有同名进程未退出，继续尝试覆盖")
    # ② 覆盖
    try:
        n_copy, n_del = _copy_tree(staged_root, install_dir)
    except UpdateError as e:
        log(f"❌ 应用失败：{e}")
        log("   （若安装目录只读，请把整个目录换到可写位置后重试）")
        return False
    log(f"覆盖完成：写入 {n_copy} 个文件，清理陈旧文件 {n_del} 个")
    # ③ 重启
    ok = relaunch(install_dir)
    log(f"更新完成（v{V.VERSION}），重启守望：{'成功' if ok else '失败'}")
    # 标记**整个 staged/ 容器**（不是里面那层）——下次启动会把解压产物一并清掉
    mark_cleanup(os.path.join(update_dir(), STAGED_NAME))
    return True


def launch_apply(staged_root, install_dir):
    """用**新版本**的 exe 起一个 --apply-update 进程（跑在 staged 里，不受锁影响），然后调用方应退出。"""
    exe = os.path.join(staged_root, V.EXE_NAME)
    if not os.path.isfile(exe):
        raise UpdateError(f"staged 里找不到 {V.EXE_NAME}")
    logf = open(os.path.join(update_dir(), "apply.log"), "ab")
    cmd = [exe, "--apply-update", "--from", staged_root, "--to", install_dir]
    try:
        p = subprocess.Popen(cmd, cwd=staged_root, stdin=subprocess.DEVNULL,
                             stdout=logf, stderr=logf, **RT.detach_kwargs())
    except Exception as e:
        raise UpdateError(f"启动更新进程失败：{type(e).__name__} {e}")
    log(f"已启动更新进程 pid={p.pid}（本进程需要退出，好让文件解锁）")
    return p


# ============================================================
# 7. 一站式入口（命令行 / 菜单都用它）
# ============================================================

def install_dir():
    """要更新的目录 = exe 所在目录。"""
    return RT.EXE_DIR


def run_apply_new(src=None, progress=None):
    """检查 → 下载 → 解压 → 起更新进程。成功返回 manifest；失败抛 UpdateError。"""
    info = check(src)
    if not info["available"]:
        raise UpdateError(info["msg"])
    m = info["manifest"]
    zip_path = download(m, progress=progress)
    staged = extract(zip_path)
    launch_apply(staged, install_dir())
    return m
