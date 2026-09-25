#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
b_hover.py — WorkBuddy 悬浮球 v3.7（macOS 原生 Cocoa / NSPanel）
=========================================================
数据源：活跃状态 = WorkBuddy 主库 workbuddy.db sessions.status='working'（权威状态）
       轮次明细 = 看板数仓 wb_usage_dw.db ODS（jsonl 采集，wb_api.py 监听保持最新）

平台：仅 macOS。Windows 请用 b_hover_win.py（纯 ctypes + GDI+，无第三方依赖）。
      两端公共逻辑（路径解析 / SQL 取数 / 格式化 / 卡片文案 / 位置记忆）见 wb_hover_core.py，
      改口径只需改 core，两端同步生效。建议用统一入口 hover.py 自动分发平台。

功能：
  1. 真正的圆形悬浮球（macOS 原生 NSPanel，透明背景+圆形绘制，无边框无方块感）
  2. 浮动层级（NSStatusWindowLevel）永远置顶；非激活面板不抢焦点；无 Dock 图标
  3. 1 秒轮询：活跃对话数（status='working' 的会话数；本版只显示活跃数）
  4. 单击 → 活跃对话面板：活跃会话当前轮次 积分/tokens/请求内容/持续时间
  5. 双击 → 浏览器打开完整看板（仅一次）
  6. 按住拖动；右键菜单：打开看板 / 退出

启动（需 pyobjc-framework-Cocoa，install.py 会自动准备）：
  python3 b_hover.py

仅 macOS 可用；非 macOS 或缺少 pyobjc 时本脚本会给出明确的安装指引后退出。
"""

import json
import os
import sys
import time
import webbrowser

try:
    import objc
    from Foundation import NSMakeRect, NSMakePoint, NSObject, NSTimer, NSString
    from AppKit import (
        NSApplication, NSPanel, NSView, NSColor, NSFont, NSBezierPath,
        NSMenuItem, NSMenu, NSScreen, NSEvent, NSStatusWindowLevel,
        NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSNotificationCenter,
        NSEventMaskLeftMouseDown,
    )
except ImportError as _e:
    sys.stderr.write(
        "\n[wb-hover] 缺少 macOS GUI 依赖 pyobjc，悬浮球无法启动。\n"
        "  自动安装：python3 scripts/install.py\n"
        "  手动安装：pip3 install pyobjc-framework-Cocoa\n"
        f"  原始错误：{_e}\n\n"
    )
    sys.exit(1)

# ---------- 公共层（与 Windows 版共用：路径 / 取数 / 格式化 / 卡片文案）----------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wb_hover_core import (            # noqa: E402
    load_paths, query_db, api_healthy, card_text,
    fmt_tokens, fmt_dur, fmt_ago, fmt_duration,
    load_pos, save_pos, report_state, log_exception,
    POLL_SEC, ACTIVE_WINDOW_SEC, STATE_FILE, ERR_LOG,
)

_P = load_paths()
DB_PATH = _P["db_path"]
WB_DB = _P["workbuddy_db"]
POS_FILE = _P["pos_file"]
DASHBOARD = _P["dashboard_url"]

# ---------- 配色（腾讯电脑管家加速球风格：亮蓝渐变球 + 白字） ----------
BG_DARK = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x12/255, 0x17/255, 0x2B/255, 0.97)   # 弹窗面板底色
BALL_HI = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x4D/255, 0xAD/255, 0xFF/255, 1.0)   # 球体高光蓝
BALL_LO = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x1E/255, 0x6E/255, 0xF5/255, 1.0)   # 球体深蓝
WHITE = NSColor.whiteColor()
WHITE_SOFT = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.85)
ONLINE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x3E/255, 0xD6/255, 0x8A/255, 1.0)
OFFLINE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0xFF/255, 0x6B/255, 0x6B/255, 1.0)
ACTIVE_C = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x4D/255, 0xA3/255, 0xFF/255, 1.0)
TXT = NSColor.colorWithCalibratedRed_green_blue_alpha_(0xEA/255, 0xF0/255, 0xFF/255, 1.0)
SUB = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x9A/255, 0xAC/255, 0xDF/255, 1.0)
GOLD = NSColor.colorWithCalibratedRed_green_blue_alpha_(0xFF/255, 0xD4/255, 0x79/255, 1.0)
CARD = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x1B/255, 0x22/255, 0x40/255, 1.0)
BORDER = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x2A/255, 0x35/255, 0x60/255, 1.0)
# 看板（dashboard.html）同款暗蓝科技风
DB_BG = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x0F/255, 0x17/255, 0x2A/255, 0.98)   # --bg #0f172a
DB_CARD = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x1E/255, 0x29/255, 0x3B/255, 1.0)   # --card #1e293b
DB_LINE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x2D/255, 0x3A/255, 0x52/255, 1.0)   # --line #2d3a52
DB_BLUE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x60/255, 0xA5/255, 0xFA/255, 1.0)   # 强调蓝 #60a5fa
DB_GRAY = NSColor.colorWithCalibratedRed_green_blue_alpha_(0x8E/255, 0xA0/255, 0xB8/255, 1.0)   # 次级文字 #8ea0b8
DB_TXT = NSColor.colorWithCalibratedRed_green_blue_alpha_(0xE2/255, 0xE8/255, 0xF0/255, 1.0)   # 主文字 #e2e8f0


# 说明：fmt_tokens / fmt_dur / fmt_ago / fmt_duration / query_db / api_healthy
# 全部来自 wb_hover_core（与 Windows 版共用同一份口径），此处不再重复实现。


def draw_text(s, x, y, size, bold=False, color=None):
    fname = "PingFangSC-Semibold" if bold else "PingFang SC"
    font = NSFont.fontWithName_size_(fname, size) or NSFont.systemFontOfSize_(size)
    NSString.stringWithString_(s).drawAtPoint_withAttributes_(
        NSMakePoint(x, y),
        {NSFontAttributeName: font,
         NSForegroundColorAttributeName: color or NSColor.blackColor()})


def draw_text_center(s, cx, y, size, bold=False, color=None):
    """以 cx 为水平中心绘制文本（自动测量宽度，保证居中）。"""
    fname = "PingFangSC-Semibold" if bold else "PingFang SC"
    font = NSFont.fontWithName_size_(fname, size) or NSFont.systemFontOfSize_(size)
    w = NSString.stringWithString_(s).sizeWithAttributes_(
        {NSFontAttributeName: font}).width
    draw_text(s, cx - w / 2, y, size, bold, color)


# ---------- 圆形悬浮球视图 ----------
class BallView(NSView):
    def initWithApp_(self, app):
        self.app = app
        self.kpi = None
        self.active = []
        self.db_ok = False
        self.api_ok = None
        self._down = None
        self._down_origin = None
        self._pending = 0.0
        self._last_double = 0.0
        self._moved = False
        return objc.super(BallView, self).init()

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        w, h = self.bounds().size.width, self.bounds().size.height
        d = min(w, h) - 6
        cx = w / 2
        # 暗蓝球体（与看板/弹窗统一：--bg 底 + --line 描边）
        ball = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect((w - d) / 2, (h - d) / 2, d, d))
        DB_BG.set()
        ball.fill()
        DB_LINE.set()
        ball.setLineWidth_(2.0)
        ball.stroke()
        # 状态点（在线绿/离线红）
        (OFFLINE if (self.api_ok is False or not self.db_ok) else ONLINE).set()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(w - 22, 4, 8, 8)).fill()
        if self.db_ok:
            # 只显示活跃对话数：大数字 + 标签（水平居中，看板文字色）
            draw_text_center(str(len(self.active)), cx, 20, 26, True, DB_TXT)
            draw_text_center("活跃", cx, 56, 10, False, DB_GRAY)
        else:
            draw_text_center("·", cx, 28, 24, True, DB_TXT)
            draw_text_center("等待数据", cx, 56, 9, False, DB_GRAY)

    # ---- 鼠标（拖动用 NSEvent.mouseLocation 屏幕坐标，方向不反转；位置钳制在屏幕内） ----
    def _clamped_origin(self, nx, ny):
        """把目标位置钳制在可见屏幕内，确保永远拖不出边界。"""
        vf = NSScreen.mainScreen().visibleFrame()
        w, h = self.window().frame().size.width, self.window().frame().size.height
        x = max(vf.origin.x + 8, min(nx, vf.origin.x + vf.size.width - w - 8))
        y = max(vf.origin.y + 8, min(ny, vf.origin.y + vf.size.height - h - 8))
        return NSMakePoint(x, y)

    def mouseDown_(self, ev):
        self._down = NSEvent.mouseLocation()   # 屏幕坐标（左下原点，与窗口 frame 同系）
        self._down_origin = self.window().frame().origin
        self._moved = False

    def mouseDragged_(self, ev):
        if self._down is None:
            return
        p = NSEvent.mouseLocation()
        dx = p.x - self._down.x
        dy = p.y - self._down.y
        if abs(dx) + abs(dy) > 3:
            self._moved = True
            o = self._down_origin
            self.window().setFrameOrigin_(self._clamped_origin(o.x + dx, o.y + dy))

    def mouseUp_(self, ev):
        if self._down is None:
            return
        if self._moved:          # 拖动结束：记住位置，重启还原
            self.app.save_ball_pos(self.window().frame().origin)
            self._down = None
            return
        now = time.time()
        if ev.clickCount() == 2:
            self._last_double = now
            self._pending = 0.0
            self.app.openDashboard()
        elif ev.clickCount() == 1:
            self._pending = now
            self.performSelector_withObject_afterDelay_(
                objc.selector(self.doSingle), None, 0.22)

    def doSingle(self):
        if self._pending and time.time() - self._pending >= 0.2 \
           and time.time() - self._last_double > 0.45:
            self.app.togglePopup()

    def rightMouseDown_(self, ev):
        m = NSMenu.alloc().init()
        i1 = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "打开看板", "openDashboard", "")
        i1.setTarget_(self.app)
        i2 = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "退出悬浮球", "quitApp", "")
        i2.setTarget_(self.app)
        m.addItem_(i1)
        m.addItem_(i2)
        m.popUpMenuPositioningItem_atLocation_inView_(
            None, self.convertPoint_fromView_(ev.locationInWindow(), None), self)


# ---------- 活跃对话面板视图 ----------
class PopupView(NSView):
    def initWithApp_(self, app):
        self.app = app
        self.items = []
        return objc.super(PopupView, self).init()

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        w, h = self.bounds().size.width, self.bounds().size.height
        # 暗蓝背景（与看板 dashboard.html 风格一致）
        DB_BG.set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, w, h), 12, 12).fill()
        DB_LINE.set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(1, 1, w - 2, h - 2), 12, 12).stroke()
        draw_text("活跃对话", 14, 10, 12, True, DB_TXT)
        draw_text("✕", w - 26, 10, 13, False, DB_GRAY)
        items = self.items
        if not items:
            draw_text("当前无活跃对话", w / 2 - 40, h / 2, 12, False, DB_GRAY)
            return
        y = 42
        for it in items[:6]:
            y = card(it, y, w)
            if y > h - 52:
                break

    def mouseDown_(self, ev):
        p = self.convertPoint_fromView_(ev.locationInWindow(), None)
        w = self.bounds().size.width
        if p.x > w - 36 and p.y < 28:      # ✕ 关闭
            self.app.hidePopup()


def card(it, y, w):
    # 看板卡片（--card #1e293b）+ 边框（--line），文字用看板色系
    DB_CARD.set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(10, y, w - 20, 62), 8, 8).fill()
    DB_LINE.set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(10, y, w - 20, 62), 8, 8).stroke()
    t = card_text(it)      # 与 Windows 版共用同一份卡片文案（core 统一口径）
    draw_text(t["title"], 20, y + 8, 11, True, DB_TXT)
    draw_text(t["info"], 20, y + 27, 10, False, DB_BLUE)
    draw_text(t["req"], 20, y + 43, 9, False, DB_GRAY)
    return y + 66


def make_panel(origin, w, h, activating=False):
    """activating=True：可激活面板（点击可激活应用，用于弹窗——配合应用失活通知自动关闭）。
    activating=False：非激活面板（不抢焦点，用于悬浮球本体）。"""
    style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    if activating:
        style = NSWindowStyleMaskBorderless
    p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(origin.x, origin.y, w, h), style, NSBackingStoreBuffered, False)
    p.setLevel_(NSStatusWindowLevel)        # 永远置顶
    p.setOpaque_(False)
    p.setBackgroundColor_(NSColor.clearColor())
    p.setHasShadow_(True)
    p.setHidesOnDeactivate_(False)
    p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
    p.setIgnoresMouseEvents_(False)
    return p


# ---------- 应用组装 ----------
class HoverApp(NSObject):
    def init(self):
        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.app.finishLaunching()   # 关键：缺省时窗口不会注册到 WindowServer（实测）

        vf = NSScreen.mainScreen().visibleFrame()
        size = 88   # 2/3 直径
        saved = load_pos(POS_FILE)     # (x, y) 或 None
        if saved:                      # 恢复上次位置（load_pos 已校验 x/y 存在）
            bx = max(vf.origin.x + 8, min(saved[0], vf.origin.x + vf.size.width - size - 8))
            by = max(vf.origin.y + 8, min(saved[1], vf.origin.y + vf.size.height - size - 8))
        else:
            bx = vf.origin.x + vf.size.width - size - 24    # 右缘
            by = vf.origin.y + 24                           # 贴底部 → 右下角默认位

        self.ball = make_panel(NSMakePoint(bx, by), size, size)
        self.ball_view = BallView.alloc().initWithApp_(self)
        self.ball.setContentView_(self.ball_view)
        self.ball.orderFrontRegardless()

        self.pw, self.ph = 380, 420
        self.popup = make_panel(NSMakePoint(-3000, -3000), self.pw, self.ph,
                                activating=True)   # 可激活：点击其他应用 → 应用失活 → 自动关闭
        self.popup_view = PopupView.alloc().initWithApp_(self)
        self.popup.setContentView_(self.popup_view)

        # 应用失活（点击其他应用）→ 自动关闭弹窗（依赖应用先激活，仅兜底）
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, objc.selector(self.appDidResignActive_), "NSApplicationDidResignActiveNotification", None)
        self._global_click_mon = None   # 全局鼠标监控：弹窗显示期间点外部 → 关闭（主机制）

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_SEC, self, "tick:", None, True)
        self.tick_(None)      # 首帧立即刷新
        return objc.super(HoverApp, self).init()

    # ---- 定时刷新 ----
    def tick_(self, timer):
        kpi, active, ok = query_db(DB_PATH, WB_DB)   # 失败时返回 (None, None, False)，不再抛
        self.ball_view.kpi = kpi
        if active is not None:         # 只有明确读到结果才更新；读失败保留上次值（防闪烁）
            self.ball_view.active = active
        self.ball_view.db_ok = ok
        self.ball_view.api_ok = api_healthy(DASHBOARD)
        self.ball_view.setNeedsDisplay_(True)
        if self.popup.isVisible():
            self.popup_view.items = self.activeItems()
            self.popup_view.setNeedsDisplay_(True)
        report_state(os.getpid(), bool(self.ball.isVisible()), len(self.app.windows()),
                     [self.ball.frame().origin.x, self.ball.frame().origin.y],
                     len(active or []), ok, self.ball_view.api_ok)

    def activeItems(self):
        return self.ball_view.active      # 无活跃即为空，不兜底显示旧会话

    # ---- 交互动作 ----
    def appDidResignActive_(self, note):
        """点击其他应用 → 应用失活 → 关闭弹窗（悬浮球本体不受影响）。"""
        self.hidePopup()

    def togglePopup(self):
        if self.popup.isVisible():
            self.hidePopup()
        else:
            self.popup_view.items = self.activeItems()
            self.popup_view.setNeedsDisplay_(True)
            b = self.ball.frame()
            px = b.origin.x - self.pw - 10
            if px < 20:
                px = b.origin.x + b.size.width + 10
            vf = NSScreen.mainScreen().visibleFrame()
            py = b.origin.y + b.size.height - self.ph
            py = max(vf.origin.y + 20, min(py, vf.origin.y + vf.size.height - self.ph - 20))
            self.popup.setFrameOrigin_(NSMakePoint(px, py))
            self.popup.orderFrontRegardless()
            self._watchOutsideClick()

    def _watchOutsideClick(self):
        """弹窗显示期间监听全局鼠标左键：点击弹窗外部 → 自动关闭。
        不依赖应用激活状态（球体是 NonactivatingPanel，打开弹窗不会激活应用，
        此时 NSApplicationDidResignActiveNotification 永不触发），全局监控兜底。"""
        if self._global_click_mon is None:
            def _on_global_click(event):
                self._popupOutsideClick_(event)
            self._global_click_mon = \
                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    NSEventMaskLeftMouseDown, _on_global_click)

    def _popupOutsideClick_(self, event):
        try:
            if not self.popup.isVisible():
                return
            f = self.popup.frame()
            p = NSEvent.mouseLocation()   # 屏幕坐标，与 frame 同系（左下原点）
            inside = (f.origin.x <= p.x <= f.origin.x + f.size.width and
                      f.origin.y <= p.y <= f.origin.y + f.size.height)
            if not inside:                # 点在弹窗之外（桌面/其他应用/悬浮球）→ 关闭
                self.hidePopup()
        except Exception:
            pass

    def hidePopup(self):
        self.popup.orderOut_(None)

    def save_ball_pos(self, origin):
        save_pos(POS_FILE, origin.x, origin.y)

    def openDashboard(self):
        webbrowser.open(DASHBOARD)   # 每次双击只开一次

    def quitApp(self):
        self.app.terminate_(None)

    def run(self):
        self.app.run()


def main():
    try:
        HoverApp.alloc().init().run()
    except Exception:
        log_exception("[macOS]")
        raise


if __name__ == "__main__":
    main()
