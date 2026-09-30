# -*- coding: utf-8 -*-
r"""P2 收尾：行为开关（OK 自动收起 / 联动关闭）UI 化 + 持久化 专项测试

交接说明 §9 P2：「OK 自动消失 / 联动关闭没有 UI 开关，只能改 wb_motion.py 的
*_ON 后重启」→ 现在右键菜单可切、写进 .whale_settings.json、重启恢复。

验证点：
  A. 默认值来自 wb_motion 常量（出厂默认唯一出处）
  B. 开关方法：翻转实例标志 + 写设置文件
  C. 持久化往返：重启（重建实例）后从设置恢复
  D. 行为生效：关掉后 _ok_focus_check 不触发自动收起、_check_linked_close 短路
  E. wb_motion 常量不被运行时污染（红线 2：动效数字唯一出处不被 UI 改写）

用法：python tools/test_menu_toggles.py
"""
import ctypes
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_motion as MOTION                     # noqa: E402
import wb_whale_win as W                       # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


TMP = os.path.join(HERE, "_tmp_toggle_test")
os.makedirs(TMP, exist_ok=True)
settings_file = os.path.join(TMP, ".whale_settings.json")


class _SettingsPatched:
    """把 SETTINGS_FILE 指到临时文件（实例的读写都发生在这个窗口期内）。"""

    def __enter__(self):
        self.saved = W.SETTINGS_FILE
        W.SETTINGS_FILE = settings_file
        return self

    def __exit__(self, *exc):
        W.SETTINGS_FILE = self.saved
        return False


def make_app(settings=None):
    with _SettingsPatched():
        if settings is not None:
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        elif os.path.exists(settings_file):
            os.remove(settings_file)
        return W.WhalePet(run_seconds=2)


print("\n[A] 默认值来自 wb_motion 常量")
app = make_app()
check("A1: ok_autodismiss 默认 = MOTION.OK_AUTODISMISS_ON",
      app.ok_autodismiss_on == MOTION.OK_AUTODISMISS_ON)
check("A2: linked_close 默认 = MOTION.LINKED_CLOSE_ON",
      app.linked_close_on == MOTION.LINKED_CLOSE_ON)

print("\n[B] 开关方法：翻转 + 持久化")
with _SettingsPatched():
    app._toggle_ok_autodismiss()
    app._toggle_linked_close()
    saved = json.load(open(settings_file, encoding="utf-8"))
check("B1: 两个开关都被翻转",
      app.ok_autodismiss_on is False and app.linked_close_on is False)
check("B2: 翻转后设置文件已写盘",
      saved.get("ok_autodismiss") is False and saved.get("linked_close") is False)

print("\n[C] 持久化往返：重建实例（模拟重启）")
app2 = make_app(saved)
check("C1: 重启后从设置恢复（False）",
      app2.ok_autodismiss_on is False and app2.linked_close_on is False)
with _SettingsPatched():
    app2._toggle_ok_autodismiss()
    app2._toggle_linked_close()
    saved2 = json.load(open(settings_file, encoding="utf-8"))
check("C2: 再翻转 → 回到 True 且写盘",
      saved2.get("ok_autodismiss") is True and saved2.get("linked_close") is True)

print("\n[D] 行为生效：关掉后对应路径短路")
now = time.time()
# D1/D2：OK 自动收起
app2._bub_mode = W.BUBBLE_OK
app2._ok_entered_at = now - 10
app2._ok_fg_at_entry = True
app2._wb_fg_prev = False
app2._is_wb_focused = lambda now: True          # 前台焦点不依赖真实桌面状态
dismissed = []
app2._ok_auto_dismiss = lambda now=None: dismissed.append(1)
app2.ok_autodismiss_on = False
app2._ok_focus_check(now)
check("D1: 关闭自动收起 → 满足焦点条件也不触发 dismiss", not dismissed)
app2.ok_autodismiss_on = True
app2._ok_focus_check(now)
check("D2: 打开后同条件触发 dismiss", bool(dismissed))
# D3/D4：联动关闭
app3 = make_app({"linked_close": False})
app3._linked_expected = True
app3._wb_seen_once = True
app3._wb_alive = lambda now: False              # 强造"agent 已全部消失"
app3._wb_gone_since = time.time() - (MOTION.LINKED_CLOSE_GRACE_S + 1)   # 宽限已过（随常量走，2026-09-30 起为 30s）
quit_flag = []
app3._linked_quit = lambda: quit_flag.append(1)
app3.linked_close_on = False
for _ in range(3):
    app3._check_linked_close(time.time())
check("D3: 关闭联动 → agent 全消失也不退出", not quit_flag)
app3.linked_close_on = True
for _ in range(3):
    app3._check_linked_close(time.time())
check("D4: 打开联动 → 连续消失判定后退出", bool(quit_flag))
# 注意：实例统一到文件末尾 close——close 过（GdiplusShutdown）之后再创建
# 实例会触发类级字体缓存句柄失效 → MeasureString 访问冲突（工程纪律）。

print("\n[E] wb_motion 常量不被运行时污染（红线 2）")
check("E1: 常量保持出厂值",
      MOTION.OK_AUTODISMISS_ON is True and MOTION.LINKED_CLOSE_ON is True)

# ---- F. 菜单全链路 smoke test（弹出→点击→分发→销毁）----
# 回归背景（2026-09-28 深夜事故）：DestroyMenu(subs) 引用了不存在的变量 →
# TrackPopupMenu 返回后在**命令分发之前**就抛 NameError → 所有菜单开关点击
# "无反应"（菜单能弹、点了没效果、无 menu_* 事件）。既有测试只直接调
# toggle 方法，从不走 Win32 分发链，因此没抓到。本节顶替 MenuSession 走完整
# context_menu：假点击一个开关，命令必须真正到达处理器。
print("\n[F] 菜单全链路 smoke test（弹出→点击→分发）")

app_f = app                                   # 复用 [A] 的实例（纪律：close 后不再新建）
before_f = app_f.bubble_on
recreated = []
app_f._recreate_window = lambda: recreated.append(1)   # 别真重建窗口
app_f._today_timeline = lambda: ([], [])               # 别查真数仓
app_f._stale_list = []
saved_sws = W.stale_working_sessions
W.stale_working_sessions = lambda *a, **k: []         # 别查真宿主库（见 test_stale_repair）


class _FakeSession:
    """顶替 MenuSession：run() 直接返回预置 cmd（不真弹窗口/不进模态循环）。"""

    def __init__(self, items, dpi=96):
        FakeSession.last_items = items

    def run(self, owner_hwnd, pt):
        return W.IDM_BUBBLE


saved_cls = W.MenuSession
FakeSession = _FakeSession
W.MenuSession = _FakeSession
try:
    with _SettingsPatched():
        app_f.context_menu(app_f.hwnd)
finally:
    W.MenuSession, W.stale_working_sessions = saved_cls, saved_sws
    # ⚠️ 这两个桩是挂在**实例**上的，用完必须摘掉：不摘的话后面的小节会静默走空壳
    #    （2026-09-29 踩过：菜单性能小节"假通过"，实际根本没调用 _recreate_window）。
    for _k in ("_recreate_window", "_today_timeline"):
        app_f.__dict__.pop(_k, None)
check("F1: 假点击「想法气泡」→ 命令分发到达、开关翻转",
      app_f.bubble_on is (not before_f), f"got {app_f.bubble_on} (before {before_f})")
check("F2: 开关生效路径触发（_recreate_window 被调）", bool(recreated))
check("F3: 菜单数据树完整（子菜单/分隔线/危险项就位）",
      len(FakeSession.last_items) >= 15
      and any(it.get("sub") for it in FakeSession.last_items)
      and any(it.get("sep") for it in FakeSession.last_items)
      and any(it.get("danger") for it in FakeSession.last_items),
      f"got {len(FakeSession.last_items)} 条")

# ---- G. 菜单美术规范（自绘菜单配色 = 看板设计语言）----
# 2026-09-28 深夜：右键菜单换自绘分层窗口（MenuSession），配色取自 dashboard.html :root。
# 这里锁死 menu_item_style 的三态/开关点/危险项规范，防止日后改花。
print("\n[G] 菜单美术规范（看板配色锁定）")
S = W.menu_item_style
_normal = S({"label": "x"}, selected=False, checked=False, disabled=False)
_sel = S({"label": "x"}, selected=True, checked=False, disabled=False)
_sel_ck = S({"label": "x"}, selected=True, checked=True, disabled=False)
_danger = S({"label": "x", "danger": True}, selected=False, checked=False, disabled=False)
_danger_sel = S({"label": "x", "danger": True}, selected=True, checked=False, disabled=False)
_disabled = S({"label": "x"}, selected=False, checked=False, disabled=True)
_sep = S({"sep": True}, selected=False, checked=False, disabled=False)
check("G1: 常态=墨色字+无底+灰点", 
      _normal["text"] == W.MENU_INK and _normal["bg"] is None
      and _normal["bold"] is False and _normal["dot"] and not _normal["dot_filled"])
check("G2: 悬停=淡紫底+深紫粗体（看板 .rs-list 选中态）",
      _sel["bg"] == W.MENU_HOVER and _sel["text"] == W.MENU_SEL_TX
      and _sel["bold"] is True)
check("G3: 勾选=实心藤紫点（未勾=实心浅灰）",
      _sel_ck["dot_filled"] is True and _sel["dot_filled"] is False)
check("G4: 危险项=领结红，悬停加深底",
      _danger["text"] == W.MENU_DANGER
      and _danger_sel["text"] == W.MENU_DANGER_HOVER
      and _danger_sel["bg"] == W.MENU_HOVER_D)
check("G5: 禁用=灰紫、不画点", 
      _disabled["text"] == W.MENU_INK_DIM and _disabled["dot"] is False)
check("G6: 分隔线走独立分支", _sep["kind"] == "sep")
check("G7: 子菜单条目带箭头标记",
      S({"label": "x", "arrow": True}, False, False, False)["arrow"] is True)
# G8: Win32 标志/消息常量必须与 SDK 真值一致（mock 测不到；曾把 MF_OWNERDRAW 手抄成
#    0xB0 → AppendMenuW 把 itemData 当字符串指针解引用 → 菜单弹不出来；又把
#    WM_MEASUREITEM/WM_DRAWITEM 抄成 0x0211/0x021B——那其实是 WM_ENTERMENULOOP/
#    WM_EXITMENULOOP（lparam 恒 0）→ measure 全落空 → 菜单缩成 19px 默认尺寸）
check("G8: Win32 菜单标志与消息号 = SDK 真值",
      W.MF_OWNERDRAW == 0x00000100 and W.MF_POPUP == 0x10
      and W.MF_SEPARATOR == 0x800 and W.MF_CHECKED == 0x8
      and W.MF_GRAYED == 0x1
      and W.WM_MEASUREITEM == 0x002C and W.WM_DRAWITEM == 0x002B)

# ---- H. 自绘菜单数据层（MenuSession 的布局/命中纯函数）----
# 2026-09-29：菜单改自绘分层窗口（MenuSession）——布局/命中是纯逻辑，可离线锁死。
print("\n[H] 自绘菜单数据层（布局/命中）")
_m = W.menu_metrics(96)
_hitems = [{"label": "A", "cmd": 1}, {"sep": True},
           {"label": "B", "cmd": 2, "checked": True}]
_hrows, _hch = W.menu_layout(_hitems, _m)
check("H1: 行数 = 条目数、总高 > 0", len(_hrows) == 3 and _hch > 0)
check("H2: 命中普通条目", W.menu_hit(_hrows, _m["pad_v"] + 1) == 0)
check("H3: 命中第三条", W.menu_hit(_hrows, _m["pad_v"] + _m["h"] + _m["sep_h"] + 1) == 2)
check("H4: 越界 → None", W.menu_hit(_hrows, -5) is None
      and W.menu_hit(_hrows, _hch + 10) is None)
_sess = W.MenuSession(_hitems, dpi=96)
check("H5: MenuSession 构造（阴影边距/内容高）",
      _sess.margins > 0 and _sess._content_h > 0)
_sub_items = [{"label": "P", "sub": [{"label": "S1", "cmd": 11}]}]
_srows, _sch = W.menu_layout(_sub_items, _m)
check("H6: 带 sub 的条目只占一行（子条目独立布局）", len(_srows) == 1 and _sch > 0)

# ---- I. 真窗口契约（2026-09-29 用户报障：菜单被人物盖住 + 鼠标穿透）----
# 根因：菜单窗口的 ex-style 里把 `0x00000020` 当 TOPMOST 手写进去 —— 那其实是
#       **WS_EX_TRANSPARENT（鼠标穿透）**，TOPMOST 是 0x8。两个症状正好对应：
#       ① 没有 topmost 位 → 被每秒抢置顶的桌宠压住；② 穿透 → hover/点击全落给下层。
#       这一节既有「SDK 真值」锁，也有**真窗口**的行为契约（mock 测不到）。
print("\n[I] 真窗口契约（扩展样式 / 置顶 / 命中 / 不再冻结）")


def _zchain(limit=800):
    """顶层可见窗口，自上而下。

    ⚠️ 必须先自己声明 restype/argtypes：模块里没声明时 ctypes 按 32-bit int 处理，
    HWND 被截断 → 链里认不出自己的窗口（踩过：I4 两个窗口都报 #1）。
    """
    from ctypes import wintypes as _wt
    W._user32.GetTopWindow.restype = _wt.HWND
    W._user32.GetWindow.restype = _wt.HWND
    W._user32.GetWindow.argtypes = [_wt.HWND, _wt.UINT]
    W._user32.IsWindowVisible.argtypes = [_wt.HWND]
    GW_HWNDNEXT = 2
    out, h = [], W._user32.GetTopWindow(None)
    while h and len(out) < limit:
        if W._user32.IsWindowVisible(h):
            out.append(h)
        h = W._user32.GetWindow(h, GW_HWNDNEXT)
    return out


def _zrank(hwnd):
    ch = _zchain()
    return ch.index(hwnd) if hwnd in ch else -1


check("I1: 窗口扩展样式常量 = SDK 真值（TOPMOST=0x8 ≠ TRANSPARENT=0x20）",
      W.WS_EX_TOPMOST == 0x00000008 and W.WS_EX_TRANSPARENT == 0x00000020
      and W.WS_EX_LAYERED == 0x00080000 and W.WS_EX_TOOLWINDOW == 0x00000080
      and W.WS_EX_NOACTIVATE == 0x08000000
      and W.WS_EX_TOPMOST != W.WS_EX_TRANSPARENT,
      f"TOPMOST=0x{W.WS_EX_TOPMOST:X} TRANSPARENT=0x{W.WS_EX_TRANSPARENT:X}")

_GWL_EXSTYLE = -20
_i_items = [{"label": "A", "cmd": 1}, {"sep": True},
            {"label": "B", "cmd": 2, "checked": True}]
_isz = W.MenuSession(_i_items, dpi=96)
_i_ok = _isz._open(None, (60, 60), grab=False)      # 不抢前台 / 不抢 capture
_i_hwnd = _isz.hwnd
_i_ex = W._user32.GetWindowLongW(_i_hwnd, _GWL_EXSTYLE) & 0xFFFFFFFF if _i_ok else 0
check("I2: 菜单窗口 ex-style = LAYERED|TOPMOST|TOOLWINDOW，无 TRANSPARENT",
      bool(_i_ok) and (_i_ex & W.WS_EX_LAYERED) and (_i_ex & W.WS_EX_TOPMOST)
      and (_i_ex & W.WS_EX_TOOLWINDOW) and not (_i_ex & W.WS_EX_TRANSPARENT),
      f"ex=0x{_i_ex:08X}")

# 非 topmost 的普通窗口：菜单（topmost 带）必须整体压在它上面
_plain = W._user32.CreateWindowExW(
    W.WS_EX_LAYERED | W.WS_EX_TOOLWINDOW, "WBMenuClass",
    None, W.WS_POPUP, 60, 60, 200, 200, None, None,
    W._kernel32.GetModuleHandleW(None), None)
W._user32.ShowWindow(_plain, W.SW_SHOWNA)
check("I3: 菜单在普通窗口之上（topmost 带隔离生效）",
      _zrank(_i_hwnd) >= 0 and _zrank(_plain) >= 0 and _zrank(_i_hwnd) < _zrank(_plain),
      f"菜单#{_zrank(_i_hwnd)} 普通#{_zrank(_plain)}")
W._user32.DestroyWindow(_plain)

# 仿桌宠：也常驻 topmost，并像 tick() 一样抢置顶；菜单 _pin_top 后必须仍压它一层
_pet_sim = W._user32.CreateWindowExW(
    W.WS_EX_LAYERED | W.WS_EX_TOPMOST | W.WS_EX_TOOLWINDOW, "WBMenuClass",
    None, W.WS_POPUP, 60, 60, 200, 200, None, None,
    W._kernel32.GetModuleHandleW(None), None)
W._user32.ShowWindow(_pet_sim, W.SW_SHOWNA)
W._user32.SetWindowPos(_pet_sim, ctypes.c_void_p(W.HWND_TOPMOST), 0, 0, 0, 0,
                       W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)
_r_pet = _zrank(_pet_sim)                           # 仿桌宠抢到置顶
_isz._pin_top()                                     # = _render() 里每帧在做的事
_ch = _zchain()                                     # ★ 同一快照里取两个排名（别分两次测）
_r_menu = _ch.index(_isz.hwnd) if _isz.hwnd in _ch else -1
_r_pet = _ch.index(_pet_sim) if _pet_sim in _ch else -1
check("I4: 仿桌宠抢置顶后，菜单 _pin_top 仍压在它之上",
      _r_menu >= 0 and _r_pet >= 0 and _r_menu < _r_pet,
      f"菜单#{_r_menu} 仿桌宠#{_r_pet}")

# 命中：卡片中心必须归菜单自己（穿透的话这里会返回桌面/别的窗口）
# ⚠️ 例外：**锁屏/系统顶层遮罩**（如 LockScreenBackstopFrame「Backstop Window」，全屏 topmost）
#    会把整块屏幕的命中都吃掉 —— 那不是"菜单穿透"，是环境状态（用户锁屏了）。
#    实测过：锁屏时 I5 必红；解屏后自然恢复。所以这里只对**非系统遮罩**判红。
def _is_system_overlay(h):
    if not h or not W._user32.IsWindow(h):
        return False
    c = ctypes.create_unicode_buffer(128)
    W._user32.GetClassNameW(h, c, 128)
    t = ctypes.create_unicode_buffer(128)
    W._user32.GetWindowTextW(h, t, 128)
    if "LockScreenBackstopFrame" in c.value or t.value == "Backstop Window":
        return True
    r = W.RECT()
    W._user32.GetWindowRect(h, ctypes.byref(r))
    vs = (W._user32.GetSystemMetrics(78), W._user32.GetSystemMetrics(79))   # SM_CXVIRTUALSCREEN/Y
    return (r.right - r.left) >= vs[0] - 2 and (r.bottom - r.top) >= vs[1] - 2


_mg = _isz.margins
_cx = 60 + _mg + _isz.m["w"] / 2.0
_cy = 60 + _mg + _isz._content_h / 2.0
_pt = W.POINT(int(_cx), int(_cy))
_who = W._user32.WindowFromPoint(_pt)
if _who != _isz.hwnd and _is_system_overlay(_who):
    _ovc = ctypes.create_unicode_buffer(128)
    W._user32.GetClassNameW(_who, _ovc, 128)
    check("I5: 菜单卡片中心命中 = 菜单自己（不穿透）—— 系统顶层遮罩在场，跳过",
          True, f"被 {_ovc.value} 挡住（锁屏？）")
else:
    check("I5: 菜单卡片中心命中 = 菜单自己（不穿透）",
      _who == _isz.hwnd, f"命中 0x{_who or 0:X}（期望 0x{_i_hwnd or 0:X}）")

_isz._teardown()
check("I6: _teardown 后窗口已销毁、登记表已清",
      _isz.hwnd is None and _i_hwnd not in W._MENU_WINDOWS)
W._user32.DestroyWindow(_pet_sim)

# 动效闸门：菜单开着时 tick 该停（省一次 SQLite），_anim_tick **不该**停（人物不冻住）
_seen = []
_saved_q = W.query_db
W.query_db = lambda *a, **k: (_seen.append(1), (None, None, None, None))[1]
app_f._menu_open = True
app_f.tick()
app_f._drawn_sig = "SENT"
app_f._motion_sig = None
app_f._anim_tick()
_anim_ran = app_f._drawn_sig != "SENT"
app_f._menu_open = False
W.query_db = _saved_q
check("I7: 菜单开着时 tick 被闸住（不查库）", not _seen, f"query_db 被调 {len(_seen)} 次")
check("I8: 菜单开着时 _anim_tick 照常绘制（人物不冻住）", _anim_ran)

# 挪窗口：菜单开着时必须 SWP_NOZORDER（否则桌宠会把自己重新置顶压住菜单）
_flags = []
_saved_swp = W._user32.SetWindowPos
W._user32.SetWindowPos = lambda *a, **k: (_flags.append(a[-1]), 1)[1]
app_f._menu_open = True
app_f._move_window(app_f.hwnd, 10, 10)
app_f._menu_open = False
app_f._move_window(app_f.hwnd, 10, 10)
W._user32.SetWindowPos = _saved_swp
check("I9: 菜单开着时挪窗口带 SWP_NOZORDER（不抢置顶）",
      bool(_flags[0] & W.SWP_NOZORDER) and not (_flags[1] & W.SWP_NOZORDER),
      f"开={_flags[0]:#x} 关={_flags[1]:#x}")

# ---- J. 子菜单窗口尺寸（2026-09-29 用户报障：子菜单下面多出一截"空白"）----
# 根因：`_render_sub` 误用**父菜单的** surface（`s = self.surf`）。父菜单比子菜单高，
# 子卡片画完后，剩下那一段只剩三层阴影叠出来的灰块；而 `Surface.present` 是按 bitmap
# 尺寸（`self.w/self.h`）上屏的 → ULW 把子菜单窗口撑成父菜单那么高 = 用户看到的空白。
print("\n[J] 子菜单窗口尺寸（用子菜单自己的 surface，不被父菜单撑大）")
_j_items = [{"label": f"项{i}", "cmd": i} for i in range(8)]     # 比子菜单高（复现现场）
_j_items[3] = {"label": "桌宠大小", "cmd": 100,
               "sub": [{"label": f"{i}x", "cmd": 10 + i} for i in range(6)]}
_js = W.MenuSession(_j_items, dpi=96)
_j_ok = _js._open(None, (60, 60), grab=False)
_js._open_sub(3)
_sub = _js._sub
_exp_w, _exp_h = _js._sub_rect(3)[2:4]
check("J1: 子菜单 surface 尺寸 = 子菜单内容尺寸（不是父菜单的）",
      bool(_j_ok) and _sub["surf"].w == _exp_w and _sub["surf"].h == _exp_h,
      f"surf={_sub['surf'].w}x{_sub['surf'].h} 期望={_exp_w}x{_exp_h}")
check("J2: 父子 surface 是两个对象、高度不同（父更高）",
      _sub["surf"] is not _js.surf and _js.surf.h > _sub["surf"].h,
      f"父={_js.surf.w}x{_js.surf.h} 子={_sub['surf'].w}x{_sub['surf'].h}")

_jr = W.RECT()
W._user32.GetWindowRect(_sub["hwnd"], ctypes.byref(_jr))
check("J3: 子菜单真窗口高度 = 子内容高（不被 ULW 撑成父菜单高）",
      (_jr.right - _jr.left) == _exp_w and (_jr.bottom - _jr.top) == _exp_h,
      f"窗口 {_jr.right - _jr.left}x{_jr.bottom - _jr.top} 期望={_exp_w}x{_exp_h}")

_before = ctypes.string_at(_js.surf.bits, _js.surf.w * _js.surf.h * 4)
_js._render_sub()
_after = ctypes.string_at(_js.surf.bits, _js.surf.w * _js.surf.h * 4)
check("J4: 子菜单重绘不动父菜单 surface（原先会被 clear + 重画）",
      _before == _after)
_js._teardown()

# ---- K. 菜单性能契约（2026-09-29：用户报「打开/选择设置时卡顿」）----
# 实测根因：点「想法气泡」会走 _recreate_window → 无条件重建帧缓存（重新解码 + 重采样
# 525 帧 ≈ 3.2s）→ UI 线程冻三秒。修法：① 帧缓存只跟显示尺寸有关（气泡开关不重建）
# ② 真改尺寸时后台重建，UI 立刻返回 ③ 菜单项构建不再每次查库（TTL）
# ④ 子菜单"停留才展开"，鼠标扫过时不再逐个闪。
print("\n[K] 菜单性能契约（缓存重建 / 后台建 / 清单 TTL / 子菜单延迟）")
# ⚠️ [F] 小节把 app_f._recreate_window 换成了 lambda（"别真重建窗口"）**且没还原** ——
#    这里必须先摘掉那个实例属性，否则下面的检查走的是桩，断言"假通过"（踩过）。
_rc_stub = getattr(app_f, "_recreate_window", None)
if "_recreate_window" in app_f.__dict__:
    del app_f.__dict__["_recreate_window"]
_saved_build = W.WhalePet._build_cache_objects
_build_calls = []


def _fake_build(self):
    _build_calls.append(1)
    return {}, [], None
W.WhalePet._build_cache_objects = _fake_build
try:
    # J1：气泡开关（只改窗口高度，不改 sc）不该重建缓存
    check("K0: _recreate_window 是真实方法（没被别处的桩顶替）",
          "_recreate_window" not in app_f.__dict__)
    _saved_bubble_on = app_f.bubble_on
    _build_calls.clear()
    _t0 = time.time()
    app_f.bubble_on = not _saved_bubble_on
    app_f._recreate_window()
    _dt_bubble = (time.time() - _t0) * 1000
    app_f.bubble_on = _saved_bubble_on
    app_f._recreate_window()
    check("K1: 气泡开关不重建帧缓存（原来要 3.2s）",
          len(_build_calls) == 0 and _dt_bubble < 400,
          f"重建 {len(_build_calls)} 次 / {_dt_bubble:.0f} ms")

    # J2：真改大小 → 立刻返回（后台建），随后换新
    _build_calls.clear()
    _saved_scale = app_f.scale
    _t0 = time.time()
    app_f.scale = 0.8 if abs(_saved_scale - 0.8) > 1e-6 else 1.0
    app_f._recreate_window()
    _dt_resize = (time.time() - _t0) * 1000
    check("K2a: 改大小立刻返回（不阻塞 UI）",
          _dt_resize < 400 and app_f._cache_building,
          f"{_dt_resize:.0f} ms building={app_f._cache_building}")
    _ok_new = False
    for _ in range(60):
        if app_f._cache_new is not None:
            _ok_new = True
            break
        time.sleep(0.05)
    app_f._wait_cache_new()
    check("K2b: 后台产物被换上、缓存 sig 已更新",
          _ok_new and not app_f._cache_building
          and app_f._cache_sig_done == app_f._cache_sig(),
          f"new={_ok_new} sig={app_f._cache_sig_done}")
    app_f.scale = _saved_scale
finally:
    W.WhalePet._build_cache_objects = _saved_build
    if _rc_stub is not None:                 # 还原 [F] 留下的桩，别影响后面的小节
        app_f._recreate_window = _rc_stub

# J3：菜单项构建不再每次查库（残留清单 TTL 记忆）
_saved_stale = W.stale_working_sessions
_stale_calls = []


def _fake_stale(*a, **k):
    _stale_calls.append(1)
    return []
_imported = W.stale_working_sessions
W.stale_working_sessions = _fake_stale
try:
    app_f._stale_ts = 0.0
    for _ in range(4):
        app_f._stale_list_cached()
    check("K3: 残留清单 TTL 记忆（4 次调用只查 1 次库）",
          len(_stale_calls) == 1, f"查库 {len(_stale_calls)} 次")
finally:
    W.stale_working_sessions = _imported

# J4：子菜单"停留才展开"
_j_items = [{"label": "A", "cmd": 1},
            {"label": "有子菜单", "sub": [{"label": "S1", "cmd": 11}]},
            {"label": "B", "cmd": 2}]
_js = W.MenuSession(_j_items, dpi=96)
_js._open(app_f.hwnd, (60, 60), grab=False)
_l, _t, _r, _b = None, None, None, None
_pr = W.RECT()
W._user32.GetWindowRect(_js.hwnd, ctypes.byref(_pr))
_y0, _y1, _ = _js._rows[1]
_gx = _pr.left + _js.margins + _js.m["w"] // 2
_gy = _pr.top + _js.margins + (_y0 + _y1) // 2
_js.on_move(_gx, _gy)
check("K4a: 悬停到子菜单父条目 → 先不展开（只挂待展开）",
      _js._sub is None and _js._sub_pending is not None and _js._sub_timer_on,
      f"sub={_js._sub} pending={_js._sub_pending} timer={_js._sub_timer_on}")
_js.on_timer()
check("K4b: 停留不足时仍不展开", _js._sub is None)
_js._sub_pending = (1, time.time() - 1.0)          # 模拟"已停留够久"
_js.on_timer()
check("K4c: 停留够久后展开子菜单（真实窗口）",
      _js._sub is not None and bool(_js._sub.get("hwnd")),
      f"sub={_js._sub and _js._sub.get('hwnd')}")
# 扫到别的条目 → 子菜单立刻收起、待展开也清掉
_js.on_move(_gx, _pr.top + _js.margins + (_js._rows[2][0] + _js._rows[2][1]) // 2)
check("K4d: 移到别的条目 → 子菜单立刻收起且不再待展开",
      _js._sub is None and _js._sub_pending is None, f"sub={_js._sub}")
_js._teardown()
check("K4e: _teardown 后定时器已关", _js._sub_timer_on is False)

# 统一收尾（全部实例在这里 close，之后不再创建实例）
app.close()
app2.close()
app3.close()

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
