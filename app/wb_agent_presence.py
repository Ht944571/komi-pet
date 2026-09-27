# -*- coding: utf-8 -*-
"""
wb_agent_presence.py — 哪些 agent 现在「在用」？（联动关闭的宿主判据）
====================================================================

判定口径（重要，和「跟随前台窗口」是两个不同的信号）
----------------------------------------------------
  · **活跃（本模块）** = 该 agent **在运行**。桌宠据此判断"宿主还在不在"（联动关闭）。
    语义是"我的生活里有它"，应当是**稳定**的——开着一整天就一整天都在，
    不能因为切个窗口就闪来闪去（否则联动关闭会误触发）。

  · **聚焦（另说）** = 前台窗口属于谁。决定徽章/领结等**即时**标识，可以频繁变。

这两件事分开，是为了让"谁在跑"这个判据保持稳定；
高频变化只交给轻量的元素去表达。详见 docs/桌宠跨Agent体验设计.md。

实现约束
--------
  ⚠️ 探测**必须跑在后台 daemon 线程**：进程快照 + 端口探测都有系统调用开销，
  放进 Win32 消息循环会拖慢拖动/点击（本项目吃过这个亏——曾把带超时的网络请求
  放进 timer tick，消息循环每秒被卡死半秒）。
  本模块对外只暴露 `active()` 读缓存，**读取是零开销的**。

声明式：探针写在 assets/_agents.json 的 `presence` 字段，
新增 agent 只改 json，不改这里的代码。
"""

import os
import socket
import threading
import time

try:
    import ctypes
    import ctypes.wintypes as wt
    _HAS_WIN = (os.name == "nt")
except Exception:                                   # pragma: no cover
    _HAS_WIN = False

import json

_HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_INTERVAL = 2.0          # 探测周期（秒）；"在不在跑"是长期状态，2s 足够
PORT_TIMEOUT = 0.15             # 端口 connect 超时（本机回环，取小值即可）

# ---------------------------------------------------------------------------
# 三级状态来源（T1：按可靠性降级，每个 agent 取**最强可用**的一层）
#   L1 hooks      agent 主动回调（心跳文件 mtime）  → "本轮活跃"（最可信）
#   L2 日志活动度  会话日志最近写入时间              → "忙碌"（不需 agent 配合）
#   L3 进程/端口   判断"活着"                       → 只能表达"在跑"（已实现）
# 注意：L3 对 CLI 类 agent（寄生在 node/python 上）基本无效——L2 正是补这个洞。
# ---------------------------------------------------------------------------

LEVEL_HOOK = 1
LEVEL_LOG = 2
LEVEL_PROC = 3

HOOK_WINDOW_S = 90          # L1：hook 心跳多久内算"本轮活跃"
LOG_ACTIVE_WINDOW_S = 45    # L2：日志最近写入多久内算"忙碌"（流式写入间隔远小于此）
LOG_PROBE_TTL_S = 10.0      # L2 的 glob/mtime 扫描缓存周期（** 递归遍历有成本，不能每帧跑）

_log_cache = {}             # {patterns_tuple: (checked_at, age_or_None)}


def _expand(pat):
    return os.path.expanduser(str(pat))


def log_activity_age(log_paths, now=None):
    """一组日志 glob 模式中**最近一次写入**距今的秒数；无法判定返回 None。

    - 支持 `**` 递归（glob recursive=True）与 `~` 展开；
    - 结果按 patterns 元组缓存 LOG_PROBE_TTL_S 秒（探测在后台线程跑，但仍
      不值得每 2s 全盘扫一遍）；
    - 一个文件都没匹配到 → None（"无法判定"，调用方按三值语义保持不动）。
    """
    now = time.time() if now is None else now
    pats = tuple(_expand(p) for p in (log_paths or []) if str(p).strip())
    if not pats:
        return None
    hit = _log_cache.get(pats)
    if hit and now - hit[0] < LOG_PROBE_TTL_S:
        return hit[1]
    import glob as _glob
    newest = None
    try:
        for pat in pats:
            for f in _glob.glob(pat, recursive=True):
                try:
                    m = os.stat(f).st_mtime
                except OSError:
                    continue
                if newest is None or m > newest:
                    newest = m
    except Exception:
        _log_cache[pats] = (now, None)
        return None
    age = (now - newest) if newest is not None else None
    _log_cache[pats] = (now, age)
    return age


def activity_sources(agents=None):
    """从登记册取有信号来源的启用项：{key: {"procs","ports","hook_files","log_paths"}}。

    hook_files 是 L1 的落点（agent 的 hook 把心跳写到文件，本层只看 mtime），
    登记册 presence 里可选声明；未声明的 agent 自动落到 L2/L3。
    log_paths 只在 caps.log 为真时参与（声明了却没有日志能力的条目不空转）。
    """
    try:
        import wb_agent_registry as REG
    except ImportError:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import wb_agent_registry as REG
    try:
        agents = REG.all_agents(include_disabled=False) if agents is None else agents
    except Exception:
        return {}
    out = {}
    for key, spec in (agents or {}).items():
        if not spec.get("enabled", True):
            continue
        pr = spec.get("presence") or {}
        caps = spec.get("caps") or {}
        procs = [str(p).lower() for p in (pr.get("procs") or [])]
        ports = []
        for p in (pr.get("ports") or []):
            try:
                ports.append(int(p))
            except (TypeError, ValueError):
                pass
        hooks = [_expand(p) for p in (pr.get("hook_files") or [])]
        logs = [str(p) for p in (spec.get("log_paths") or [])] \
            if caps.get("log") else []
        if procs or ports or hooks or logs:
            out[key] = {"procs": procs, "ports": ports,
                        "hook_files": hooks, "log_paths": logs}
    return out


def detect_detail(specs=None, now=None):
    """探测一轮，返回 {key: {"level": int, "age": float|None}}（可单测，不依赖线程）。

    level = 该 agent **最强可用**信号层（1=hook / 2=日志忙碌 / 3=进程或端口在跑）；
    不在返回里 = 本轮不活跃。age 仅 L2 有意义（日志静默秒数）。
    三值语义：探测异常的项保持"不在返回里"，绝不含糊成活跃或不活跃。
    """
    now = time.time() if now is None else now
    specs = activity_sources() if specs is None else specs
    if not specs:
        return {}

    need_procs = any(s["procs"] for s in specs.values())
    procs = _running_procs() if need_procs else set()

    out = {}
    for key, spec in specs.items():
        level = None
        age = None
        # L1 hooks：心跳文件 mtime
        for f in spec.get("hook_files") or []:
            try:
                if now - os.stat(f).st_mtime <= HOOK_WINDOW_S:
                    level = LEVEL_HOOK
                    break
            except OSError:
                continue
        # L2 日志活动度
        if level is None and spec.get("log_paths"):
            age = log_activity_age(spec["log_paths"], now)
            if age is not None and age <= LOG_ACTIVE_WINDOW_S:
                level = LEVEL_LOG
        # L3 进程/端口
        if level is None:
            if spec["procs"] and any(n in procs for n in spec["procs"]):
                level = LEVEL_PROC
            elif spec["ports"] and any(_port_open(pt) for pt in spec["ports"]):
                level = LEVEL_PROC
        if level is not None:
            out[key] = {"level": level, "age": age}
    return out


# ---------------------------------------------------------------------------
# 声明式探针
# ---------------------------------------------------------------------------

def load_probes():
    """从 **agent 登记册**读 {agent_key: {"procs": [...], "ports": [...]}}。

    登记册是唯一来源（`assets/_agents.json`），
    见 `wb_agent_registry`。**只返回 enabled=true 的项** —— 用户在设置里关掉的
    agent 不该再被探测（这是"用户自主选择接入哪些 agent"的落点）。
    """
    try:
        import wb_agent_registry as REG
    except ImportError:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import wb_agent_registry as REG
    try:
        return REG.probes()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 探测原语（纯 ctypes，无子进程）
# ---------------------------------------------------------------------------

def _running_procs():
    """当前进程名集合（小写）。非 Windows 返回空集。"""
    if not _HAS_WIN:
        return set()
    names = set()
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return names
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if k32.Process32FirstW(snap, ctypes.byref(e)):
            while True:
                names.add((e.szExeFile or "").lower())
                if not k32.Process32NextW(snap, ctypes.byref(e)):
                    break
    finally:
        k32.CloseHandle(ctypes.c_void_p(snap))
    return names


def _port_open(port):
    """本机回环端口是否有服务在听。

    用 connect_ex 而不是解析 TCP 表：实现简单、无权限要求，
    且本机回环 connect 极快（配 0.15s 超时，失败也几乎不阻塞）。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(PORT_TIMEOUT)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def detect_once(probes=None):
    """探测一轮，返回活跃 agent key 集合（可单测，不依赖线程）。

    probes=None → 走**完整三级降级**（登记册驱动，含 L2 日志活动度）；
    传入旧式 {key: {"procs": [...], "ports": [...]}} → 只做 L3
    （兼容既有调用与测试——显式给探针就是"我只认这些探针"）。
    """
    if probes is None:
        return {k for k, v in detect_detail().items() if v["level"] >= 1}
    if not probes:
        return set()

    need_procs = any(p["procs"] for p in probes.values())
    procs = _running_procs() if need_procs else set()

    active = set()
    for key, spec in probes.items():
        hit = False
        if spec["procs"] and any(n in procs for n in spec["procs"]):
            hit = True
        if not hit and spec["ports"]:
            hit = any(_port_open(pt) for pt in spec["ports"])
        if hit:
            active.add(key)
    return active


# ---------------------------------------------------------------------------
# 后台探测器
# ---------------------------------------------------------------------------

class PresenceDetector:
    """后台线程轮询，对外只读缓存（读取零开销）。"""

    def __init__(self, interval=DEFAULT_INTERVAL):
        self.interval = max(0.5, float(interval))
        self._active = set()
        self._detail = {}          # {key: {"level": int, "age": float|None}}（T1）
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._ticks = 0
        self._last_error = None

    # ---- 对外 ----

    @property
    def agents(self):
        with self._lock:
            return set(self._active)

    def active(self):
        """当前活跃 agent 集合（读缓存，不触发探测）。"""
        return self.agents

    def detail(self):
        """分层明细（读缓存）：{key: {"level": 1|2|3, "age": float|None}}。

        level 语义见模块头：1=hook 回调 / 2=日志忙碌 / 3=进程或端口在跑。
        """
        with self._lock:
            return {k: dict(v) for k, v in self._detail.items()}

    def is_active(self, key):
        return key in self.agents

    def status(self):
        with self._lock:
            return {"active": sorted(self._active), "ticks": self._ticks,
                    "error": self._last_error, "interval": self.interval,
                    "levels": {k: v["level"] for k, v in self._detail.items()}}

    def poll_now(self):
        """立刻探一次（测试/手动刷新用）。"""
        try:
            d = detect_detail()
            with self._lock:
                self._detail = d
                self._active = {k for k, v in d.items() if v["level"] >= 1}
                self._last_error = None
        except Exception as e:                       # 探测失败绝不影响桌宠
            with self._lock:
                self._last_error = f"{type(e).__name__}: {e}"
        return self.agents

    # ---- 线程 ----

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="acc-presence",
                                        daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        # 首轮立刻探，避免开桌宠后要等一个周期才知道宿主在不在
        while not self._stop.is_set():
            self.poll_now()
            with self._lock:
                self._ticks += 1
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
