# -*- coding: utf-8 -*-
r"""古见同学桌宠 · 守望进程（独立项目）

功能：检测到 WorkBuddy.exe 运行且桌宠未运行时，自动拉起桌宠。
设计为随登录常驻（pythonw 无窗口），轮询开销极低（纯 ctypes，无子进程、无第三方库）。

覆盖所有 WorkBuddy 启动方式：开机自启、开始菜单、快捷方式、命令行。

职责二：WorkBuddy 会话结束（进程退出）时，兜底关闭遗留桌宠
（桌宠自身也有联动关闭，这里是双保险）。

CLI:
  python wb_whale_watcher.py                # 常驻守望（默认 5s 轮询）
  python wb_whale_watcher.py --once         # 只检测一次，需要则拉起后退出（测试用）
  python wb_whale_watcher.py --status       # 打印 WorkBuddy / 桌宠 / 自启项状态
  python wb_whale_watcher.py --install      # 注册登录自启（HKCU Run，幂等，无需管理员）
  python wb_whale_watcher.py --uninstall    # 取消守望自启（不影响正在运行的桌宠）
"""

import ctypes
import ctypes.wintypes as wt
import json  # 供 _pet_entry() 读取 pet_mode
import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
HOVER_ENTRY = os.path.join(SCRIPTS_DIR, "hover.py")
PET3D_ENTRY = os.path.join(SCRIPTS_DIR, "wb_pet3d.py")


def _pet_entry():
    """选择桌宠入口：默认 2D（`hover.py`）；设置 `pet_mode: "3d"` 时改为 3D（`wb_pet3d.py`）。

    为什么要这样做：自启项里已经有守望在拉桌宠，如果再单独给 3D 加一条 Run，
    就会**同时跑起两个桌宠**。用模式切换可以保证只起一个，且仍然由守望托管（崩溃自愈）。
    """
    # ⚠️ 不要静默吞异常：缺 import / 键名写错都会悄悄退回 2D，很难查。明确打印原因。
    path = os.path.join(SCRIPTS_DIR, ".whale_settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        mode = str(st.get("pet_mode", "2d")).lower()
        if mode == "3d":
            if os.path.isfile(PET3D_ENTRY):
                return PET3D_ENTRY
            print(f"[watcher] pet_mode=3d 但找不到 {PET3D_ENTRY}，回退 2D", file=sys.stderr)
        elif mode != "2d":
            print(f"[watcher] pet_mode 非法: {mode!r}（只支持 2d/3d），回退 2D", file=sys.stderr)
    except FileNotFoundError:
        pass                                   # 没设置文件 = 默认 2D，正常
    except Exception as e:
        print(f"[watcher] 读取 pet_mode 失败({type(e).__name__}: {e})，回退 2D", file=sys.stderr)
    return HOVER_ENTRY

WORKBUDDY_EXE = "WorkBuddy.exe"
WHALE_CLASS = "WBWhalePetClass"      # 桌宠主窗口类名（wb_whale_win.py 注册，历史名保留）

# HKCU Run 键中的自启项名称（本项目专用，避免与其它项目抢同一个键）
RUN_VALUE_NAME = "KomiPetWhaleWatcher"
# 历史自启项：从 WorkBuddy skill 目录独立出来之前的键名，注册时顺手清掉
LEGACY_RUN_VALUE_NAMES = ("WorkBuddyWhaleWatcher",)

POLL_INTERVAL = 5                    # 常驻模式轮询间隔（秒）
SPAWN_COOLDOWN = 30                  # 拉起桌宠后的冷却期（秒），避免竞态重复拉起

WM_CLOSE = 0x0010                    # 优雅关闭消息（桌宠窗口收到 → DestroyWindow → 进程退出）
PROCESS_TERMINATE = 0x0001           # 强制结束进程所需的访问权
KILL_GRACE_S = 2.0                   # 先礼后兵：WM_CLOSE 后等这么久仍未退出 → TerminateProcess


# ---------------------------------------------------------------------------
# Win32 检测（纯 ctypes，零子进程开销）
# ---------------------------------------------------------------------------

def _process_pid(exe_name):
    """返回指定 exe 的进程 PID（取第一个匹配），未运行返回 None。"""
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return None  # 快照失败按"没在运行"处理，下轮再试
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe_name.lower():
                return entry.th32ProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return None
    finally:
        k32.CloseHandle(snap)


def _whale_window_alive():
    """用窗口类名检测桌宠是否存活——比进程名可靠（pythonw 可能跑着别的东西）。"""
    hwnd = ctypes.windll.user32.FindWindowW(WHALE_CLASS, None)
    return bool(hwnd)


# ---------------------------------------------------------------------------
# 拉起桌宠
# ---------------------------------------------------------------------------

def spawn_whale():
    """以分离方式启动桌宠桌宠（pythonw 无控制台，进程独立于守望者）。

    注入 WB_PET_LINKED=1：告诉桌宠"你是为 WorkBuddy 拉起的"，
    于是即使它启动瞬间没探测到 WorkBuddy，也会在宽限期后自行退出（不留孤儿）。
    """
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable  # 兜底
    flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    env = dict(os.environ)
    env["WB_PET_LINKED"] = "1"
    subprocess.Popen(
        [pythonw, _pet_entry()],
        cwd=SCRIPTS_DIR, close_fds=True,
        creationflags=flags, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# 联动关闭兜底：WorkBuddy 退出后，灭杀遗留的桌宠桌宠
# ---------------------------------------------------------------------------

def should_kill_on_session_end(prev_agents, cur_agents):
    """纯判定：是否由「有 agent 活跃」变为「一个都不剩」。

    v2：入参从"进程实例 (pid, 创建时间)"改为"**活跃 agent 集合**"。

    · 首次检测（prev 为空/None）不触发
    · 探测未知（cur 为 None）**不触发** —— 宁可留着，也不误杀
    · 仍有 agent 活跃（cur 非空）不触发
    """
    return bool(prev_agents) and cur_agents is not None and not cur_agents


def _window_pid(hwnd):
    pid = wt.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def kill_whale(grace_s=KILL_GRACE_S):
    """关闭遗留的桌宠桌宠：先 WM_CLOSE 礼请，超时未退再 TerminateProcess。

    返回 True=已确保关闭（或本来就没有）；False=未能关闭（极少见）。
    """
    hwnd = ctypes.windll.user32.FindWindowW(WHALE_CLASS, None)
    if not hwnd:
        return True                                   # 本来就没桌宠
    pid = _window_pid(hwnd)
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    deadline = time.time() + grace_s
    while time.time() < deadline:
        time.sleep(0.2)
        if not _whale_window_alive():
            return True                               # 优雅退出成功
    # 仍未退出（卡死/旧版无 WM_CLOSE 处理）→ 强制结束进程
    if pid:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            try:
                k32.TerminateProcess(h, 0)
            finally:
                k32.CloseHandle(h)
            return not _whale_window_alive()
    return not _whale_window_alive()


def active_agents():
    """当前活跃的 agent 集合。**None = 无法判定**（调用方应保持不动，宁可留着）。

    v2（2026-09-25 解耦）：宿主从「WorkBuddy 进程」泛化为「任一已注册 agent」。
    探针是**声明式**的（app/assets/_acc_persona.json 的 presence 字段），
    新增 agent 只改 JSON 就自动覆盖，不用动本文件。

    降级链（保证任何环境下都有确定行为）：
      · 测试钩子 WB_WATCHER_TARGET_EXE 设了 → 退化为"只认这一个 exe"（与旧行为一致）
      · 探测模块不可用 → 退化为"只认 WorkBuddy"（老环境行为不变）
      · 探测本身抛异常 → 返回 None（无法判定，不误杀）
    """
    override = os.environ.get("WB_WATCHER_TARGET_EXE")
    if override:
        return {"__target__"} if _process_pid(override) else set()
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    try:
        import wb_agent_presence as P
    except Exception:
        return {"workbuddy"} if _process_pid(WORKBUDDY_EXE) else set()
    try:
        return set(P.detect_once())
    except Exception:
        return None


def _process_info(exe_name):
    """返回 (pid, 创建时间) 或 None。
    创建时间用于识别 PID 复用：同 PID 但创建时间不同 = 新进程 = 新会话。
    """
    pid = _process_pid(exe_name)
    if pid is None:
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return (pid, None)  # 打不开（权限）就退化为仅 PID 判断
    try:
        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]
        create, exit_t, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(create), ctypes.byref(exit_t),
                                   ctypes.byref(kernel), ctypes.byref(user)):
            return (pid, None)
        ctime = (create.dwHighDateTime << 32) | create.dwLowDateTime
        return (pid, ctime)
    finally:
        k32.CloseHandle(h)


def _recent_menu_quit(window_seconds=90):
    """检查桌宠事件日志里最近是否有用户主动退出（右键 → 退出桌宠）。
    用于区分「用户手动退出」（不拉起）和「意外崩溃」（自动拉起）。"""
    import json
    # komi- 前缀与桌宠本体的 EVENT_LOG 一致（wb_whale_win.py）；旧技能遗留进程
    # 写的是无前缀旧名，互不干扰（P1 隔离）
    log = os.path.join(os.environ.get("TEMP", "."), "komi-wb-hover.events.log")
    try:
        with open(log, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-10:]
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("ev") == "menu_quit":
                hh, mm, ss = rec.get("ts", "0:0:0").split(":")
                now = time.localtime()
                secs = int(hh) * 3600 + int(mm) * 60 + int(ss)
                now_secs = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
                if 0 <= now_secs - secs <= window_seconds:  # 跨午夜不判定，可忽略
                    return True
                return False
        return False
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 常驻守望 / 一次性检测
# ---------------------------------------------------------------------------

def watch(interval=POLL_INTERVAL):
    """守望主循环。

    v2（2026-09-25 解耦）：把"会话"从「WorkBuddy 进程实例」泛化为
    **「活跃 agent 集合」**。语义一一对应：
      · 集合非空          ←→ 旧「会话存在」      → 确保桌宠在跑
      · 集合由非空变空    ←→ 旧「会话结束」      → 兜底灭杀遗留桌宠
      · 集合首次非空      ←→ 旧「新会话开始」    → 清除"用户手动退出"抑制
      集合为空            → 不拉起（在没装任何 agent 的机器上不空转）
    """
    prev = None            # 上一轮活跃集合；None = 尚未探测
    whale_seen = False     # 上一轮桌宠是否存活
    suppressed = False     # 本轮使用内用户已手动退出 → 不再拉起
    last_spawn = 0.0

    while True:
        time.sleep(interval)
        try:
            cur = active_agents()
            if cur is None:
                continue                       # 探测未知：保持不动

            if should_kill_on_session_end(prev, cur):
                # 最后一个 agent 也退出了 → 兜底灭杀遗留桌宠（桌宠自身通常已自关，防卡死/旧版）
                kill_whale()
                suppressed = False
                whale_seen = False
                prev = cur
                continue

            if not prev and cur:
                suppressed = False                 # 新一轮使用开始 → 清除抑制
                whale_seen = False
            prev = cur

            if not cur:
                continue                           # 没有任何 agent → 不拉起

            whale = _whale_window_alive()
            if not whale and not suppressed and time.time() - last_spawn > SPAWN_COOLDOWN:
                if whale_seen and _recent_menu_quit():
                    suppressed = True              # 用户手动退出，本轮内不再打扰
                else:
                    spawn_whale()
                    last_spawn = time.time()
            whale_seen = whale
        except Exception:
            pass  # 守望进程绝不因单轮异常退出


def check_once():
    cur = active_agents()
    if cur is None:
        print("agent 探测不可用，未作判断")
        return
    if not cur:
        print("未检测到任何 agent 运行，不拉起桌宠")
        return
    names = "/".join(sorted(cur))
    if _whale_window_alive():
        print(f"agent 运行中（{names}）；桌宠已在运行，无需拉起")
    else:
        spawn_whale()
        print(f"agent 运行中（{names}）；已拉起桌宠")


# ---------------------------------------------------------------------------
# 计划任务注册 / 状态
# ---------------------------------------------------------------------------

def _run_key_set():
    """把守望进程写入 HKCU Run（登录自启，用户级无需管理员，幂等）。

    同时清理历史自启项（LEGACY_RUN_VALUE_NAMES）——本项目从 WorkBuddy skill 目录
    独立出来时，旧的自启项还指回 skill，不清掉会各自拉起一只桌宠。
    """
    import winreg
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    value = f'"{pythonw}" "{os.path.abspath(__file__)}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
            try:
                old, _ = winreg.QueryValueEx(k, RUN_VALUE_NAME)
            except FileNotFoundError:
                old = None
            winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, value)
            for legacy in LEGACY_RUN_VALUE_NAMES:
                try:
                    lv, _ = winreg.QueryValueEx(k, legacy)
                    winreg.DeleteValue(k, legacy)
                    print(f"已清理历史自启项 {legacy}（原指向 {lv}）")
                except FileNotFoundError:
                    pass
            return "updated" if old else "created"
    except PermissionError as e:
        print(f"注册失败（权限不足）：{e}", file=sys.stderr)
        return 1


def _run_key_remove():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            removed = []
            for name in (RUN_VALUE_NAME,) + LEGACY_RUN_VALUE_NAMES:
                try:
                    winreg.DeleteValue(k, name)
                    removed.append(name)
                except FileNotFoundError:
                    pass
        if removed:
            print(f"已移除守望进程自启项 {', '.join(removed)}（正在运行的桌宠不受影响）")
        else:
            print("本来就没有守望自启项，无需操作")
    except PermissionError as e:
        print(f"删除失败（权限不足）：{e}", file=sys.stderr)
        return 1
    return 0


def _autostart_registered():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_QUERY_VALUE) as k:
            winreg.QueryValueEx(k, RUN_VALUE_NAME)
        return True
    except OSError:
        return False


LEGACY_MARK = "workbuddy-usage-dashboard__skillhub"   # 拆分前旧技能目录的特征串


def _legacy_leftovers():
    """旧技能（拆分前目录）还在跑的进程 pid 列表——迁移收尾核查用。

    只在 `--status` 这类**一次性诊断**里调用：内部起 PowerShell 子进程，
    绝不能进 5s 守望循环（工程红线 6）。
    返回 None = 无法判定（子进程失败），三值语义与联动关闭一致。
    """
    if os.name != "nt":
        return []
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '"
             + LEGACY_MARK + "' } | Select-Object -ExpandProperty ProcessId"],
            capture_output=True, timeout=30,
            creationflags=0x08000000)                # CREATE_NO_WINDOW
        pids = [int(x) for x in r.stdout.decode(errors="ignore").split()
                if x.strip().isdigit()]
        return [p for p in pids if p != os.getpid()]
    except Exception:
        return None


def show_status():
    cur = active_agents()
    wb = "无法判定" if cur is None else ("、".join(sorted(cur)) if cur else "无")
    whale = _whale_window_alive()
    print(f"活跃 agent    : {wb}")
    print(f"桌宠        : {'运行中' if whale else '未运行'}")
    print(f"守望自启项    : {'已注册（下次登录自动守望）' if _autostart_registered() else '未注册'}")
    leftovers = _legacy_leftovers()
    if leftovers is None:
        print("旧技能遗留进程: 无法判定（PowerShell 不可用？）")
    elif leftovers:
        print(f"旧技能遗留进程: {len(leftovers)} 个（pid "
              f"{', '.join(map(str, leftovers))}）—— 重新登录后消失，"
              "届时旧技能目录（含其旧库）可整体删除")
    else:
        print("旧技能遗留进程: 无（迁移已收尾）")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--status" in argv:
        show_status()
        return 0
    if "--install" in argv:
        r = _run_key_set()
        if r == 1:
            return 1
        print(f"守望自启项 → {r}（WorkBuddy 打开时将自动拉起桌宠）")
        return 0
    if "--uninstall" in argv:
        return _run_key_remove()
    if "--once" in argv:
        check_once()
        return 0
    if os.name != "nt":
        print("本脚本仅适用于 Windows", file=sys.stderr)
        return 2
    watch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
