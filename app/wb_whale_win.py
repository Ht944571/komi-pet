#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb_whale_win.py — WorkBuddy 用量看板 · 古见同学桌宠（Windows 原生，纯 ctypes + GDI+）
=====================================================================================
2026-09-28 形态收敛：**桌宠只有一种形态** —— AI 视频转来的「抱着本子写字的她」

  · 桌宠本体 = 写字帧序列（`assets/anim/write/`，49 帧 @12fps 真透明 PNG）
    有任务在跑 → 循环播写字段；任务完成 → 播一遍翻页展示段；其余时间就是写字常态
  · 气泡 = 深蓝描边椭圆想法框 + 双小圆点（数据态 / OK 完成态）
  · 交互：拖拽四边吸附、按压 Q 弹、双击开看板（自愈拉起服务）、
    右键菜单（大小 0.6–2.5x / 气泡开关 / 退出）

⚠️ 已删除、别加回来的东西（都在 git 历史里）：v3 八表情立绘 + 分层差分眨眼 +
   状态交叉溶解 + 点击部位切形态 + 程序化写字本子 + 高冷版 alt + 配件层 +
   3D 立体摆件线 + Live2D 线 —— 理由见 docs/ 下对应的交接文档。

数据链路零改动：wb_hover_core（workbuddy.db working 会话 + wb_usage_dw ODS），
与 Web 看板同一份口径。依赖：仅 Python 标准库（ctypes）+ 系统 gdiplus。
启动：pythonw wb_whale_win.py（或 hover.py 自动分发）。
"""

import ctypes
import json
import math
import os
import random
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes as wt
from urllib.parse import urlparse

try:
    import winsound
except ImportError:
    winsound = None

# ---------- 公共层 ----------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from wb_hover_core import (                                   # noqa: E402
    load_paths, query_db, api_healthy,
    load_pos, save_pos, report_state, log_exception, POLL_SEC, ERR_LOG,
    rotate_if_large, today_timeline,
    fmt_tokens, fmt_duration, fmt_duration_live, fmt_ago,
    stale_working_sessions, repair_stale_working, turn_src,
)

# ---------- 运行时环境适配（源码运行 / 打包 exe：可写目录与自身唤起命令）----------
import wb_runtime as RT                                       # noqa: E402

# ---------- 动效设计系统（节奏 / 幅度 / 缓动 / 情绪 / 降级，全部集中管理）----------
import wb_motion as MOTION                                    # noqa: E402

# ---------- 写字动作：帧序列素材 + 相位状态机（纯逻辑，可直接单测）----------
import wb_anim as ANIM                                         # noqa: E402

# ---------- 跟随模式（聚焦信号）：纯逻辑层（去抖状态机 / 前台→agent 推断）----------
import wb_follow as FOLLOW                                    # noqa: E402

# ---------- 登记册（跟随的 window_hints / accent 来源）----------
import wb_agent_registry as REG                               # noqa: E402

# ---------- 活跃探测（哪些 agent 现在"在用" → 「联动关闭」的宿主判据）----------
# ⚠️ 配件层（猫/贝雷帽/鲸鱼玩偶）已于 2026-09-27 整体删除；
#    PRESENCE **保留** —— 它同时是 _host_alive 的判据，不是配件专属。
#    这里仍整体 try 兜底：探测起不来也不能连累桌宠本体。
try:
    import wb_agent_presence as PRESENCE                      # noqa: E402
except Exception:                                             # pragma: no cover
    PRESENCE = None

# ---------- 布局（基准 scale=1.0，实际尺寸 = 基准 × self.scale）----------
BASE_W = 300                   # 窗口宽
BASE_BUB_H = 128               # 气泡椭圆高
BASE_BUBBLE_H = 162            # 气泡区总高（含想法小圆）
# 主气泡椭圆底 → 立绘实际头顶 的连线跨度（基准 scale=1.0）。
# ⚠️ 气泡**不再钉死在窗口顶部**，而是锚在「头顶上方 BUBBLE_CHAIN_PX」处 ——
# ⚠️ 气泡**不再钉死在窗口顶部**，而是锚在「写字帧内容顶上方 BUBBLE_CHAIN_PX」处 ——
# 帧上方有大量透明留白，钉死在顶部会让气泡离脑袋空出一大截。
# 钉死在顶部会让 idle/stone 时气泡离脑袋空出 100+px。
# 调大 = 气泡离脑袋更远，调小 = 更近。
BUBBLE_CHAIN_PX = 40
# 写字帧区高。帧画布是 420×565（内容只占约 64% 高，见 tools/video_to_pet_frames.py），
# 若仍用旧的 210，角色会比旧版小约 40%。按内容反算：
#   旧 823×981 画到 210px → 角色实高 ≈ 207px；新 idle 内容 999/1627 → 210×1627/999 ≈ 342
BASE_PET_H = 342               # 立绘区高（v3 统一画布）
DRAG_EDGE = 24                 # 距屏幕边缘 24px 内松手 → 贴边吸附
TICK_MS = int(POLL_SEC * 1000)
WATCH_MS = 60
ID_TIMER_TICK, ID_TIMER_CLICK, ID_TIMER_WATCH = 1, 2, 4
EVENT_LOG = os.path.join(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "C:/Windows/Temp",
                         "komi-wb-hover.events.log")   # komi- 前缀：与旧技能遗留进程隔离（P1）
DASH_READY_WAIT = 8.0
_HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠️ **可写**文件（窗口位置 / 设置）走 RT.data_dir()：
#    打包后 = %LOCALAPPDATA%\KomiPet；源码运行 = app/ —— 与打包前完全一致。
#    只读资源（立绘等）继续从**包内**读（bundle_dir()），装到只读目录也不怕。
POS_FILE = os.path.join(RT.data_dir(), ".whale_pos.json")
SETTINGS_FILE = os.path.join(RT.data_dir(), ".whale_settings.json")
ASSETS_DIR = os.path.join(RT.bundle_dir(), "assets")
# 写字帧序列目录（桌宠唯一形态）——由 tools/video_to_pet_frames.py 生成。
ANIM_DIR = os.path.join(ASSETS_DIR, "anim")


# ---------- 配色（古见同学主题：制服蓝 / 领结红 / 深紫黑 / 灰紫，对齐古见同学展示页）----------
C_BUBBLE = 0xFFFDFBF6          # 气泡米白底（展示页 --bg #F7F4EE 的亮阶）
C_BUBBLE_LINE = 0xFF39406B     # 气泡描边制服蓝（展示页 --navy）
C_TXT_HEAD = 0xFFB4364F        # 标题领结红（展示页 --crimson 强调色）
C_TXT_MAIN = 0xFF2A2438        # 正文深紫黑（展示页 --ink）
C_TXT_DIM = 0xFF6A6284         # 次级灰紫（展示页 --ink-2）
C_TXT_PINK = 0xFFE89AAE        # 积分强调樱花粉（展示页 --sakura，"本轮 X 积分"专用）
C_SHADOW = 0x1F39406B          # 立绘底部浅影（制服蓝淡）

# ---------- 气泡形态（状态机两态）----------
BUBBLE_DEFAULT = "default"     # 数据态：三行文案（正在对话 / 最近对话 …）
BUBBLE_OK = "ok"               # 完成态：OK 字形 + 连点进度（对话任务结束时进入）

# 对话窗口识别：WorkBuddy 主窗口标题含这些关键字之一即视为"对应的对话窗口"。
# 自动消失逻辑据此判断"用户是否回到了对话窗口"（标题探测失败时有句柄缓存兜底）。
# 可用环境变量 WB_HOST_TITLE_HINTS（逗号分隔）覆盖——测试钩子，线上默认不变。
WB_HOST_TITLE_HINTS = tuple(
    s.strip() for s in os.environ.get("WB_HOST_TITLE_HINTS", "WorkBuddy,CodeBuddy").split(",")
    if s.strip()) or ("WorkBuddy",)

# 联动关闭：WorkBuddy 宿主进程名（与守望进程 wb_whale_watcher.py 保持一致）。
# WB_LINKED_TARGET_EXE 可用环境变量覆盖——测试钩子（对齐守望的 WB_WATCHER_TARGET_EXE）。
WB_PROC_NAME = "WorkBuddy.exe"
WB_LINKED_TARGET_ENV = "WB_LINKED_TARGET_EXE"
# ---- OK 态几何规格（单一来源：绘制 / 测试断言 / 预览对比都走 ok_spec()）----
#
# 设计取向「整个泡泡整体变成 OK 的样式」的落地方式：
#   ① 复用数据态已有的元素 —— 同一椭圆（同宽、同高、同 3.5px 描边、同白底）、
#      同一想法小圆尾巴、同一字形与配色体系；几何相位完全不动，切换时不跳版。
#   ② 只做必要调整 —— 主色藏蓝 → OK 橙；内容由「三行文案」换成「OK 图形主视觉」。
#   ③ 图形高度以气泡高 bh 为基准，随 scale 自动等比，不需要为每个缩放级别再调数。
OK_GLYPH_SRC_AR = 162 / 266    # ok_glyph.png 源图宽高比（等比缩放用）
OK_BUB_TOP_R = 4 / 128         # 椭圆上沿 by = bh * OK_BUB_TOP_R（与数据态 _draw_bubble 一致）
OK_CAP_LINE = 1.5              # 文案行高倍数（Surface.ctext 的占位行高）

# 候选方案（比例均相对气泡高 bh）——"cap"=None 表示不画文案，"dots"=None 表示不画进度点。
# OK_STYLE 选定其一为线上默认；其余保留供 tools/preview_ok_styles.py 出对比图。
OK_STYLES = {
    # 图形主导：一颗大 OK 占满整颗气泡，最贴参考图
    "A": {"glyph_h": 0.86, "glyph_cy": 0.50, "cap": None, "dots": None},
    # 图形主导 + 三颗进度点：保留"点三次"的可发现性，无文字（推荐）
    "B": {"glyph_h": 0.72, "glyph_cy": 0.42, "cap": None, "dots": 0.88},
    # 图形 + 一行文案 + 进度点：信息最完整，仍比原版图形大得多
    "C": {"glyph_h": 0.58, "glyph_cy": 0.33, "cap": 0.70, "dots": 0.91},
    # 紧凑徽章：椭圆按图形收窄（水平居中），整颗泡泡就是一枚 OK 徽章
    "D": {"glyph_h": 0.74, "glyph_cy": 0.46, "cap": None, "dots": 0.89,
          "bub_w_r": 0.86},
}
OK_STYLE = "D"                 # 线上默认方案


def ok_spec(lay, style=None, bub_y=None):
    """OK 态几何规格：比例 → 像素的唯一换算点。

    绘制（_draw_bubble_ok）、测试断言（A12）、预览出图三处共用同一组数字，
    避免"改了一处、另一处还是老数"的漂移。返回 dict：
      椭圆盒 bub_x/bub_y/bub_w/bub_h、图形盒 g_cx/g_cy/g_w/g_h、
      文案 cap_y/cap_font（可能为 None）、进度点 dots_cy/dot_r/dot_gap（可能为 None）

    `bub_y`：主气泡椭圆上沿。**不传**则沿用旧口径（贴在窗口顶部，`bh * OK_BUB_TOP_R`）——
    测试与预览出图保持向后兼容；**线上绘制会传入锚定头顶的实际位置**
    （见 `WhalePet._bubble_box`），保证 OK 态与数据态的气泡停在同一个地方。
    """
    st = OK_STYLES.get(style or OK_STYLE) or OK_STYLES[OK_STYLE]
    sc, W, bh = lay["sc"], lay["W"], lay["bub_h"]
    by = bh * OK_BUB_TOP_R if bub_y is None else bub_y
    bub_w = bh * st["bub_w_r"] if "bub_w_r" in st else W - 12 * sc
    gh = bh * st["glyph_h"]
    gw = gh * OK_GLYPH_SRC_AR
    return {
        "sc": sc, "W": W, "bh": bh, "by": by,
        # 收窄时水平居中，避免椭圆靠左（气泡始终对着立绘头顶）
        "bub_x": (W - bub_w) / 2, "bub_y": by, "bub_w": bub_w, "bub_h": bh,
        "g_cx": W / 2, "g_cy": by + bh * st["glyph_cy"], "g_w": gw, "g_h": gh,
        "cap_y": None if st["cap"] is None else by + bh * st["cap"],
        "cap_font": bh * (10.5 / 128),
        "dots_cy": None if st["dots"] is None else by + bh * st["dots"],
        "dot_r": bh * (3.6 / 128), "dot_gap": bh * (12 / 128),
    }

# ---------- 互动反应：古见同学风格台词（长按时用）----------
# 古见人设：交流障碍症，几乎不说话，用笔记本/动作/微表情表达，内心戏丰富
# ⚠️ 2026-09-28：按点击频率分档的三组（SHY/POUT/OUTBURST）与按备注部位的三组
#   （HEAD/FACE/SKIRT）随「点击切形态」一并删除，只剩长按用的这一组。
QUOTES_TSUNDERE = [                  # 长按：紧张（开始石像化，移开视线）
    "（身体开始僵硬）……人、人有点多",
    "（写：请、请轻一点）",
    "（往后缩了半步，长发跟着抖了抖）",
    "（偷偷看你一眼，又飞快移开视线）",
]
REACT_WINDOW = 5.0                   # 点击计数的滑动窗口（秒）
ANIM_MS = 41                         # 动画帧间隔（≈24fps，**与帧素材同帧率**）
# ⚠️ 这个值必须 ≥ 素材帧率，否则等于白转：2026-09-28 素材按源片 24fps 重转后，
#    这里还停在 66ms（15fps）会每秒丢掉 9 帧。改 41ms ≈ 24.4fps。
# ⚠️ 光有 41 还不够 —— 系统默认定时器粒度是 15.6ms，SetTimer(41) 实际会被拉长到
#    **46.8ms**：2026-09-30 实测写字态只有 21.5fps，且按墙钟取帧会**不规则跳帧**
#    （111 个 tick 里 14 次一次跳 2 帧 >> 用户看到的"写字不流畅"）。
#    解法 = `_high_res_timer_on()`（进程级 timeBeginPeriod(1)）+ 取帧时「单 tick 最多推进 1 帧」。
IDLE_AFTER = float(os.environ.get("WB_WHALE_IDLE_AFTER", "20"))      # 无交互 N 秒后进入自主玩耍
BEHAVIOR_MIN = float(os.environ.get("WB_WHALE_BEHAVIOR_MIN", "6"))   # 自主行为间隔下限
BEHAVIOR_MAX = float(os.environ.get("WB_WHALE_BEHAVIOR_MAX", "14"))  # 上限（随机化防机械感）
GREET_DELAY = float(os.environ.get("WB_WHALE_GREET_DELAY", "1.5"))   # 启动后打招呼延迟

# ---- 「写字态」防卡死三件套（2026-09-29 事故后加）----
# 事故现象：桌宠一直卡在写字状态，怎么都不坐下。
# 三个独立成因，这里各配一道闸：
#   ① 判据抖动：宿主 working 行/数据源抖动 → n 在 0↔1 之间跳（实测一天 142 次
#      write_task_start、只有 3 次 done）→ 她不停上演"拿本子→坐下→拿本子"。
#      → 活跃数**下降**加确认窗口（上升立即生效，来任务还是要马上响应）。
ACTIVE_FALL_DEBOUNCE_S = 3.0
#   ② 读数持续失败：数仓读不出来时，为了不闪烁会保留上次的 active，
#      于是"永远记得有任务在跑"。连续失败超过这个时长就不再替它记着。
READ_STALE_SEC = 90.0
#   ③ 残留 working 行：原来只在**启动时**修一次，中途卡住的会话要等下次重启才治。
#      改成周期自愈（后台线程，判定仍走保守的 WB_STALE_AFTER_SEC=900）。
REPAIR_EVERY_SEC = 600.0

# ---------- Win32 常量 ----------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
# ⚠️ WS_EX_TRANSPARENT = 鼠标**穿透**（窗口不接受命中，消息落给下层）。
#    它和 WS_EX_TOPMOST(0x8) 长得很像但完全是两回事 —— 2026-09-29 菜单窗口就是
#    把 0x20 当成 TOPMOST 手写进去，导致「菜单被桌宠盖住 + 鼠标全穿透」。
#    **窗口扩展样式一律用具名常量，禁止再写字面量。**
WS_EX_TRANSPARENT = 0x00000020
WM_TIMER = 0x0113
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_MOUSEMOVE = 0x0200
WM_MOUSEACTIVATE = 0x0021
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NULL = 0x0000
WM_COMMAND = 0x0111           # 菜单命令通知（TPM_RETURNCMD 下不出现，防御性忽略）
MA_NOACTIVATE = 3
MK_LBUTTON = 0x0001
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
SW_SHOWNA = 8
SW_HIDE = 0
ULW_ALPHA = 0x00000002
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
VK_LBUTTON = 0x01
SPI_GETWORKAREA = 0x0030
MF_STRING = 0x00000000
MF_CHECKED = 0x00000008
MF_SEPARATOR = 0x00000800
MF_GRAYED = 0x00000001
MF_POPUP = 0x00000010
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100
SMOOTHING_ANTIALIAS = 4
TEXT_HINT_ANTIALIAS = 4
PIXEL_OFFSET_HIGHQUALITY = 2
INTERP_HIGHQUALITY = 7         # HighQualityBicubic（立绘缩放质量）
UNIT_PIXEL = 2
FONT_REGULAR, FONT_BOLD = 0, 1
IDM_DASHBOARD, IDM_QUIT, IDM_BUBBLE, IDM_SOUND = 1001, 1002, 1003, 1004
IDM_OK_AUTO = 1006             # 完成态自动收起开关（P2：不再只能改 wb_motion 重启）
IDM_LINKED = 1007              # 随 Agent 退出联动关闭开关（P2 同上）
IDM_FOLLOW = 1008              # 跟随前台切换聚焦开关（跟随模式 P1）
IDM_PIN = 1009                 # 锁定聚焦（pin）开关（跟随模式 P3）
IDM_TIMELINE0 = 1400           # 1400：今日时间线摘要项；1401+i ↔ 最近轮次[i]
IDM_FOCUS0 = 1450              # 1450+i ↔ 手动聚焦候选[i]（登记册启用 agent）
IDM_HANDOFF = 1010             # 生成接续摘要 → 剪贴板（跟随模式 P4；仅角标活跃时出现）
IDM_UPDATE = 1011              # 检查更新 / 更新到 vX（仅配置了更新源时出现）
IDM_REPAIR = 1012              # 修复卡死会话（残留 working 清库；仅检测到残留时出现）
ID_HOTKEY_FOLLOW = 1           # 全局热键 id（RegisterHotKey 的 id 命名空间独立于定时器）
IDM_SCALE0 = 1100              # 1100+i ↔ SCALES[i]
SCALES = (0.6, 0.8, 1.0, 1.5, 2.0, 2.5)
IDM_QUALITY0 = 1200            # 1200+i ↔ QUALITY_CHOICES[i]
QUALITY_CHOICES = (
    (MOTION.QUALITY_FULL, "完整"),
    (MOTION.QUALITY_LITE, "精简"),
    (MOTION.QUALITY_OFF, "关闭"),
)
ID_TIMER_ANIM = 5              # 反应动画定时器
CLASS_NAME = "WBWhalePetClass"

# ---- 右键菜单 owner-draw（美术 = 用量看板 dashboard.html 的设计语言）----
MF_OWNERDRAW = 0x00000100   # ⚠️ Win32 SDK 真值（曾手抄成 0xB0 → 系统把 itemData 当
                            #    字符串指针解引用 → access violation，菜单弹不出来）
WM_DRAWITEM = 0x002B        # ⚠️ SDK 真值（曾手抄成 0x021B——那是 WM_EXITMENULOOP）
WM_MEASUREITEM = 0x002C     # ⚠️ SDK 真值（曾手抄成 0x0211——那是 WM_ENTERMENULOOP，
                            #    lparam 恒 0 → cast 炸 NULL，measure 全落空 → 菜单缩成默认尺寸）
ODS_SELECTED, ODS_DISABLED, ODS_CHECKED = 0x0001, 0x0004, 0x0008
MIM_BACKGROUND, MIM_APPLYTOSUBMENUS = 0x00000002, 0x80000000
ETO_CLIPPED, TRANSPARENT_BK, PS_NULL, NULL_BRUSH_STOCK = 0x0004, 1, 5, 5
# 色板：web 十六进制（#RRGGBB），GDI 用前经 _bgr() 转 COLORREF(0x00BBGGRR)
MENU_BG      = 0xFFFFFF   # --card      菜单卡底
MENU_HOVER   = 0xEFEAF7   # --bg-soft   悬停/选中底（看板 .rs-list 选中态同款）
MENU_HOVER_D = 0xF7ECEF   # 悬停底的危险色变体（crimson 调淡）
MENU_INK     = 0x2A2438   # --ink       正文
MENU_INK_DIM = 0x9C94B5   # --ink-3     禁用 / 子菜单箭头
MENU_SEL_TX  = 0x4F3F6E   # --violet-d  选中文字
MENU_VIOLET  = 0x6B5B8A   # --violet    勾选圆点
MENU_DOT_OFF = 0xC9C2D6   # 未勾选圆点描边
MENU_LINE    = 0xE5DFEA   # --line      分隔线
MENU_DANGER  = 0xB4364F   # --crimson   危险项（退出 / 修复卡死会话）
MENU_DANGER_HOVER = 0x8E2A3E   # --crimson-d
MENU_ITEM_H, MENU_SEP_H, MENU_W = 32, 9, 272   # 96dpi 基准，按窗口 DPI 缩放


def menu_metrics(dpi):
    """菜单绘制度量（96dpi 基准按窗口 DPI 缩放）。"""
    s = dpi / 96.0
    return {"w": round(MENU_W * s), "h": round(MENU_ITEM_H * s),
            "sep_h": round(MENU_SEP_H * s), "inset": round(2 * s),
            "r": round(7 * s), "pad": round(10 * s), "pad_v": round(6 * s),
            "dot_x": round(22 * s), "dot_r": round(4 * s),
            "tx": round(36 * s), "arrow_w": round(20 * s),
            "font_h": max(11, round(13 * s)),
            "font_px": max(12, round(14 * s))}


def menu_item_style(meta, selected, checked, disabled):
    """菜单 item 的绘制决策（纯函数，配色=看板规范；test_menu_toggles [G] 断言）。

    对应看板 .rs-list button 的三态语言：常态墨色、悬停淡紫底+深紫粗体、
    禁用灰紫；开关项左侧实心/空心圆点；危险项（退出等）用领结红。
    """
    if meta.get("sep"):
        return {"kind": "sep"}
    danger = bool(meta.get("danger"))
    if disabled:
        return {"kind": "item", "bg": None, "text": MENU_INK_DIM, "bold": False,
                "dot": False, "dot_filled": False, "arrow": meta.get("arrow"),
                "danger": False}
    if selected:
        return {"kind": "item", "bg": MENU_HOVER_D if danger else MENU_HOVER,
                "text": MENU_DANGER_HOVER if danger else MENU_SEL_TX,
                "bold": True, "dot": True, "dot_filled": checked,
                "arrow": meta.get("arrow"), "danger": danger}
    return {"kind": "item", "bg": None, "text": MENU_DANGER if danger else MENU_INK,
            "bold": False, "dot": True, "dot_filled": checked,
            "arrow": meta.get("arrow"), "danger": danger}


# （原 owner-draw 的 _menu_fonts / _paint_menu_item 两个 GDI 绘制函数已随
#   TrackPopupMenu 方案退役删除；现在绘制在 MenuSession._render_item 里用
#   Surface 的 GDI+ 原语完成，配色仍走 menu_item_style。）

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_msimg32 = ctypes.WinDLL("msimg32", use_last_error=True)
try:
    _gdiplus = ctypes.WinDLL("gdiplus", use_last_error=True)
except OSError:
    _gdiplus = None
try:
    _winmm = ctypes.WinDLL("winmm", use_last_error=True)
except OSError:
    _winmm = None

_HIRES_TIMER_ON = False


def _high_res_timer_on():
    """把本进程的定时器精度提到 1ms（`timeBeginPeriod(1)`），返回是否成功。

    **为什么必须做**：Windows 默认定时器粒度 15.6ms → `SetTimer(41)` 实际 46.8ms。
    动画按墙钟取帧（`AnimClip.index`），于是每 8 个 tick 就有一次「一次跳 2 帧」，
    肉眼就是**一顿一顿**（2026-09-30 实测写字态 21.5fps / 111 tick 里 14 次跳帧）。
    提精度后 tick 回到 41ms → 真 24.4fps，跳帧基本消失。
    Win10 1803+ 起这是**进程级**设置，不影响其他程序；代价只是本进程略增唤醒频率。
    """
    global _HIRES_TIMER_ON
    if _HIRES_TIMER_ON:
        return True
    if _winmm is None:
        return False
    try:
        _winmm.timeBeginPeriod(1)
        _HIRES_TIMER_ON = True
        return True
    except Exception:
        return False


# ---- 高精度动画时钟（2026-09-30 修「写字不流畅」）----
# 实测：系统默认定时器粒度 15.6ms → `SetTimer(41)` 真实周期 **46.8ms**（动画 21.3fps）；
# 且本机 `timeBeginPeriod(1)` **无效**（调用成功、周期不变）。改用高精度可等待定时器
# （Win10 1803+ 的 `CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`，走内核高精度时钟）
# + 后台线程 PostMessage 投递 —— 实测能稳定给出 41ms。
WM_APP_ANIM = 0x8000 + 41            # 时钟线程投递的动画 tick（自绘消息，不走 WM_TIMER）

_CWT_HIGH_RESOLUTION = 0x00000002
_CWT_ALL_ACCESS = 0x1F0003
_WAIT_OBJECT_0 = 0x00000000


def _make_hires_timer(period_ms):
    """建一个 1ms 精度、周期 `period_ms` 的可等待定时器；系统不支持 → None。"""
    if _kernel32 is None:
        return None
    try:
        _kernel32.CreateWaitableTimerExW.restype = wt.HANDLE
        _kernel32.CreateWaitableTimerExW.argtypes = [
            ctypes.c_void_p, wt.LPCWSTR, wt.DWORD, wt.DWORD]
        h = _kernel32.CreateWaitableTimerExW(
            None, None, _CWT_HIGH_RESOLUTION, _CWT_ALL_ACCESS)
        if not h:
            return None
        _kernel32.SetWaitableTimer.argtypes = [
            wt.HANDLE, ctypes.POINTER(ctypes.c_longlong), ctypes.c_long,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        due = ctypes.c_longlong(-int(period_ms) * 10000)   # 100ns 单位；负数 = 相对时间
        if not _kernel32.SetWaitableTimer(h, ctypes.byref(due), int(period_ms),
                                          None, None, 0):
            _kernel32.CloseHandle(h)
            return None
        return h
    except Exception:
        return None


def _set_dpi_aware():
    try:
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore", use_last_error=True).SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


P = ctypes.c_void_p
F = ctypes.c_float
U32 = ctypes.c_uint32
INT_C = ctypes.c_int


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class MEASUREITEMSTRUCT(ctypes.Structure):
    _fields_ = [("CtlType", wt.UINT), ("CtlID", wt.UINT), ("itemID", wt.UINT),
                ("itemWidth", wt.UINT), ("itemHeight", wt.UINT),
                ("itemData", ctypes.c_size_t)]


class DRAWITEMSTRUCT(ctypes.Structure):
    _fields_ = [("CtlType", wt.UINT), ("CtlID", wt.UINT), ("itemID", wt.UINT),
                ("itemAction", wt.UINT), ("itemState", wt.UINT),
                ("hwndItem", wt.HWND), ("hDC", wt.HDC), ("rcItem", RECT),
                ("itemData", ctypes.c_size_t)]


class MENUINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwMask", wt.UINT), ("dwStyle", wt.UINT),
                ("cyMax", wt.UINT), ("hbrBack", wt.HBRUSH),
                ("dwContextHelpID", wt.DWORD), ("dwMenuData", ctypes.c_size_t)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", POINT)]


class RectF(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float),
                ("width", ctypes.c_float), ("height", ctypes.c_float)]


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", U32), ("DebugCallback", P),
                ("SuppressBackgroundThread", wt.BOOL),
                ("SuppressExternalCodecs", wt.BOOL)]


# ---------- 函数签名（不声明时 64 位句柄会被 ctypes 按 32 位截断 → 崩溃）----------
_user32.CreateWindowExW.restype = wt.HWND
_user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
_user32.GetDC.restype = wt.HDC
_user32.GetDC.argtypes = [wt.HWND]
_user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
_user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wt.UINT]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
_user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(POINT),
                                        ctypes.POINTER(SIZE), wt.HDC, ctypes.POINTER(POINT),
                                        wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
_user32.CreatePopupMenu.restype = wt.HMENU
_user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, ctypes.POINTER(RECT)]
_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.CreateDIBSection.restype = wt.HBITMAP
_gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
_gdi32.SelectObject.restype = wt.HGDIOBJ
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
_kernel32.GetModuleHandleW.restype = wt.HINSTANCE
_kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
_user32.DefWindowProcW.restype = ctypes.c_ssize_t
_user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_user32.LoadCursorW.restype = wt.HANDLE
_user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_user32.DestroyWindow.argtypes = [wt.HWND]
_user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_user32.SetTimer.restype = ctypes.c_size_t
_user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
_user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
_user32.SetCapture.argtypes = [wt.HWND]
_user32.ReleaseCapture.restype = ctypes.c_int
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetDoubleClickTime.restype = wt.UINT
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.SetForegroundWindow.argtypes = [wt.HWND]
_user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, ctypes.c_size_t]
_user32.DestroyMenu.argtypes = [wt.HMENU]
# ---- owner-draw 菜单：owner 窗口收测量/绘制消息，GDI 自绘 item ----
_user32.GetDpiForWindow.restype = wt.UINT
_user32.GetDpiForWindow.argtypes = [wt.HWND]
_user32.SetMenuInfo.restype = wt.BOOL
_user32.SetMenuInfo.argtypes = [wt.HMENU, ctypes.POINTER(MENUINFO)]
_user32.FillRect.restype = ctypes.c_int
_user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(RECT), wt.HBRUSH]
_gdi32.Polygon.restype = wt.BOOL
_gdi32.Polygon.argtypes = [wt.HDC, ctypes.POINTER(POINT), ctypes.c_int]
_gdi32.CreateSolidBrush.restype = wt.HBRUSH
_gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
_gdi32.CreatePen.restype = wt.HGDIOBJ
_gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.COLORREF]
_gdi32.CreateFontW.restype = wt.HFONT
_gdi32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [wt.LPCWSTR]
_gdi32.SetBkMode.restype = ctypes.c_int
_gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
_gdi32.SetTextColor.restype = wt.COLORREF
_gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
_gdi32.ExtTextOutW.restype = wt.BOOL
_gdi32.ExtTextOutW.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.UINT,
                               ctypes.POINTER(RECT), wt.LPCWSTR, wt.UINT,
                               ctypes.POINTER(ctypes.c_int)]
_gdi32.RoundRect.restype = wt.BOOL
_gdi32.RoundRect.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_int]
_gdi32.Ellipse.restype = wt.BOOL
_gdi32.Ellipse.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int]
_gdi32.GetStockObject.restype = wt.HGDIOBJ
_gdi32.GetStockObject.argtypes = [ctypes.c_int]
_user32.EnumWindows.restype = wt.BOOL
_user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
_user32.GetForegroundWindow.restype = wt.HWND
_user32.GetForegroundWindow.argtypes = []
_user32.GetWindowThreadProcessId.restype = wt.DWORD
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextLengthW.argtypes = [wt.HWND]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_user32.IsWindow.restype = wt.BOOL
_user32.IsWindow.argtypes = [wt.HWND]
_user32.IsWindowVisible.restype = wt.BOOL
_user32.IsWindowVisible.argtypes = [wt.HWND]

# ---- kernel32：联动关闭要"等 WorkBuddy 进程结束" + 进程快照兜底 ----
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.WaitForSingleObject.restype = wt.DWORD
_kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_kernel32.CloseHandle.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]

# ---- 跟随模式（跟随前台切换聚焦）：前台事件钩子 + 前台进程名查询 ----
# 设计：docs/桌宠跨Agent体验设计.md §2.2 —— 钩子优先（即时），轮询兜底；
# ⚠️ 钩子回调里绝不做重活：只记 hwnd，身份解析放动画帧（_follow_tick）。
_user32.SetWinEventHook.restype = ctypes.c_void_p
_user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                    ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD]
_user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENTPROC = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, wt.DWORD, wt.HWND,
                                 wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)
PROCESS_QUERY_LIMITED = 0x1000
_kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                                 ctypes.c_wchar_p,
                                                 ctypes.POINTER(wt.DWORD)]
_user32.GetWindowThreadProcessId.restype = wt.DWORD
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
# P3 全局热键：RegisterHotKey 到桌宠 hwnd，WM_HOTKEY 进既有消息泵（零轮询）
_user32.RegisterHotKey.restype = wt.BOOL
_user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
_user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
WM_HOTKEY = 0x0312
_kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.c_void_p]
_kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.c_void_p]

SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value      # 平台自适应（-1 的句柄形态）

_gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wt.HDC]
# AlphaBlend：预缩放立绘缓存的逐像素 alpha 合成（源为 GDI+ 输出的预乘 alpha 位图）
_msimg32.AlphaBlend.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wt.DWORD]
_msimg32.AlphaBlend.restype = wt.BOOL
_ALPHABLEND_BF = 0x01FF0000          # BLENDFUNCTION: AC_SRC_OVER + SrcAlpha=255 + AC_SRC_ALPHA


def _gp(name, argtypes):
    fn = getattr(_gdiplus, name)
    fn.argtypes = argtypes
    fn.restype = ctypes.c_int
    return fn


if _gdiplus is not None:
    _GdiplusStartup = _gp("GdiplusStartup", [P, P, P])
    _GdiplusShutdown = _gp("GdiplusShutdown", [P])
    _CreateFromHDC = _gp("GdipCreateFromHDC", [wt.HDC, P])
    _DeleteGraphics = _gp("GdipDeleteGraphics", [P])
    _SetSmoothing = _gp("GdipSetSmoothingMode", [P, INT_C])
    _SetTextHint = _gp("GdipSetTextRenderingHint", [P, INT_C])
    _SetPixelOffset = _gp("GdipSetPixelOffsetMode", [P, INT_C])
    _SetInterp = _gp("GdipSetInterpolationMode", [P, INT_C])
    _GraphicsClear = _gp("GdipGraphicsClear", [P, U32])
    _SolidFill = _gp("GdipCreateSolidFill", [U32, P])
    _DeleteBrush = _gp("GdipDeleteBrush", [P])
    _CreatePen = _gp("GdipCreatePen1", [U32, F, INT_C, P])
    _DeletePen = _gp("GdipDeletePen", [P])
    _FillEllipse = _gp("GdipFillEllipse", [P, P, F, F, F, F])
    _DrawEllipse = _gp("GdipDrawEllipse", [P, P, F, F, F, F])
    _CreatePath = _gp("GdipCreatePath", [INT_C, P])
    _DeletePath = _gp("GdipDeletePath", [P])
    _AddPathArc = _gp("GdipAddPathArc", [P, F, F, F, F, F, F])
    _AddPathLine = _gp("GdipAddPathLine", [P, F, F, F, F])
    _CloseFigure = _gp("GdipClosePathFigure", [P])
    _FillPath = _gp("GdipFillPath", [P, P, P])
    _DrawPath = _gp("GdipDrawPath", [P, P, P])
    _CreateFamily = _gp("GdipCreateFontFamilyFromName", [wt.LPCWSTR, P, P])
    _GenericFamily = _gp("GdipGetGenericFontFamilySansSerif", [P])
    _DeleteFamily = _gp("GdipDeleteFontFamily", [P])
    _CreateFont = _gp("GdipCreateFont", [P, F, INT_C, INT_C, P])
    _DeleteFont = _gp("GdipDeleteFont", [P])
    _CreateStringFormat = _gp("GdipCreateStringFormat", [INT_C, ctypes.c_ushort, P])
    _SetAlign = _gp("GdipSetStringFormatAlign", [P, INT_C])
    _SetLineAlign = _gp("GdipSetStringFormatLineAlign", [P, INT_C])
    _SetFormatFlags = _gp("GdipSetStringFormatFlags", [P, INT_C])
    _DeleteStringFormat = _gp("GdipDeleteStringFormat", [P])
    _DrawString = _gp("GdipDrawString", [P, wt.LPCWSTR, INT_C, P, P, P, P])
    _MeasureString = _gp("GdipMeasureString", [P, wt.LPCWSTR, INT_C, P, P, P, P, P, P])
    # ---- 立绘 PNG ----
    _LoadImage = _gp("GdipLoadImageFromFile", [wt.LPCWSTR, P])
    _DisposeImage = _gp("GdipDisposeImage", [P])
    _GetImageW = _gp("GdipGetImageWidth", [P, P])
    _GetImageH = _gp("GdipGetImageHeight", [P, P])
    _DrawImageRectI = _gp("GdipDrawImageRectI", [P, P, INT_C, INT_C, INT_C, INT_C])
    # 旋转贴图（平行四边形目标）：3 个目标点 = 左上 / 右上 / 左下。
    # 源矩形必须给**图片实际尺寸**，否则只取左上角一块（第一次踩的就是这个）。
    _DrawImagePoints = _gp("GdipDrawImagePointsRect",
                           [P, P, ctypes.POINTER(F), INT_C, F, F, F, F, INT_C, P, P, P])
    _CreateBmpFromHBITMAP = _gp("GdipCreateBitmapFromHBITMAP", [wt.HBITMAP, wt.HPALETTE, P])

_FONT_FAMILY = None
_STR_NOWRAP = 0x00001000


def _family():
    global _FONT_FAMILY
    if _FONT_FAMILY is not None:
        return _FONT_FAMILY
    for name in ("Yu Gothic", "游ゴシック", "Yu Gothic UI",
                 "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑"):
        fam = P()
        if _CreateFamily(name, None, ctypes.byref(fam)) == 0:
            _FONT_FAMILY = fam
            return fam
    _FONT_FAMILY = P()
    _GenericFamily(ctypes.byref(_FONT_FAMILY))
    return _FONT_FAMILY


class Surface:
    """离屏画布（自上而下坐标；字体/画笔/格式缓存复用）。"""

    _fonts = {}
    _fmt_cache = {}

    def __init__(self, w, h):
        self.w, self.h = w, h
        hdc_screen = _user32.GetDC(None)
        self.hdc = _gdi32.CreateCompatibleDC(hdc_screen)
        _user32.ReleaseDC(None, hdc_screen)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        self.bits = ctypes.c_void_p()
        self.hbm = _gdi32.CreateDIBSection(self.hdc, ctypes.byref(bi), DIB_RGB_COLORS,
                                           ctypes.byref(self.bits), None, 0)
        if not self.hbm:
            raise RuntimeError("CreateDIBSection 失败")
        self.old = _gdi32.SelectObject(self.hdc, self.hbm)
        self.g = P()
        _CreateFromHDC(self.hdc, ctypes.byref(self.g))
        _SetSmoothing(self.g, SMOOTHING_ANTIALIAS)
        _SetTextHint(self.g, TEXT_HINT_ANTIALIAS)
        _SetPixelOffset(self.g, PIXEL_OFFSET_HIGHQUALITY)
        _SetInterp(self.g, INTERP_HIGHQUALITY)

    def _font(self, size, bold):
        key = (size, bold)
        f = self._fonts.get(key)
        if f is None:
            f = P()
            _CreateFont(_family(), float(size), FONT_BOLD if bold else FONT_REGULAR,
                        UNIT_PIXEL, ctypes.byref(f))
            self._fonts[key] = f
        return f

    def _format(self, center):
        key = (bool(center),)
        f = self._fmt_cache.get(key)
        if f is None:
            f = P()
            _CreateStringFormat(0, 0, ctypes.byref(f))
            _SetFormatFlags(f, _STR_NOWRAP)
            _SetAlign(f, 1 if center else 0)
            _SetLineAlign(f, 1)
            self._fmt_cache[key] = f
        return f

    def _fy(self, y, hh):
        # GDI+ 在内存 DC 上的逻辑坐标本身就是自上而下（原点位图左上），
        # 无需再翻转——翻转会导致整个画面上下颠倒（立绘跑到气泡上面）。
        return y

    def clear(self):
        _GraphicsClear(self.g, 0x00000000)

    def ellipse(self, argb, x, y, w, h, line_argb=None, line_w=1.0):
        b = P()
        _SolidFill(argb, ctypes.byref(b))
        _FillEllipse(self.g, b, F(float(x)), F(float(self._fy(y, h))), F(float(w)), F(float(h)))
        _DeleteBrush(b)
        if line_argb is not None:
            p = P()
            _CreatePen(line_argb, float(line_w), UNIT_PIXEL, ctypes.byref(p))
            _DrawEllipse(self.g, p, F(float(x)), F(float(self._fy(y, h))),
                         F(float(w)), F(float(h)))
            _DeletePen(p)

    def image(self, img, x, y, w, h):
        """绘制 PNG 立绘（含 alpha）。坐标自上而下。"""
        _DrawImageRectI(self.g, img, int(x), int(self._fy(y, h)), int(w), int(h))

    def image_rot(self, img, iw, ih, x, y, w, h, deg):
        """把位图画进 (x,y,w,h) 并绕**中心**旋转 deg 度。

        走 GDI+ 的平行四边形映射：给 3 个目标点即可，缩放与旋转一次完成，
        比"自己重采样 + 逐帧旋转"省得多。
        """
        g = self.g
        cx, cy = x + w / 2.0, y + h / 2.0
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)

        def _r(px, py):
            dx, dy = px - cx, py - cy
            return (cx + dx * ca - dy * sa, cy + dx * sa + dy * ca)

        x0, y0 = _r(x, y)
        x1, y1 = _r(x + w, y)
        x2, y2 = _r(x, y + h)
        pts = (F * 6)(x0, y0, x1, y1, x2, y2)
        _DrawImagePoints(g, img, pts, 3, 0.0, 0.0, float(iw), float(ih),
                         UNIT_PIXEL, None, None, None)

    def blit(self, src, x, y, w, h, sw=None, sh=None):
        """从另一 Surface alpha 合成（预缩放立绘缓存用，免重采样）。

        sw/sh 省略时为 1:1；传入时允许缩放（hover 放大 / 按压压缩用，交给 GDI 缩放比
        每帧重采样大图便宜得多）。源位图是 GDI+ 输出的预乘 alpha，AlphaBlend 直接正确混合。
        """
        sw = src.w if sw is None else sw
        sh = src.h if sh is None else sh
        _msimg32.AlphaBlend(self.hdc, int(x), int(self._fy(y, h)), int(w), int(h),
                            src.hdc, 0, 0, int(sw), int(sh), _ALPHABLEND_BF)

    def blit_a(self, src, x, y, w, h, alpha):
        """带**全局 alpha** 的 alpha 合成（当前未使用，保留给需要真正淡入淡出的场景）。

        BLENDFUNCTION 是 4 字节小端 DWORD：`AlphaFormat<<24 | SourceConstantAlpha<<16 |
        BlendFlags<<8 | BlendOp`。项目原有的 `_ALPHABLEND_BF = 0x01FF0000` 就是
        `AC_SRC_ALPHA` + `SCA=255`，所以**只改第 3 个字节即可拿到全局透明度**——
        （早先"GDI 的 AlphaBlend 没有全局 alpha"的说法不准确：是没暴露，不是没有）。
        最终 alpha = 源像素 alpha × SCA/255。

        眨眼**没有**用它：两张差异很大的图做 alpha 交叉会重影（睁眼暗瞳透过半透明闭眼图
        显出来）。半闭改用离线「按眼睑位置做垂直遮罩的多档贴片」，见 build_blink_patches.py。
        """
        bf = (0x01 << 24) | ((int(max(0, min(255, alpha))) & 0xFF) << 16)
        _msimg32.AlphaBlend(self.hdc, int(x), int(self._fy(y, h)), int(w), int(h),
                            src.hdc, 0, 0, int(src.w), int(src.h), bf)

    def blit_part(self, src, dx, dy, dw, dh, sx, sy, sw, sh):
        """从 src 的局部矩形合成到本画布指定位置（尾鳍摆动 / 头部倾斜的分层位移用）。

        实测：虽然 DIB 在内存里是自下而上存放，但 GDI+ 写入时用的是自上而下逻辑坐标，
        AlphaBlend 的源 y 与之同向（直接传 sy 即可），多做换算反而会取到镜像的那一块。
        """
        if dw <= 0 or dh <= 0 or sw <= 0 or sh <= 0:
            return
        _msimg32.AlphaBlend(self.hdc, int(dx), int(dy), int(dw), int(dh),
                            src.hdc, int(sx), int(sy), int(sw), int(sh),
                            _ALPHABLEND_BF)

    def poly(self, argb, pts):
        """多边形填充（心形/粒子用）。pts 为 [(x,y), ...] 自上而下坐标。"""
        if len(pts) < 3:
            return
        path = P()
        _CreatePath(0, ctypes.byref(path))
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            _AddPathLine(path, F(float(x1)), F(float(self._fy(y1, 0))),
                         F(float(x2)), F(float(self._fy(y2, 0))))
        _CloseFigure(path)
        b = P()
        _SolidFill(argb, ctypes.byref(b))
        _FillPath(self.g, b, path)
        _DeleteBrush(b)
        _DeletePath(path)

    def round_rect(self, argb, x, y, w, h, r, line_argb=None, line_w=1.0):
        """圆角矩形（右键菜单卡片/悬停高亮用；GDI+ path 四边四弧，alpha 全通道）。"""
        if w <= 0 or h <= 0:
            return
        r = min(r, w / 2, h / 2)
        path = P()
        _CreatePath(0, ctypes.byref(path))
        fy = self._fy
        _AddPathArc(path, F(x + w - r), F(fy(y + r, 0)), F(2 * r), F(2 * r),
                    F(-90.0), F(90.0))
        _AddPathArc(path, F(x + w - r), F(fy(y + h - r, 0)), F(2 * r), F(2 * r),
                    F(0.0), F(90.0))
        _AddPathArc(path, F(x + r), F(fy(y + h - r, 0)), F(2 * r), F(2 * r),
                    F(90.0), F(90.0))
        _AddPathArc(path, F(x + r), F(fy(y + r, 0)), F(2 * r), F(2 * r),
                    F(180.0), F(90.0))
        _CloseFigure(path)
        b = P()
        _SolidFill(argb, ctypes.byref(b))
        _FillPath(self.g, b, path)
        _DeleteBrush(b)
        if line_argb is not None:
            p = P()
            _CreatePen(line_argb, float(line_w), UNIT_PIXEL, ctypes.byref(p))
            _DrawPath(self.g, p, path)
            _DeletePen(p)
        _DeletePath(path)

    def heart(self, cx, cy, r, argb):
        """小心形：双圆 + 下三角。"""
        hr = r * 0.55
        self.ellipse(argb, cx - r, cy - hr, hr * 2, hr * 2)
        self.ellipse(argb, cx, cy - hr, hr * 2, hr * 2)
        self.poly(argb, [(cx - r * 1.05, cy + r * 0.15),
                         (cx + r * 1.05, cy + r * 0.15),
                         (cx, cy + r * 1.5)])

    def measure(self, s, size, bold=False):
        font = self._font(size, bold)
        fmt = self._format(False)
        layout = RectF(0, 0, 100000.0, float(size * 3))
        bb = RectF()
        cp, ln = INT_C(0), INT_C(0)
        _MeasureString(self.g, s, -1, font, ctypes.byref(layout), fmt,
                       ctypes.byref(bb), ctypes.byref(cp), ctypes.byref(ln))
        return bb.width, bb.height

    def fit(self, s, size, bold, maxw):
        if maxw and self.measure(s, size, bold)[0] > maxw:
            lo, hi = 0, len(s)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self.measure(s[:mid] + "…", size, bold)[0] <= maxw:
                    lo = mid
                else:
                    hi = mid - 1
            s = s[:lo] + "…"
        return s

    def text(self, s, x, y, size, argb, bold=False, maxw=None, center=False):
        if not s:
            return
        s = self.fit(s, size, bold, maxw)
        font = self._font(size, bold)
        fmt = self._format(center)
        bh = size * 1.45
        rect = RectF(0.0 if center else float(x), F(float(self._fy(y, bh))),
                     float(self.w) if center else float(maxw or (self.w - x)), F(float(bh)))
        b = P()
        _SolidFill(argb, ctypes.byref(b))
        _DrawString(self.g, s, -1, font, ctypes.byref(rect), fmt, b)
        _DeleteBrush(b)

    def ctext(self, s, cx, y, size, argb, bold=False, maxw=None):
        if not s:
            return
        s = self.fit(s, size, bold, maxw)
        tw = self.measure(s, size, bold)[0]
        self.text(s, int(cx - tw / 2), y, size, argb, bold=bold, maxw=None)

    def present(self, hwnd):
        size = SIZE(self.w, self.h)
        src = POINT(0, 0)
        r = RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        dst = POINT(r.left, r.top)
        blend = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
        hdc_screen = _user32.GetDC(None)
        ok = _user32.UpdateLayeredWindow(hwnd, hdc_screen, ctypes.byref(dst), ctypes.byref(size),
                                         self.hdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        _user32.ReleaseDC(None, hdc_screen)
        return bool(ok)

    def close(self):
        try:
            _DeleteGraphics(self.g)
        except Exception:
            pass
        try:
            _gdi32.SelectObject(self.hdc, self.old)
            _gdi32.DeleteObject(self.hbm)
            _gdi32.DeleteDC(self.hdc)
        except Exception:
            pass


# ================= 自绘右键菜单（MenuSession） =================
# 为什么不用 TrackPopupMenu owner-draw（2026-09-29 实测结论）：
# Win11 的系统菜单窗口**背景合成不可控**——MIM_BACKGROUND 铺底不可靠，未绘制区域
# 在 DWM 合成时透出下层窗口（视觉=文字与桌面内容"重叠"），hover 切换的擦除时机
# 也管不到。这里改用与桌宠本体同款的自绘分层窗口（ULW，逐像素不透明 alpha），
# 每帧全量重绘，背景天然不透明——重叠/残影/透明三类问题一起消失。
# 语义对齐 TrackPopupMenu(TPM_RETURNCMD)：run() 模态跑消息循环，返回选中 cmd / 0。
# 条目 = {"label","cmd","checked","disabled","danger","sep","sub":[条目...]}（sub 仅一层）。

WM_KEYDOWN = 0x0100          # （模块原本未定义，菜单键盘 Esc 用）
WM_ACTIVATE = 0x0006         # 失活 → 关菜单（点外部由 capture 兜底，这里是 Alt-Tab 路）
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205        # （与模块首部同名常量一致；这里为菜单段落就近自证）
_MENU_VK_ESCAPE = 0x1B
_MENU_SHADOW = 14              # 窗口四边的阴影外边距（px @96dpi）
# 子菜单**悬停停留**才展开（原生菜单行为）。原来一 hover 就展开 —— 鼠标顺着菜单往下扫时
# 会一路"闪"（每个带子菜单的条目都开一次窗口、再销毁，实测 12ms/次），既卡又吵。
SUB_OPEN_DELAY_S = 0.14
ID_TIMER_SUB = 6               # 子菜单延迟展开的轮询定时器（挂在菜单窗口上）


def menu_layout(items, m):
    """条目 → 行布局 [(y0, y1, item), ...] 与内容总高（纯函数，[H] 单测）。"""
    rows, y = [], m["pad_v"]
    for it in items:
        h = m["sep_h"] if it.get("sep") else m["h"]
        rows.append((y, y + h, it))
        y += h
    return rows, y + m["pad_v"]


def menu_hit(rows, y):
    """本地 y → 条目下标；间隙/越界 → None（纯函数）。"""
    for i, (y0, y1, _it) in enumerate(rows):
        if y0 <= y < y1:
            return i
    return None


class MenuSession:
    _registered = False

    def __init__(self, items, dpi=96):
        self.items = items
        self.m = menu_metrics(dpi)
        self.scale = dpi / 96.0
        self.margins = round(_MENU_SHADOW * self.scale)
        self._cmd = 0
        self._done = False
        self._hover = None          # 主菜单悬停下标
        self._sub = None            # {"item","hwnd","surf","rect","rows","content_h","hover"}
        self._sub_pending = None    # (下标, 起始时刻)：停留够久才展开的子菜单
        self._sub_timer_on = False
        self.hwnd = None
        self.surf = None
        self._rows, self._content_h = menu_layout(items, self.m)

    # ---------- 几何 ----------
    def _win_size(self, content_h):
        return (self.m["w"] + 2 * self.margins,
                content_h + 2 * self.margins)

    def _place(self, pt):
        """弹出点 → 主窗口 (l, t)，防溢出屏幕（工作区）。"""
        W, H = self._win_size(self._content_h)
        wa = RECT()
        _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)   # SPI_GETWORKAREA
        l = min(max(pt[0] - 4, wa.left), wa.right - W)
        t = min(max(pt[1] - 4, wa.top), wa.bottom - H)
        return l, t, W, H

    def _sub_rect(self, item_idx):
        """子菜单窗口位置：父条目右侧衔接，右溢出改左侧。"""
        it = self.items[item_idx]
        rows, ch = menu_layout(it.get("sub") or [], self.m)
        W, H = self._win_size(ch)
        y0, _y1, _ = self._rows[item_idx]
        pr = RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(pr))
        wa = RECT()
        _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
        l = pr.right - self.margins + 2
        if l + W > wa.right:
            l = pr.left + self.margins - W - 2
        t = min(max(pr.top + self.margins + y0 - 3, wa.top), max(wa.bottom - H, wa.top))
        return l, t, W, H, rows, ch

    # ---------- 绘制（全 GDI+，alpha 全通道） ----------
    def _argb(self, c):
        return 0xFF000000 | (c & 0xFFFFFF)

    def _render(self):
        s, m = self.surf, self.m
        s.clear()
        W, H = s.w, s.h
        # 阴影：由外向内三层渐强黑（柔和近似）
        for i, a in enumerate((0x24, 0x3C, 0x55)):
            e = self.margins - 4 * i
            s.round_rect((a << 24), e, e, W - 2 * e, H - 2 * e,
                         self.m["r"] + 6 - i * 2)
        # 白卡 + 描边
        s.round_rect(self._argb(MENU_BG), self.margins, self.margins,
                     m["w"], self._content_h, m["r"], line_argb=self._argb(MENU_LINE))
        # 条目
        for idx, (y0, y1, it) in enumerate(self._rows):
            selected = (self._hover == idx)
            sub_sel = (self._sub and self._sub["item"] is it)
            if sub_sel:
                selected = True
            self._render_item(s, self.margins, self.margins + y0,
                              y1 - y0, it, selected)
        s.present(self.hwnd)
        self._pin_top()                 # 每次绘制都压回桌宠之上（它每秒在抢置顶）
        if self._sub:
            self._render_sub()

    def _render_item(self, s, ox, oy, h, it, selected):
        m = self.m
        st = menu_item_style(it, selected, bool(it.get("checked")),
                             bool(it.get("disabled")))
        if st["kind"] == "sep":
            yy = oy + h // 2
            s.poly(self._argb(MENU_LINE),
                   [(ox + m["pad"], yy), (ox + m["w"] - m["pad"], yy),
                    (ox + m["w"] - m["pad"], yy + 1), (ox + m["pad"], yy + 1)])
            return
        if st["bg"]:
            s.round_rect(self._argb(st["bg"]), ox + m["inset"], oy + 1,
                         m["w"] - 2 * m["inset"], h - 2, m["r"])
        cy = oy + h / 2.0
        if st["dot"]:
            r = m["dot_r"]
            color = MENU_VIOLET if st["dot_filled"] else MENU_DOT_OFF
            s.ellipse(self._argb(color), ox + m["dot_x"] - r, cy - r, 2 * r, 2 * r)
        label = it.get("label") or ""
        if label:
            maxw = m["w"] - m["tx"] - (m["arrow_w"] if st["arrow"] else m["pad"])
            s.text(label, ox + m["tx"], int(cy - m["font_h"] * 0.75), m["font_px"],
                   self._argb(st["text"]), bold=st["bold"], maxw=maxw)
        if st["arrow"]:
            x = ox + m["w"] - m["pad"] - m["dot_r"]
            s.poly(self._argb(MENU_INK_DIM),
                   [(x - 2, cy - 5), (x - 2, cy + 5), (x + 4, cy)])

    def _render_sub(self):
        sub = self._sub
        # ⚠️ 必须用**子菜单自己的** surface。原先写成 `s = self.surf`（父菜单的）：
        #    父菜单比子菜单高 → 子卡片画完后，下面那一段只剩三层阴影叠出来的灰块；
        #    而 ULW 又是按这张 bitmap 的尺寸显示（`Surface.present` 用 self.w/self.h）
        #    → 子菜单窗口"凭空多出一大截空白"（2026-09-29 用户报障
        #    「设置面板设置时会有空出来」）。
        s, m = sub["surf"], self.m
        W, H = s.w, s.h
        s.clear()                       # 每次全量重绘（hover 会反复走这里，别叠阴影）
        for i, a in enumerate((0x24, 0x3C, 0x55)):
            e = self.margins - 4 * i
            s.round_rect((a << 24), e, e, W - 2 * e, H - 2 * e, m["r"] + 6 - i * 2)
        s.round_rect(self._argb(MENU_BG), self.margins, self.margins,
                     m["w"], sub["content_h"], m["r"],
                     line_argb=self._argb(MENU_LINE))
        for idx, (y0, y1, it) in enumerate(sub["rows"]):
            self._render_item(s, self.margins, self.margins + y0, y1 - y0, it,
                              sub["hover"] == idx)
        s.present(sub["hwnd"])
        self._pin_top(sub["hwnd"])      # 子菜单同样要压在桌宠（与父菜单）之上

    # ---------- 窗口 ----------
    @classmethod
    def _ensure_class(cls):
        global _MENU_WNDPROC_STUB
        if cls._registered:
            return
        wc = WNDCLASSW()
        _MENU_WNDPROC_STUB = WNDPROC(_menu_wndproc)     # 模块级持有防 GC
        wc.lpfnWndProc = ctypes.cast(_MENU_WNDPROC_STUB, ctypes.c_void_p)
        wc.lpszClassName = "WBMenuClass"
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.hCursor = _user32.LoadCursorW(None, wt.LPCWSTR(32512))   # IDC_ARROW
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            raise RuntimeError("RegisterClassW(WBMenuClass) 失败")
        cls._registered = True

    def run(self, owner_hwnd, pt):
        """模态弹出。pt=(x,y) 屏幕坐标（右键位置）。返回选中 cmd / 0（取消）。"""
        if not self._open(owner_hwnd, pt):
            self._teardown()
            return 0
        try:
            msg = MSG()
            while not self._done and _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._release()
            self._teardown()
        return self._cmd

    def _open(self, owner_hwnd, pt, grab=True):
        """建窗口 + 首绘 + 置顶（+ 抢前台/鼠标 capture）。

        拆出 `run()` 的原因：模态循环一进去就出不来，**测试/repro 没法观察窗口状态**
        （扩展样式位 / z 序 / 命中归属）。拆开后可以先 `_open(grab=False)`、探针、再
        `_teardown()`；`grab=False` 供测试用 —— 不抢前台、不抢 capture（否则跑一次
        测试就会把用户当前的焦点/鼠标捕获抢走）。
        返回是否成功建窗。
        """
        try:
            self._ensure_class()
            dpi = (_user32.GetDpiForWindow(owner_hwnd) or 96) if owner_hwnd else 96
            self.m = menu_metrics(dpi)
            self.scale = dpi / 96.0
            self.margins = round(_MENU_SHADOW * self.scale)
            self._rows, self._content_h = menu_layout(self.items, self.m)
            l, t, W, H = self._place(pt)
            # ★ 只用具名常量。历史教训（2026-09-29 用户报障）：
            #   这里原写 `0x00080000 | 0x00000080 | 0x00000020`，作者以为 0x20 是
            #   WS_EX_TOPMOST —— **0x20 其实是 WS_EX_TRANSPARENT，TOPMOST 是 0x8**。
            #   后果正好是两个症状：
            #     ① 没有 topmost 位 → 落在普通层 → 被每秒 `SetWindowPos(HWND_TOPMOST)`
            #        的桌宠压住（截图：人物盖住菜单卡片）；
            #     ② TRANSPARENT = 鼠标穿透 → hover/点击全落给下层窗口
            #        （表现为「不用力按住右键就选不动」「点菜单变成点桌宠」）。
            #   LAYERED 是 ULW 的前置条件（漏了不报错、只是不上屏）。
            ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
            self.hwnd = _user32.CreateWindowExW(
                ex, "WBMenuClass", None, WS_POPUP,
                l, t, W, H, None, None, _kernel32.GetModuleHandleW(None), None)
            if not self.hwnd:
                return False
            _MENU_WINDOWS[self.hwnd] = self
            self.surf = Surface(W, H)
            _user32.ShowWindow(self.hwnd, SW_SHOWNA)
            self._pin_top()                 # 建窗即置顶（CreateWindowEx 的 TOPMOST 不保证生效）
            self._render()
            if grab:
                _user32.SetForegroundWindow(self.hwnd)  # 收键盘 Esc；失活即关闭
                _user32.SetCapture(self.hwnd)           # 鼠标集中制：全屏坐标由本窗口裁决
            return True
        except BaseException:
            self._teardown()
            raise

    def _teardown(self):
        """拆窗口（run / 测试 / repro 共用，可重复调用）。"""
        self._sub_pending = None
        self._disarm_sub_timer()          # 定时器挂在 hwnd 上，窗口销毁时顺手清掉
        if self.hwnd:
            _MENU_WINDOWS.pop(self.hwnd, None)
        if self.surf:
            try:
                self.surf.close()
            except Exception:
                pass
            self.surf = None
        if self.hwnd:
            _user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self._close_sub()

    def _pin_top(self, hwnd=None):
        """把窗口钉到 topmost 带的最上层（建窗后 + 每次绘制后都调）。

        为什么必须每次绘制都钉：桌宠本体也常驻 topmost 且 `tick()` 每秒
        `SetWindowPos(HWND_TOPMOST)` 重新置顶自己 —— 菜单不主动钉就会被它压住。
        （实测：桌宠 #1 / 菜单 #14，菜单卡片被人物理盖掉一半。）
        """
        h = hwnd or self.hwnd
        if h:
            _user32.SetWindowPos(h, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def finish(self, cmd):
        self._cmd = cmd or 0
        self._done = True
        _user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)   # 踢醒模态循环

    def _release(self):
        try:
            _user32.ReleaseCapture()
        except Exception:
            pass

    def _close_sub(self):
        if self._sub and self._sub.get("hwnd"):
            _MENU_WINDOWS.pop(self._sub["hwnd"], None)
            try:
                self._sub["surf"].close()
            except Exception:
                pass
            _user32.DestroyWindow(self._sub["hwnd"])
        self._sub = None

    # ---------- 交互（capture 集中制：全部消息换算成屏幕坐标后裁决） ----------
    @staticmethod
    def _scr(lparam, hwnd):
        """鼠标消息的 client 坐标（SetCapture 时相对 capture 窗口）→ 屏幕坐标。"""
        gx = ctypes.c_short(lparam & 0xFFFF).value
        gy = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        r = RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        return gx + r.left, gy + r.top

    def on_move(self, gx, gy):
        # 子菜单命中
        if self._sub:
            l, t, _W, _H = self._sub["rect"]
            if l <= gx < l + self._sub["win_w"] and t <= gy < t + self._sub["win_h"]:
                li = menu_hit(self._sub["rows"], gy - t - self.margins)
                if li != self._sub["hover"]:
                    self._sub["hover"] = li
                    self._render_sub()
                return
        # 主菜单命中（子菜单区域之外 → 若已开子菜单而指针不在父条目上 → 关）
        pr = RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(pr))
        if pr.left <= gx < pr.right and pr.top <= gy < pr.bottom:
            li = menu_hit(self._rows, gy - pr.top - self.margins)
            if li != self._hover:
                self._hover = li
                it = self.items[li] if li is not None else None
                if it and it.get("sub") and not it.get("disabled"):
                    # ⚠️ 不是立刻展开：**停留** SUB_OPEN_DELAY_S 才开（见常量注释）。
                    #    鼠标顺着菜单往下扫时，一路上的子菜单父条目就不会逐个闪一下。
                    if not (self._sub and self._sub["item"] is it):
                        self._close_sub()
                        self._sub_pending = (li, time.time())
                        self._arm_sub_timer()
                else:
                    self._sub_pending = None
                    if self._sub:
                        self._close_sub()
                self._render()
        elif self._sub or self._sub_pending:
            self._sub_pending = None
            self._close_sub()
            self._render()

    # ---------- 子菜单延迟展开 ----------
    def _arm_sub_timer(self):
        """挂上轮询定时器（60ms 一跳，只在有待展开的子菜单时开着）。"""
        if not self._sub_timer_on and self.hwnd:
            _user32.SetTimer(self.hwnd, ID_TIMER_SUB, 60, None)
            self._sub_timer_on = True

    def _disarm_sub_timer(self):
        if self._sub_timer_on and self.hwnd:
            try:
                _user32.KillTimer(self.hwnd, ID_TIMER_SUB)
            except Exception:
                pass
        self._sub_timer_on = False

    def on_timer(self):
        """WM_TIMER：停留够久就把待展开的子菜单打开。"""
        pend = self._sub_pending
        if not pend:
            self._disarm_sub_timer()
            return
        idx, t0 = pend
        if time.time() - t0 < SUB_OPEN_DELAY_S:
            return
        self._sub_pending = None
        self._disarm_sub_timer()
        it = self.items[idx] if 0 <= idx < len(self.items) else None
        if it and it.get("sub") and self._hover == idx:      # 光标还在它身上才开
            self._open_sub(idx)

    def _open_sub(self, idx):
        self._close_sub()
        it = self.items[idx]
        l, t, W, H, rows, ch = self._sub_rect(idx)
        # LAYERED(ULW 必需) | TOPMOST(压住桌宠) | NOACTIVATE(不抢前台，
        # 鼠标仍由父窗口 capture 裁决) | TOOLWINDOW —— 全部走具名常量（见 _open 里的教训）
        ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        hwnd = _user32.CreateWindowExW(ex, "WBMenuClass", None, WS_POPUP,
                                       l, t, W, H, None, None,
                                       _kernel32.GetModuleHandleW(None), None)
        if not hwnd:
            return
        _MENU_WINDOWS[hwnd] = self           # 子窗口消息也由本 session 裁决
        self._sub = {"item": it, "hwnd": hwnd, "surf": Surface(W, H),
                     "rect": (l, t, W, H), "rows": rows,
                     "content_h": ch, "win_w": W, "win_h": H, "hover": None}
        _user32.ShowWindow(hwnd, SW_SHOWNA)
        self._pin_top(hwnd)
        self._render()

    def _in_any(self, gx, gy):
        """点是否落在任一菜单窗口内（主菜单 or 已展开的子菜单）。"""
        pr = RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(pr))
        if pr.left <= gx < pr.right and pr.top <= gy < pr.bottom:
            return True
        if self._sub:
            l, t, _W, _H = self._sub["rect"]
            if l <= gx < l + self._sub["win_w"] and t <= gy < t + self._sub["win_h"]:
                return True
        return False

    def on_rbutton(self, gx, gy, up):
        """右键落在菜单上：**不选中**（选中只认左键），菜单外松开 → 收起。

        为什么要显式处理：菜单窗口修掉 WS_EX_TRANSPARENT 后不再穿透，右键会真的
        送到菜单；不处理的话用户「在别处右键收起菜单」的习惯动作会被 capture 吞掉，
        菜单反而赖着不走。
        """
        if up and not self._in_any(gx, gy):
            self.finish(0)

    def on_button(self, gx, gy, up):
        if self._sub:
            l, t, _W, _H = self._sub["rect"]
            if l <= gx < l + self._sub["win_w"] and t <= gy < t + self._sub["win_h"]:
                li = menu_hit(self._sub["rows"], gy - t - self.margins)
                if li is not None:
                    it = self._sub["item"]["sub"][li]
                    if not it.get("disabled") and not it.get("sep"):
                        self.finish(it.get("cmd") or 0)
                return
        pr = RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(pr))
        if pr.left <= gx < pr.right and pr.top <= gy < pr.bottom:
            li = menu_hit(self._rows, gy - pr.top - self.margins)
            if li is not None:
                it = self.items[li]
                if not it.get("disabled") and not it.get("sep") and not it.get("sub"):
                    self.finish(it.get("cmd") or 0)
        elif up:
            self.finish(0)                    # 菜单外点击 → 关闭（吞掉该次点击）

    def on_key(self, wp):
        if wp == _MENU_VK_ESCAPE:
            if self._sub:
                self._close_sub()
                self._render()
            else:
                self.finish(0)


_MENU_WINDOWS = {}
_MENU_WNDPROC_STUB = None       # 模块级持有：WNDPROC 回调被 GC 会导致菜单窗口崩溃


def _menu_wndproc(hwnd, msg, wparam, lparam):
    sess = _MENU_WINDOWS.get(hwnd)
    if sess is None:
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    try:
        if msg == WM_MOUSEMOVE:
            gx, gy = sess._scr(lparam, hwnd)
            sess.on_move(gx, gy)
        elif msg == WM_TIMER:
            sess.on_timer()               # 子菜单"停留才展开"的轮询
        elif msg in (WM_LBUTTONDOWN, WM_LBUTTONUP):
            gx, gy = sess._scr(lparam, hwnd)
            sess.on_button(gx, gy, up=(msg == WM_LBUTTONUP))
        elif msg in (WM_RBUTTONDOWN, WM_RBUTTONUP):
            gx, gy = sess._scr(lparam, hwnd)
            sess.on_rbutton(gx, gy, up=(msg == WM_RBUTTONUP))
        elif msg == WM_KEYDOWN and wparam == _MENU_VK_ESCAPE:
            sess.on_key(wparam)
        elif msg == WM_ACTIVATE and (wparam & 0xFFFF) == 0:
            sess.finish(0)                    # 失活（Alt-Tab/点别的窗口）→ 关菜单
        elif msg == WM_CLOSE:
            # ⚠️ 外部关闭请求（WM_CLOSE / 任务栏关窗）：必须走正常收尾。
            #    原来这里一律 return 0 —— 请求被吞、窗口活着、模态循环也退不出，
            #    结果是**幽灵菜单**：菜单永远关不掉，桌宠还以为菜单开着（tick 一直被闸）。
            sess.finish(0)
        else:
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        return 0
    except BaseException:
        try:
            log_exception(f"[menu-wndproc msg=0x{msg:X}]")
        except Exception:
            pass
        return 0


# ---------- 窗口过程（模块级唯一桩防 GC） ----------
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_WINDOWS = {}


def _wndproc(hwnd, msg, wparam, lparam):
    try:
        app = _WINDOWS.get(hwnd)
        if app is not None:
            return app.on_message(hwnd, msg, wparam, lparam)
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    except BaseException:
        try:
            log_exception(f"[wndproc msg=0x{msg:X}]")
        except Exception:
            pass
        try:
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0


_WNDPROC_STUB = WNDPROC(_wndproc)


def _argb(hex_color, alpha=0xFF):
    """'#1A2042' / '1A2042' → 0xFF1A2042（GDI+ 用 ARGB）。"""
    s = str(hex_color).lstrip("#").strip()
    if len(s) == 6:
        s = "FF" + s
    try:
        return int(s, 16) & 0xFFFFFFFF
    except ValueError:
        return (int(alpha) << 24) | 0x1A2042


def work_area():
    r = RECT()
    if _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return (0, 0, _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1))


# ---------- 音效合成（winsound SND_MEMORY 异步播放，运行时现算 WAV 字节）----------
def _wav_bytes(pcm, rate=22050):
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _tone(freq_fn, dur, vol=0.32, rate=22050):
    """正弦音：freq_fn(t)->Hz；带 10ms 起音 + 平方衰减包络防爆音。"""
    n = int(dur * rate)
    out = bytearray()
    phase = 0.0
    for i in range(n):
        t = i / rate
        phase += 2 * math.pi * freq_fn(t) / rate
        env = min(1.0, i / (rate * 0.01)) * max(0.0, 1.0 - (i / n) ** 2)
        out += struct.pack("<h", int(32767 * vol * env * math.sin(phase)))
    return bytes(out)


def _noise(dur, vol=0.22, rate=22050):
    """水花噪声：均匀随机 + 移动平均柔化 + 指数衰减。"""
    n = int(dur * rate)
    raw = [random.uniform(-1, 1) for _ in range(n)]
    out = bytearray()
    sm = 0.0
    for i in range(n):
        sm = 0.65 * sm + 0.35 * raw[i]          # 简易低通
        env = (1.0 - i / n) ** 2
        out += struct.pack("<h", int(32767 * vol * env * sm))
    return bytes(out)


def _lerp_argb(a, b, t):
    """两个 ARGB 颜色按 t∈[0,1] 线性插值（描边由制服蓝渐变到 OK 橙用）。"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    out = 0
    for shift in (24, 16, 8, 0):
        ca = (a >> shift) & 0xFF
        cb = (b >> shift) & 0xFF
        out |= (int(round(ca + (cb - ca) * t)) & 0xFF) << shift
    return out


def _make_sounds():
    """四档反应音效。winsound 不可用或合成失败时返回 {}（静音降级）。"""
    if winsound is None:
        return {}
    try:
        return {
            "pop": _wav_bytes(_tone(lambda t: 500 + 5500 * t, 0.08, 0.30)),
            "chirp": _wav_bytes(_tone(lambda t: 880.0, 0.06, 0.30)
                                + _tone(lambda t: 1175.0, 0.09, 0.30)),
            "hmph": _wav_bytes(_tone(lambda t: 420 - 900 * t, 0.18, 0.28)),
            "splash": _wav_bytes(_noise(0.28, 0.24)
                                 + _tone(lambda t: 300 - 1200 * t, 0.12, 0.20)),
        }
    except Exception:
        return {}


def format_timeline_summary(summary):
    """跟随 P2 连续锚点：[(agent, 轮次), ...] → 「Codex 3 轮 · WorkBuddy 12 轮」。"""
    return " · ".join(f"{lab} {n} 轮" for lab, n in (summary or []))


# ---------- 古见同学桌宠（主体）----------
class WhalePet:
    def __init__(self, run_seconds=None):
        if _gdiplus is None:
            raise RuntimeError("未找到 gdiplus.dll")
        _set_dpi_aware()
        _high_res_timer_on()      # 1ms 定时器精度：41ms 动画 tick 才不会被拉长到 46.8ms
        tok = P()
        si = GdiplusStartupInput()
        si.GdiplusVersion = 1
        si.DebugCallback = None
        si.SuppressBackgroundThread = 0
        si.SuppressExternalCodecs = 0
        if _GdiplusStartup(ctypes.byref(tok), ctypes.byref(si), None) != 0:
            raise RuntimeError("GdiplusStartup 失败")
        self._gp_token = tok

        p = load_paths()
        self.db_path = p["db_path"]
        self.wb_db = p["workbuddy_db"]
        self.dashboard = p["dashboard_url"]

        self.active = []
        self.latest_turn = None            # 最近一次有数据 turn（不依赖 working，用于气泡"本轮"实时统计）
        self.db_ok = False
        self.api_ok = None
        self.run_seconds = run_seconds
        self.t0 = time.time()
        # 部位点击触发的临时形态（点击头部/脸/身体/裙摆后保持 N 秒）
        self._morph_state = ""
        self._morph_until = 0.0
        self._dragging = False
        self._moved = False
        self._down = (0, 0)
        self._win0 = (0, 0)
        self._swallow_up = False             # 双击后吞掉配对的那次 WM_LBUTTONUP
        # ---- 生命力状态 ----
        self._home = None                  # "家"位置（拖动吸附/初始摆放后更新），漂移以此为锚
        self._drift = None                 # 窗口漂移动画 {x0,y0,x1,y1,t0,dur}
        self._drift_back_at = 0.0          #  outward 漂移完成后的返程时刻
        self._facing = ""                  # 当前朝向（"" 或 "_f"）
        self._follow_dir = None            # 鼠标跟随候选朝向（防抖）
        self._follow_since = 0.0
        self._last_interact = time.time()  # 最近用户交互时间（点击/拖动）
        self._next_behavior = time.time() + 8.0   # 下次自主行为时刻
        self._greet_at = time.time() + GREET_DELAY  # 启动打招呼时刻
        self._bob_phase = random.uniform(0, math.pi * 2)  # 呼吸相位随机化
        self._pressed = False
        self._talk_until = 0.0
        self._quote = ""
        self._quote_dur = "-"
        self._drawn_sig = None
        # ---- 气泡形态状态机：BUBBLE_DEFAULT（数据态）↔ BUBBLE_OK（任务完成态）----
        # 迁移规则：
        #   default → ok      ：运行中会话数 由「>0」跌到「0」= 一轮对话任务结束
        #   ok      → default ：气泡被**连续**点击 OK_CLICKS_NEEDED 次（窗口 OK_CLICK_WINDOW_S）
        #   ok      → default ：新对话开始（会话数重新 >0）——否则任务在跑却挂着"完成"会误导
        self._bub_mode = BUBBLE_DEFAULT
        self._prev_active_n = None       # 上一 tick 的运行中会话数（None = 还没取到基线）
        # 活跃数防抖（见 ACTIVE_FALL_DEBOUNCE_S）：已生效值 / 下降起始时刻 / 窗口
        self._n_stable = 0
        self._n_hold_since = 0.0
        self._fall_debounce_s = ACTIVE_FALL_DEBOUNCE_S
        self._read_ok_at = 0.0           # 上次**成功**读到数仓的时刻（读数卡死兜底用）
        self._ok_clicks = 0              # ok 态下的连续点击计数
        self._ok_last_click = 0.0        # 上一次气泡点击时刻（连击窗口判定）
        self._ok_anim_t0 = 0.0           # 形态切换动画起点
        self._ok_anim_dur = 0.0          # 形态切换动画时长（0 = 无动画）
        self._ok_anim_from = 1.0         # 切换动画起始字形缩放
        self._ok_pulse_t0 = 0.0          # 单次点击脉冲起点
        self._ok_release_at = 0.0        # 点满后的"完成"保持截止（让这一下被看见，阈值改了此处文案仍成立）
        # ---- OK 态自动消失（回到对话窗口 + 窗口重新获得焦点 → 平滑淡出）----
        self._ok_entered_at = 0.0          # 进入 OK 的时刻（自动消失计时基准）
        self._ok_fg_at_entry = False        # 进入 OK 时对话窗口是否已在前台（用户没离开 → 走宽限）
        self._ok_auto_dismissed = False     # 本轮完成是否已被焦点自动收起过（仅作状态标注，不影响再触发）
        self._wb_hwnd = None                # 探测到的 WorkBuddy 主窗口句柄（缓存，定期自愈）
        self._wb_hwnd_checked_at = 0.0      # 上次探测句柄的时刻（过期则重新 EnumWindows）
        self._wb_fg_prev = False            # 上一帧对话窗口是否前台（上升沿判定）
        # ---- 联动关闭（WorkBuddy 退出 → 桌宠一并退出）----
        # 由守望进程拉起时注入 WB_PET_LINKED=1；手动直接运行 hover.py 时为空 → 不自动关（测试友好）。
        self._linked_expected = os.environ.get("WB_PET_LINKED") == "1"
        self._wb_proc_name = os.environ.get(WB_LINKED_TARGET_ENV) or WB_PROC_NAME  # 测试钩子可覆盖
        self._wb_pid = None                 # WorkBuddy 进程 PID（缓存）
        self._wb_proc_handle = None         # WorkBuddy 进程句柄（SYNCHRONIZE，等它结束）
        self._wb_seen_once = False          # 是否至少见过一次 WorkBuddy 存活（建立基线才允许联动关）
        self._wb_gone_since = 0.0           # 首次判定"已消失"的时刻（宽限计时）
        self._wb_gone_checks = 0            # 连续"已消失"次数
        self._wb_probe_at = 0.0             # 上次窗口/进程探测时刻（限频）
        self._wb_probe_done = False         # 是否已完成过一次探测
        self._wb_alive_cache = True         # 探测结果缓存（True/False）
        self._linked_closed = False         # 是否已因联动关闭而退出（防重复）
        self._spawning_dash = False
        self._spawn_lock = threading.Lock()
        # ---- 外观状态（设置持久化）----
        self._settings = self._load_settings()
        # 自更新：后台检查结果缓存 + 是否在处理（网络全在后台线程，绝不进消息循环）
        self._update_info = None
        self._update_busy = False
        self._size_t0 = 0.0
        # 帧序列动作（AI 视频 → 透明帧）：坐着 / 变困 / 困了 / 拿本子 / 写字
        # 缺哪个动作就少哪个，不报错（load_all 只收存在的）
        self._clips = ANIM.load_all()
        self._anim_key = None                         # (动作, 素材名)，变了就重置计时
        self._anim_t0 = 0.0
        self._anim_caches = {}                        # {动作名: [(Surface,w,h), ...]}
        self._anim_imgs = {}                          # {动作名: {帧号: (img,w,h)}}
        # 抽签式兜底：没有 idle 就用任意一个，保证 _spr_cbox 一开始有值
        self._anim_clip = (self._clips.get(ANIM.ACT_IDLE)
                           or next(iter(self._clips.values()), None))
        # 动作状态机：任务开始/进行/完成 + 空闲犯困 → 决定播哪段
        self._wr = ANIM.PetPhase()
        self.scale = float(self._settings.get("scale", 1.0))
        self.bubble_on = bool(self._settings.get("bubble", True))
        self.sound_on = bool(self._settings.get("sound", True))
        # P2：两个行为开关从 wb_motion 常量默认值起步，UI 可改并持久化
        # （常量仍是出厂默认的唯一出处；实例标志才是运行时真相）
        self.ok_autodismiss_on = bool(self._settings.get(
            "ok_autodismiss", MOTION.OK_AUTODISMISS_ON))
        self.linked_close_on = bool(self._settings.get(
            "linked_close", MOTION.LINKED_CLOSE_ON))
        self._sounds = _make_sounds() if self.sound_on else {}
        # ---- 动作音效（AI 视频自带音轨，见 assets/anim/<动作>/_sfx.wav）----
        # 与上面那套**合成音效**分开：这套跟着「动作」走，不是跟着点击走
        self._anim_sfx = self._load_anim_sfx()
        self._anim_sfx_act = None        # 当前正在播的动作音效 (动作, 素材名)
        self._menu_open = False          # 右键菜单弹出期间：暂停重绘，把 UI 让给菜单
        self._sfx_resume_at = 0.0        # 互动音效播完后，何时把动作音效接回来
        # ---- 动效状态（提案 §1 待机 / §2 情绪 / §3 微交互 / §6 降级）----
        self._quality = self._load_quality()
        self._breath = 0.0            # 呼吸位移（px，向下为正）
        self._tail_dx = 0.0           # 尾鳍末端水平位移（px）
        self._float_dy = 0.0          # 漂浮位移（px）
        self._shadow_scale = 1.0      # 软阴影随漂浮缩放
        # ---- 跟随模式（聚焦信号，设计文档 P1）：前台 → agent，去抖 + 未知态 ----
        self.follow_on = bool(self._settings.get("follow", True))
        self._follow_tracker = FOLLOW.FollowTracker()
        self._follow_focus = None       # {"key","accent","kind","t0"} 当前确认身份
        self._follow_hwnd = None        # 钩子交付的原始 hwnd（解析放动画帧，回调不做重活）
        self._follow_last_seq = 0       # 前台事件序号（钩子回调递增）
        self._follow_seen_seq = -1      # 动画帧已消费的序号（-1 → 首帧即解析一次）
        self._follow_poll_n = 0         # 轮询兜底分频
        # ---- P2 连续锚点：跨 agent 统一时间线（10s 缓存，纯查询零新链路）----
        self._timeline_cache = None     # (at, (summary, recent)) 只读缓存，UI 线程绝不查库
        self._timeline_busy = False     # 后台刷新进行中
        # ---- P3 pin：锁定聚焦（锁定后跟随不覆盖；手动切换走「手动聚焦」子菜单）----
        self.focus_pin = bool(self._settings.get("focus_pin", False))
        self._focus_pin_key = self._settings.get("focus_pin_key") or None
        if self.focus_pin and self._focus_pin_key:
            _spec = (REG.load()["agents"].get(self._focus_pin_key) or {})
            self._follow_focus = {"key": self._focus_pin_key,
                                  "accent": _spec.get("accent"),
                                  "kind": "pin", "t0": 0.0}
        # ---- P4 接续：刚离开的 agent"似乎未结束" → 领结徽章角标；摘要用户触发 ----
        self._handoff = None            # {"key","title","since"} 或 None
        self._follow_hook = None
        self._follow_winproc = None     # 引用保活：被回收 = 钩子静默失效
        self._follow_hint_cache = None  # (at, hint_map) 5s 缓存
        if self.follow_on:
            self._install_follow_hook()
        self._phase_tail = random.uniform(0, math.pi * 2)   # 相位随机化防机械同步
        self._phase_float = random.uniform(0, math.pi * 2)
        self._gaze_dx = 0.0           # 头部朝光标的水平偏移
        self._gaze_target = 0.0
        self._hovering = False
        self._hover_t = 0.0           # hover 淡入淡出进度 0..1
        self._pose_diff_cache = {}    # {(k1,k2): 局部最大差异}
        self._pose_wh = (0, 0)
        self._squash = 1.0            # 点击压缩曲线值（1.0 = 常态）
        self._squash_t0 = 0.0
        self._drag_tilt = 0.0         # 拖拽倾斜
        self._emotion = MOTION.EMOTION_NEUTRAL
        self._emotion_until = 0.0
        self._press_at = 0.0          # 按下时刻（长按判定）
        self._frame_ms = 0.0          # 单帧绘制耗时（自动降级用）
        self._stale_list = []            # 残留会话清单（菜单项用，带 TTL 记忆）
        self._stale_ts = 0.0
        # 帧缓存（预缩放）状态：缓存依据 sig / 后台重建中 / 后台产物 / 待跟的目标
        self._cache_sig_done = None
        self._cache_building = False
        self._cache_new = None
        self._cache_pending_sig = None
        self._slow_frames = 0
        self._degraded_at = 0.0
        self._motion_sig = None       # 动效脏检测签名
        # ---- 互动反应状态 ----
        self._wobble_until = 0.0         # 甩尾摇摆截止时间
        self._wobble_amp = 0.0           # 摇摆幅度（px）
        self._particles = []             # {x,y,vx,vy,life,max,kind,size,phase}
        self._last_quote = ""

        # 帧序列的内容框：气泡锚点 / 命中区都按它算。
        # ⚠️ 各动作内容框**不一样**（坐着是近景 0.53、写字是全身 0.36），
        #    所以 _draw_anim_frame 每帧都会按当前动作刷新它。
        self._spr_cbox = {}
        if self._anim_clip:
            self._spr_cbox["__anim__"] = list(self._anim_clip.content_box)
        self._ok_img = self._load_ok_glyph()
        self._ok_glyph_cache = None       # (Surface, w, h) 按当前 scale 预缩放
        self._register_class()
        self.hwnd = None
        self.surf = None
        self._watch_armed = False         # WATCH 定时器是否已启用（按需挂载）
        self._visible = False             # 窗口是否已 ShowWindow
        # ---- 动画时钟（必须在 _arm_timers 之前初始化，见 _start_anim_clock）----
        self._anim_clock = None          # 高精度可等待定时器句柄
        self._anim_clock_on = False
        self._anim_clock_dead = False
        self._anim_pending = False       # 在途的 WM_APP_ANIM（合并语义）
        self._anim_last_tick = 0.0       # 最近一次动画 tick 时刻（看门狗）
        self._anim_i_key = None          # 抖动钳位：上一帧的（动作, 素材名）
        self._anim_i_prev = None         # 抖动钳位：上一帧的帧号（见 _draw_anim_frame）
        self._recreate_window(place=True)
        self.tick()
        self._arm_timers()                # 统一挂载定时器（hwnd 变化后需重新挂载）
        # 启动时后台预热时间线：首次右键/首批气泡就不用等（查库 3.6s 全在后台线程）
        self._kick_timeline_refresh()
        self._start_api_guard()           # 看板 API 守护（后台 daemon 线程，不做网络阻塞）
        self._start_update_watch()        # 自更新检查（未配更新源 → 线程直接退出，零联网）
        self._start_stale_repair()        # 启动自愈：残留 working 会话落回终态（重启即修复）
        # P3 全局热键（Ctrl+Alt+F9 手动聚焦轮换）：绑定桌宠 hwnd，
        # WM_HOTKEY 走既有消息泵；组合被占用 → 静默降级（右键菜单仍是兜底）
        self._install_follow_hotkey()
        threading.Thread(target=self._health_loop, daemon=True).start()

        # ---- 活跃探测（后台线程；探测绝不放消息循环）----
        # ⚠️ 用途是**「联动关闭」的宿主判据**（见 _host_alive），**与配件无关**；
        #    配件层（猫/贝雷帽/鲸鱼玩偶）已于 2026-09-27 整体删除，这段必须留着。
        self._presence = None
        self._spr_rect = None            # 上一帧立绘矩形（命中区 / 气泡锚点用）
        self._spr_key = ("idle", "")
        try:
            self._presence = PRESENCE.PresenceDetector().start()
        except Exception:
            log_exception("[presence] 活跃探测启动失败")
            self._presence = None

    # ---- 设置持久化 ----
    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self):
        try:
            # 合并写入：整体重写会抹掉本文件之外的键（踩过：保存后别人的键丢失）
            data = {}
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                pass
            data.update({"sound": self.sound_on, "scale": self.scale,
                         "bubble": self.bubble_on,
                         "quality": self._quality,
                         "ok_autodismiss": self.ok_autodismiss_on,
                         "linked_close": self.linked_close_on,
                         "follow": self.follow_on,
                         "focus_pin": self.focus_pin,
                         "focus_pin_key": self._focus_pin_key})
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---- 动效：质量档位 / 眼部配置 ----
    def _load_quality(self):
        q = self._settings.get("quality", MOTION.QUALITY_FULL)
        if q not in (MOTION.QUALITY_FULL, MOTION.QUALITY_LITE, MOTION.QUALITY_OFF):
            q = MOTION.QUALITY_FULL
        return q

    def set_quality(self, q):
        """切换动效强度并持久化（右键菜单 / 自动降级共用）。"""
        self._quality = q
        self._save_settings()
        self._drawn_sig = None
        self._motion_sig = None
        self._report_event("quality", detail=q)

    def _toggle_ok_autodismiss(self):
        """P2：完成态自动收起开关（右键菜单）。wb_motion 的 *_ON 是出厂默认，
        这里起实例标志并持久化——运行时只认实例标志。"""
        self.ok_autodismiss_on = not self.ok_autodismiss_on
        self._report_event("menu_ok_autodismiss", detail=str(self.ok_autodismiss_on))
        self._save_settings()

    def _toggle_linked_close(self):
        """P2：随 Agent 退出联动关闭开关（右键菜单）。关闭后桌宠只走手动退出
        与守望兜底，不做逐帧联动判定。"""
        self.linked_close_on = not self.linked_close_on
        self._report_event("menu_linked_close", detail=str(self.linked_close_on))
        self._save_settings()

    def _toggle_follow(self):
        """P1：跟随前台切换聚焦开关（右键菜单）。关闭 → 徽章消失、
        前台不再解析；重新打开 → 补装钩子并立刻解析一次。"""
        self.follow_on = not self.follow_on
        if self.follow_on and not self._follow_hook:
            self._install_follow_hook()
        self._report_event("menu_follow", detail=str(self.follow_on))
        self._save_settings()
        self._drawn_sig = None

    # ---- P3 全局热键（Ctrl+Alt+F9 手动聚焦轮换）----
    def _install_follow_hotkey(self):
        """RegisterHotKey 绑定桌宠 hwnd；组合被占用 → 静默降级
        （右键「手动聚焦」子菜单仍是兜底，不弹窗打扰）。"""
        try:
            ok = _user32.RegisterHotKey(
                self.hwnd, ID_HOTKEY_FOLLOW,
                MOTION.FOLLOW_HOTKEY_MODS, MOTION.FOLLOW_HOTKEY_VK)
            return bool(ok)
        except Exception:
            return False

    def _uninstall_follow_hotkey(self):
        try:
            _user32.UnregisterHotKey(self.hwnd, ID_HOTKEY_FOLLOW)
        except Exception:
            pass

    def _focus_candidates(self):
        """手动聚焦候选（登记册启用 agent，按 order/label 排序）——热键与子菜单共用。"""
        return [k for k, v in sorted(REG.load()["agents"].items(),
                                     key=lambda kv: (kv[1].get("order", 100),
                                                     kv[0]))
                if v.get("enabled")]

    def _hotkey_cycle_focus(self):
        """全局热键：轮换到下一个启用 agent 并自动 pin（手动模式不被跟随覆盖）。"""
        key = FOLLOW.cycle_next(self._focus_candidates(),
                                (self._follow_focus or {}).get("key"))
        if not key:
            return
        if not self.focus_pin:
            self._toggle_focus_pin()      # 手动轮换即进入手动模式（设计 §7#1）
        self._set_manual_focus(key)
        self._report_event("hotkey_cycle", detail=key)

    def _toggle_focus_pin(self):
        """P3：锁定聚焦（pin）。锁定后跟随不覆盖当前聚焦；解开 → 跟随即刻接管。
        锁定时把聚焦 key 持久化，重启后恢复（设计 §8 P0 聚焦状态持久化）。"""
        self.focus_pin = not self.focus_pin
        self._focus_pin_key = (self._follow_focus or {}).get("key") \
            if self.focus_pin else None
        self._report_event("menu_focus_pin", detail=str(self.focus_pin))
        self._save_settings()
        self._drawn_sig = None

    def _set_manual_focus(self, key):
        """P3：手动聚焦到某个 agent（「手动聚焦」子菜单）。跟随开启时会被下一次
        前台变化覆盖（手动是兜底，跟随是主路径）；pin 期间则保持。
        同步去抖状态机（force），否则状态机以为身份没变 → 跟随静默失效。"""
        spec = (REG.load()["agents"].get(key) or {})
        self._follow_focus = {"key": key, "accent": spec.get("accent"),
                              "kind": "change", "t0": time.time()}
        self._follow_tracker.force(key)
        self._report_event("follow_manual", detail=key)
        self._drawn_sig = None

    # ---- 环境联动 ----
    def _night_mode(self):
        """夜间联动（跟随 P4 同族的"环境联动"）：23:00–07:00 → 眨眼频率降低
        + 渲染层半眯眼慵懒态。屏幕亮度读取不可移植，用本地时段替代。"""
        h = time.localtime().tm_hour
        return h >= 23 or h < 7

    # ---- P4 接续：摘要生成（用户触发才生成；必过脱敏；复制到剪贴板）----
    def _copy_to_clipboard(self, text):
        """CF_UNICODETEXT 复制（ctypes 标准写法）。失败静默——不打扰用户。"""
        CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
        try:
            data = text.encode("utf-16-le") + b"\x00\x00"
            _user32.OpenClipboard.argtypes = [wt.HWND]
            _user32.EmptyClipboard.restype = wt.BOOL
            _user32.SetClipboardData.restype = ctypes.c_void_p
            _user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
            _user32.CloseClipboard.restype = wt.BOOL
            _kernel32.GlobalAlloc.restype = ctypes.c_void_p
            _kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
            _kernel32.GlobalLock.restype = ctypes.c_void_p
            _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            if not _user32.OpenClipboard(self.hwnd or None):
                return False
            _user32.EmptyClipboard()
            h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = _kernel32.GlobalLock(h)
            if p:
                ctypes.memmove(ctypes.c_void_p(p), data, len(data))
                _kernel32.GlobalUnlock(h)
            _user32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(h))
            _user32.CloseClipboard()
            return True
        except Exception:
            try:
                _user32.CloseClipboard()
            except Exception:
                pass
            return False

    def _handoff_cwd(self, agent_key):
        """该 agent 最近一轮的工作目录（注入降级链第二档的目标位置）——
        cwd 本身就是 agent 的工作目录，handoff.md 直接写在那里。
        拿不到或目录不存在 → None（降级到剪贴板）。"""
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True,
                                   timeout=3)
            try:
                row = conn.execute(
                    """SELECT cwd FROM ods_jsonl_event
                        WHERE agent = ? AND cwd != ''
                        ORDER BY ts_ms DESC LIMIT 1""", (agent_key,)).fetchone()
            finally:
                conn.close()
            cwd = str(row[0]).strip() if row else ""
            return cwd if cwd and os.path.isdir(cwd) else None
        except Exception:
            return None

    def _gen_handoff(self):
        """P4：用户右键触发才生成交接摘要（必过脱敏），按能力降级注入：
        ① 写 agent 最近工作目录的 handoff.md（CLI 型可直接读到）；
        ② 目录不可得/写入失败 → 复制到剪贴板（保底通道）。
        生成后角标消失；全程无弹窗打扰；埋点 handoff_expand 记录通道。"""
        h = self._handoff
        if not h:
            return
        try:
            import sqlite3
            sys_dir = os.path.dirname(os.path.abspath(__file__))
            udir = os.path.join(sys_dir, "wb_usage")
            if udir not in sys.path:
                sys.path.insert(0, udir)
            from wb_common import mask_text
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True,
                                   timeout=3)
            try:
                # ⚠️ 走 turn_src（优先物化表）：直查 v_turn_total 视图实测 10.3s
                #    —— 点一次「生成接续摘要」就冻十秒（与跟随切换同一个坑）。
                row = conn.execute(
                    f"""SELECT COALESCE(NULLIF(user_prompt, ''), ''),
                              COALESCE(NULLIF(title, ''), '')
                         FROM {turn_src(conn, self.db_path)} WHERE agent = ?
                        ORDER BY last_time DESC LIMIT 1""", (h["key"],)).fetchone()
            finally:
                conn.close()
            prompt = row[0] if row else ""
            title = row[1] if row else (h.get("title") or "")
            self._report_event("handoff_expand", detail=h["key"])
            label = ((REG.load()["agents"].get(h["key"]) or {}).get("label")
                     or h["key"])
            summary = (f"# 接续自 {label}（{mask_text(title, 40)}）\n\n"
                       f"最后一问：{mask_text(prompt, 160)}\n\n"
                       f"请基于以上上下文继续。")
            # 降级链第一档：handoff.md 写进 agent 最近的工作目录
            cwd = self._handoff_cwd(h["key"])
            if cwd:
                try:
                    with open(os.path.join(cwd, MOTION.HANDOFF_FILENAME),
                              "w", encoding="utf-8") as f:
                        f.write(summary)
                    self._report_event("handoff_expand",
                                       detail=f"{h['key']} via handoff.md")
                    self._handoff = None
                    self._drawn_sig = None
                    return
                except Exception:
                    pass                      # 写失败 → 降级剪贴板
            # 保底通道：剪贴板
            self._copy_to_clipboard(summary)
            self._report_event("handoff_expand", detail=f"{h['key']} via clipboard")
            self._handoff = None
            self._drawn_sig = None
        except Exception:
            log_exception("[handoff] 生成失败")



    # ---- 立绘资源 ----



    def _load_ok_glyph(self):
        """加载「OK」完成态字形（透明 PNG）。

        资源缺失不是致命错误：绘制时退化成程序化橙环，状态依然可辨，
        只是没有参考字形好看。用 tools/make_ok_glyph.py 可从参考图重新生成。
        """
        path = os.path.join(ASSETS_DIR, "ok_glyph.png")
        img = P()
        if _LoadImage(path, ctypes.byref(img)) != 0 or not img:
            log_exception(f"[ok] 未找到 OK 字形 {path}（可跑 tools/make_ok_glyph.py 生成）")
            return None
        w, h = U32(0), U32(0)
        _GetImageW(img, ctypes.byref(w))
        _GetImageH(img, ctypes.byref(h))
        return (img, w.value, h.value)

    # ---- 布局 ----
    def _layout(self):
        sc = self.scale
        bub_h = int(BASE_BUB_H * sc)
        bubble_h = int(BASE_BUBBLE_H * sc) if self.bubble_on else 0
        pet_h = int(BASE_PET_H * sc)
        return {"W": int(BASE_W * sc), "H": bubble_h + pet_h,
                "bub_h": bub_h, "bubble_h": bubble_h,
                "pet_h": pet_h, "sc": sc}

    # ---- 窗口 ----
    def _register_class(self):
        wc = WNDCLASSW()
        wc.style = 0x0008                        # CS_DBLCLKS
        wc.lpfnWndProc = ctypes.cast(_WNDPROC_STUB, ctypes.c_void_p)
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.hCursor = _user32.LoadCursorW(None, ctypes.cast(ctypes.c_void_p(32512), wt.LPCWSTR))
        wc.lpszClassName = CLASS_NAME
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:
                raise RuntimeError(f"RegisterClassW 失败 (err={err})")

    def _create_window(self, x, y, w, h):
        hwnd = _user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            CLASS_NAME, "WB Whale Pet", WS_POPUP, x, y, w, h, None, None,
            _kernel32.GetModuleHandleW(None), None)
        if not hwnd:
            raise RuntimeError(f"CreateWindowExW 失败 (err={ctypes.get_last_error()})")
        return hwnd

    def _recreate_window(self, place=False):
        """按当前 scale / bubble_on 应用布局（缩放、气泡开关时调用）。

        【关键】已有窗口时只改尺寸，**不重建 hwnd**。
        定时器是挂在 hwnd 上的：旧窗口一旦 DestroyWindow，
        ID_TIMER_TICK / ID_TIMER_ANIM / ID_TIMER_WATCH 会随之销毁，
        新窗口又没有重新挂载 → 动画帧、数据刷新、点击反馈全部静默失效，
        只剩走同步调用路径的「双击气泡开看板」还能用。
        """
        lay = self._layout()
        if self.hwnd:
            # 尺寸变更：保留 hwnd（保住定时器/消息路由），底部锚定（立绘在下）
            r = RECT()
            _user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            x, y, old_h = r.left, r.top, r.bottom - r.top
            ax, ay, aw, ah = work_area()
            nx = max(ax + 4, min(x, ax + aw - lay["W"] - 4))
            ny = max(ay + 4, min(y + old_h - lay["H"], ay + ah - lay["H"] - 4))
            _user32.SetWindowPos(self.hwnd, ctypes.c_void_p(HWND_TOPMOST),
                                 int(nx), int(ny), int(lay["W"]), int(lay["H"]), 0)
            if self._home:                     # 同步"家"的坐标，避免漂回旧位置
                self._home = (int(nx), int(ny))
        else:
            hwnd = self._create_window(0, 0, lay["W"], lay["H"])
            self.hwnd = hwnd
            _WINDOWS[hwnd] = self
        if self.surf:
            self.surf.close()
        self.surf = Surface(lay["W"], lay["H"])
        # ★ 帧缓存只跟**显示尺寸**有关（见 _cache_sig）：气泡开关这类只改窗口高度的切换
        #   根本不重建 —— 重建要重新解码+重采样 525 帧 ≈ 3.2s，白冻 UI 三秒。
        #   真要改尺寸时：首次（启动）同步建（否则首帧没素材），之后**后台建**，
        #   UI 线程立刻返回（先用旧缓存拉伸顶一下，见 _draw_anim_frame 的放宽条件）。
        _sig = self._cache_sig()
        if _sig != getattr(self, "_cache_sig_done", None):
            if getattr(self, "_cache_sig_done", None) is None:
                self._build_draw_cache()
            else:
                self._rebuild_cache_async(_sig)
        if place:
            self._place()
        self._drawn_sig = None
        self.draw()
        if not self._visible:
            _user32.ShowWindow(self.hwnd, SW_SHOWNA)
            self._visible = True
        self._arm_timers()

    def _arm_timers(self):
        """把定时器挂到当前 hwnd 上。SetTimer 同 ID 会原地更新，可安全重复调用。

        动画帧有两条路：优先**高精度时钟线程**（`_start_anim_clock`），
        拿不到才退回 `SetTimer`（周期会被系统粒度拉长到 46.8ms，但有防抖钳位兜底）。
        """
        if not self.hwnd:
            return
        _user32.SetTimer(self.hwnd, ID_TIMER_TICK, TICK_MS, None)
        if self._start_anim_clock():
            # 别和 SetTimer 双重投递（那才是真跳帧）
            _user32.KillTimer(self.hwnd, ID_TIMER_ANIM)
        else:
            _user32.SetTimer(self.hwnd, ID_TIMER_ANIM, ANIM_MS, None)
        if self._watch_armed:
            _user32.SetTimer(self.hwnd, ID_TIMER_WATCH, WATCH_MS, None)

    def _start_anim_clock(self):
        """起高精度动画时钟（后台线程 + 可等待定时器），返回是否由它接管。

        **为什么要它**（2026-09-30 用户报障「写字不流畅」）：系统默认定时器粒度 15.6ms
        → `SetTimer(41)` 实际 46.8ms，写字态实测只有 21.3fps；本机 `timeBeginPeriod(1)`
        也无效。高精度可等待定时器走内核高精度时钟，实测稳定 41ms → 24.4fps。
        拿不到就返回 False（调用方退回 SetTimer，行为与从前一致）。
        """
        if self._anim_clock_on:
            return True
        if self._anim_clock_dead:                # 试过且失败：别每轮重建
            return False
        h = _make_hires_timer(ANIM_MS)
        if not h:
            self._anim_clock_dead = True
            return False
        self._anim_clock = h
        self._anim_clock_on = True
        threading.Thread(target=self._anim_clock_loop, name="wb-anim-clock",
                         daemon=True).start()
        return True

    def _anim_clock_loop(self):
        """时钟线程：**只做「等 → 投递消息」**，绝不碰 UI / 不查库（本项目铁律）。

        投递带**合并语义**（同时只有一个在途消息）—— 对齐 `WM_TIMER` 的行为：
        万一 UI 线程被卡住 1 秒，也不会攒下 20 多个 tick 一次性补跑（那是"快进抽帧"）。
        """
        h = self._anim_clock
        while self._anim_clock_on and h == self._anim_clock:
            try:
                r = _kernel32.WaitForSingleObject(h, 250)
            except Exception:
                break
            if r != _WAIT_OBJECT_0:          # 超时（0x102）→ 回头检查退出条件
                continue
            hwnd = self.hwnd
            if not hwnd or self._anim_pending:
                continue
            self._anim_pending = True
            try:
                _user32.PostMessageW(hwnd, WM_APP_ANIM, 0, 0)
            except Exception:
                self._anim_pending = False

    def _stop_anim_clock(self):
        self._anim_clock_on = False
        self._anim_pending = False
        h, self._anim_clock = self._anim_clock, None
        if h:
            try:
                _kernel32.CloseHandle(h)     # 线程的等待会立刻返回并退出
            except Exception:
                pass

    def _arm_watch(self):
        """台词期间用：外部点击检测（按需挂载，记住状态以便 hwnd 变化后恢复）。"""
        self._watch_armed = True
        if self.hwnd:
            _user32.SetTimer(self.hwnd, ID_TIMER_WATCH, WATCH_MS, None)

    # ---- 立绘预缩放缓存（每帧 AlphaBlend 1:1 合成，避免每帧重采样大图）----

    # ---- 显示尺寸预缩放缓存（每帧 AlphaBlend 1:1 合成，避免每帧重采样大图）----
    def _cache_sig(self):
        """帧缓存的构建依据 —— **只跟显示尺寸有关**。

        五个动作的 `head_scale` 是静态的，`pet_h`/`g_h` 都只由 `sc` 派生，所以：
        **气泡开关（只改窗口高度）不需要重建缓存**。
        （2026-09-29：右键菜单里点「想法气泡」原来会重建 → 重新解码+重采样 525 帧 ≈ 3.2s
          的 UI 线程冻结，用户报的"选择设置时卡顿"就是这一下。）
        """
        lay = self._layout()
        return (lay["sc"], lay["pet_h"], ok_spec(lay)["g_h"])

    def _build_cache_objects(self):
        """按当前显示尺寸产出预缩放对象（**不碰 self**，可在后台线程里跑）。

        返回 (anim_caches, cache_surfs, ok_glyph_cache)；失败抛异常由调用方兜。
        为什么拆出来：重建要重新解码 + 重采样 525 帧（GDI+ 高分插值 3.9ms/帧 ≈ 3s），
        必须能放到后台线程做 —— 否则改一次「桌宠大小」就冻住 UI 三秒。
        """
        anim_caches, cache_surfs = {}, []
        lay = self._layout()
        ph = lay["pet_h"] - 8 * lay["sc"]
        for _name, _clip in (getattr(self, "_clips", {}) or {}).items():
            _buf = []
            for k in range(_clip.count):
                info = self._anim_image(k, _name)
                if not info:
                    continue
                aimg, aiw, aih = info
                # ★ 按头大小对齐：近景动作整体缩小（见 wb_anim.HEAD_SCALE）
                _ph = ph * _clip.head_scale
                apw, aph = int(_ph * aiw / aih), int(_ph)
                atmp = Surface(apw, aph)
                atmp.clear()
                atmp.image(aimg, 0, 0, apw, aph)
                _buf.append((atmp, apw, aph))
                cache_surfs.append(atmp)
            # ★ 预缩放建完就把**原始位图**扔掉：它只在建缓存时用得上。
            #   24fps 下 5 个动作 500+ 帧，原始位图（每帧约 950KB）要占 500MB 上下，
            #   而预缩放后的那份（每帧约 350KB）才是真正每帧要画的 —— 留一份就够。
            #   （改窗口尺寸时会走回 _anim_image 重新加载，所以扔了不影响 resize。）
            for _ent in (getattr(self, "_anim_imgs", {}) or {}).get(_name, {}).values():
                if _ent:
                    try:
                        _DisposeImage(_ent[0])
                    except Exception:
                        pass
            self._anim_imgs.pop(_name, None)
            anim_caches[_name] = _buf
        # OK 图形同样按「当前尺寸 + 当前方案」预缩放（分层窗口要 1:1 合成）
        ok_glyph = None
        if getattr(self, "_ok_img", None):
            img, iw, ih = self._ok_img
            gh = max(1, int(round(ok_spec(lay)["g_h"])))
            gw = max(1, int(round(gh * iw / ih)))
            tmp = Surface(gw, gh)
            tmp.clear()
            tmp.image(img, 0, 0, gw, gh)
            ok_glyph = (tmp, gw, gh)
            cache_surfs.append(tmp)
        return anim_caches, cache_surfs, ok_glyph

    def _apply_cache(self, anim_caches, cache_surfs, ok_glyph, sig):
        """把一份建好的缓存换上（**只能在 UI 线程调用**），并回收旧的对象。"""
        for tmp in getattr(self, "_cache_surfs", []):
            try:
                tmp.close()
            except Exception:
                pass
        self._anim_caches = anim_caches or {}
        self._ok_glyph_cache = ok_glyph
        self._cache_surfs = list(cache_surfs or [])
        self._cache_sig_done = sig
        self._drawn_sig = None

    def _rebuild_cache_async(self, sig):
        """后台重建帧缓存：UI 线程不等待，先用旧缓存（拉伸）顶到新就位。

        期间窗口已按新尺寸重排、`_cache_building=True` 让 `_draw_anim_frame` 放宽
        "尺寸差太多就跳过"那条规则（否则缩放跳档时她会**整帧不画 = 消失**）。
        """
        if self._cache_building:
            self._cache_pending_sig = sig          # 正在建 → 记下最新目标，建完再跟一轮
            return
        self._cache_building = True

        def job():
            try:
                objs = self._build_cache_objects()
                err = None
            except BaseException as e:             # 后台线程：异常只能自己咽下
                objs, err = None, e
            if err is not None:
                try:
                    log_exception(f"[cache] 后台重建失败: {err}")
                except Exception:
                    pass
            self._cache_new = (sig, objs)          # 原子换引用，UI 线程下一 tick 取走

        threading.Thread(target=job, daemon=True, name="komi-cache-build").start()

    def _wait_cache_new(self):
        """（UI 线程）把后台建好的缓存换上；有更新的目标就再跟一轮。"""
        got = self._cache_new
        if got is None:
            return
        self._cache_new = None
        sig, objs = got
        self._cache_building = False
        if objs is not None:
            self._apply_cache(objs[0], objs[1], objs[2], sig)
            self._drawn_sig = None
            self.draw()
        nxt = self._cache_pending_sig
        self._cache_pending_sig = None
        if nxt is not None and nxt != self._cache_sig_done:
            self._rebuild_cache_async(nxt)

    def _build_draw_cache(self):
        """同步重建（启动首建用；改尺寸一律走 `_rebuild_cache_async`）。

        2026-09-28：原 `_build_sprite_cache` 里的**立绘 / 眨眼贴片 / 形态尺寸归一**
        三部分随 v3 立绘一起删除，只剩这两样。
        """
        for tmp in getattr(self, "_cache_surfs", []):
            try:
                tmp.close()
            except Exception:
                pass
        self._cache_surfs = []
        if not getattr(self, "surf", None):
            return
        anim_caches, surfs, okg = self._build_cache_objects()
        self._apply_cache(anim_caches, surfs, okg, self._cache_sig())

    def _place(self):
        lay = self._layout()
        ax, ay, aw, ah = work_area()
        saved = load_pos(POS_FILE)
        if saved:
            x, y = saved
        else:
            x, y = ax + aw - lay["W"] - 20, ay + ah - lay["H"] - 16
        bx0, by0, bx1, by1 = self._drag_bounds(lay)
        x = max(bx0, min(x, bx1))
        y = max(by0, min(y, by1))
        self._move_window(self.hwnd, int(x), int(y))
        self._home = (int(x), int(y))

    def _move_window(self, hwnd, x, y):
        # 菜单开着时**只挪位置、不碰 z 序**：菜单是自绘 topmost 窗口，靠每次绘制
        # `_pin_top()` 压在桌宠之上；桌宠若在这期间再抢一次 TOPMOST，人物就会盖回
        # 菜单（用户报障现场）。SWP_NOZORDER 让 hWndInsertAfter 参数直接被忽略。
        flags = SWP_NOSIZE | SWP_NOACTIVATE | (SWP_NOZORDER if self._menu_open else 0)
        _user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), x, y, 0, 0, flags)

    def _window_xy(self, hwnd):
        r = RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top

    def _drag_bounds(self, lay):
        """窗口可移动范围 (min_x, min_y, max_x, max_y)。

        ⚠️ 上界**不能**取「工作区上沿 + 4」：窗口上沿之上还有一大段**透明留白**
        （气泡区 162*sc + 帧内上方的透明边 ≈ 300+px），拿窗口上沿当"头顶"会让
        桌宠离屏幕上边空出一大截 —— 2026-09-29 用户报障「向上移动只能达到这里」。
        改成按**可见内容顶**算上界（气泡开着 = 气泡上沿，关气泡 = 人物头顶）：
        允许那段透明留白整段顶出屏幕外，可见内容能贴到屏幕上边。
        左右与下界不用改（人物底边本就贴着窗口底边）。
        """
        ax, ay, aw, ah = work_area()
        min_y = int(ay - self._dead_top(lay) + 4)
        max_y = int(ay + ah - lay["H"] - 4)
        return (int(ax + 4), min(min_y, max_y),
                int(ax + aw - lay["W"] - 4), max_y)

    def _snap(self, x, y):
        """贴边吸附：松手距边缘 ≤ DRAG_EDGE 则贴边（含四角组合）。

        边界与拖动夹取**同源**（`_drag_bounds`）—— 否则吸附能把窗口推到
        夹取范围之外，下次启动 `_place` 又会把它拽回来（位置会"神秘"漂移）。
        """
        lay = self._layout()
        bx0, by0, bx1, by1 = self._drag_bounds(lay)
        nx, ny = x, y
        if x - bx0 <= DRAG_EDGE:
            nx = bx0
        elif bx1 - x <= DRAG_EDGE:
            nx = bx1
        if y - by0 <= DRAG_EDGE:
            ny = by0
        elif by1 - y <= DRAG_EDGE:
            ny = by1
        return int(nx), int(ny)

    # ---- 数据 / 后台健康 / 事件日志 ----
    def _health_loop(self):
        while True:
            try:
                self.api_ok = api_healthy(self.dashboard)
            except Exception:
                pass
            time.sleep(2.0)

    def _report_event(self, ev, ok=None, detail=""):
        try:
            rotate_if_large(EVENT_LOG)               # P1：超 1.5MB 轮转（保留一代）
            line = json.dumps(
                {"ts": time.strftime("%H:%M:%S"), "ev": ev, "ok": ok, "d": detail},
                ensure_ascii=False)
            with open(EVENT_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _bubble_lines(self):
        """气泡三行文案。

        设计要点（修本轮用量实时刷新）：
        - row1 标题 = "正在对话"（workbuddy.db 标记 working）或 "最近对话"（仅有 ODS 残留 turn）；
          空态返回空串（2026-09-29 用户反馈：不显示"当前没有活跃会话"）
        - row2 数据 = self.latest_turn（ODS 最近有数据的 turn，不依赖 working 状态）
          用时算到 now（live 模式，每 tick 重绘 +1s）；积分制 agent（credit>0）显示
          "本轮 X 积分"（渲染端染粉），非积分 agent 只显示 tok/用时
        - row3 恒为空串（2026-09-29 用户反馈：气泡第三行任何时候都不出现）——
          原「今天：Codex 3 轮 · WorkBuddy 12 轮 · 双击开看板」/ 回退「活跃 X 分钟前」整体下线；
          今日时间线数据保留在右键菜单「今日时间线」子菜单（_today_timeline 未删）
        """
        online = not (self.api_ok is False or not self.db_ok)
        live = self.latest_turn  # 最近 30 分钟内有数据的 turn（独立于 working）

        # ---- row1 标题 ----
        title = ""
        n_active = len(self.active)
        working_turn = self.active[0] if n_active else None
        if working_turn:
            title = (working_turn.get("_wb_title") or working_turn.get("title")
                     or working_turn.get("project") or "对话")
            row1 = (f"{n_active} 个会话运行中 · {title[:8]}"
                    if n_active > 1 else f"正在对话 · {title[:12]}")
        elif live:
            title = (live.get("title") or live.get("project") or "对话")
            row1 = f"最近对话 · {title[:12]}"
        elif not self.db_ok:
            return "数据源读取中…", "等待 WorkBuddy 会话记录", ""
        else:
            # 空态（2026-09-29 用户反馈）：不显示"当前没有活跃会话"标题，
            # 也不显示"数据每秒自动刷新"——只留一句引导（渲染端对空行跳过并聚拢）
            return "", "双击打开看板查看今日用量", ""

        # ---- row2 数据：用 live（最近有数据 turn），不用 working 等待态 ----
        if live:
            credit = live.get("credit") or 0
            tokens = fmt_tokens(live.get("total_tokens") or 0)
            # live 模式：用时算到 now（workbuddy 标记 working 时）或 last_ts（仅残留 turn 时）
            if working_turn:
                dur = fmt_duration_live(live)            # first_ts → now，每 tick 自动 +1
            else:
                dur = fmt_duration(live)                # first_ts → last_ts，最终耗时
            # 积分制 agent（credit>0，如 WorkBuddy）→ "本轮 X 积分"（渲染端染樱花粉）；
            # 非积分 agent（zcode/codex/claude 等 credit 恒 0）→ 不显示积分
            # （原先恒显示"0.0 分"，对非积分 agent 是噪音——2026-09-29 用户反馈去除）
            if credit > 0:
                row2 = f"本轮 {credit:.1f} 积分 · {tokens} tok · 用时 {dur}"
            else:
                row2 = f"本轮 {tokens} tok · 用时 {dur}"
        else:
            # working 但 ODS 真的没数据（新对话刚开、第一笔调用未到达）——
            # 不显示 0（之前 bug），直接给"等待下一笔"
            row2 = "本轮等待首笔调用…"

        # ---- row3：整体下线（2026-09-29 用户反馈「图中圈出的地方删除，任何时候都不要出现」）----
        # 曾是 P2 连续锚点的跨 agent 叙述「今天：Codex 3 轮 · WorkBuddy 12 轮 · 双击开看板」
        # （无数据回退「活跃 X 分钟前 · 项目 · 双击开看板」）。气泡从此固定最多两行。
        # _today_timeline / format_timeline_summary 仍被右键菜单「今日时间线」子菜单使用，保留。
        return row1, row2, ""

    def _in_talk(self):
        return self._talk_until and time.time() < self._talk_until

    # ---- 气泡形态状态机：default（数据态）↔ ok（对话任务完成态）----
    def _set_bubble_mode(self, mode, now=None, sound=True):
        """切换气泡形态。幂等 —— 同态调用直接返回，避免重复播动画/音效。

        sound=False 用于"焦点自动消失"：只是已读收起，不打扰（不播 pop）。
        """
        if mode == self._bub_mode:
            return
        now = time.time() if now is None else now
        self._bub_mode = mode
        self._ok_clicks = 0
        self._ok_last_click = 0.0
        self._ok_release_at = 0.0
        self._ok_anim_t0 = now
        if mode == BUBBLE_OK:
            self._ok_anim_dur = MOTION.OK_POP_IN_S
            self._ok_anim_from = MOTION.OK_GLYPH_MIN_SCALE
            self._ok_entered_at = now
            self._ok_fg_at_entry = self._is_wb_focused(now)
            self._ok_auto_dismissed = False
            if sound:
                self._play("chirp")
            lay = self._layout()
            _bb = self._bubble_box(lay)          # 气泡锚定头顶，迸发点跟着走
            self._spawn_particles("spark", 6, lay["W"] / 2, _bb[1] + _bb[3] / 2)
        else:
            self._ok_anim_dur = MOTION.OK_POP_OUT_S
            self._ok_anim_from = 1.0
            if sound:
                self._play("pop")
        self._drawn_sig = None
        self._motion_sig = None
        self._report_event("bubble_mode", detail=mode)

    def _clear_stuck_drag(self):
        """拖拽卡死兜底：左键实际已抬起、状态却还停在"拖拽中" → 清掉。

        为什么必须兜底：`tick` 里的数据刷新被 `if not self._dragging` 包着 ——
        一旦 `_dragging` 卡在 True，**整条数据链（活跃会话数 / 气泡 / 动作流转的
        喂数）就永久停摆**，她就定在当前动作不动（用户报的"卡在某个状态"）。
        成因是抬起事件没送到窗口：捕获被别的窗口抢走、菜单模态期被吃掉、
        或鼠标在别处松开。这里只看「物理按键还在不在」——最可靠的真相。
        """
        if self._dragging and not (_user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
            self._dragging = False
            self._pressed = False
            self._swallow_up = False
            self._drawn_sig = None
            self._report_event("drag_stuck_cleared")
            return True
        return False

    def _read_stale(self, ok, now):
        """读数是否已"连续失败太久"（→ 调用方应放弃记着的活跃会话）。返回 bool。

        ok=True 时刷新成功时刻。ok=False 且距上次成功超过 READ_STALE_SEC → True。
        为什么需要：`query_db` 失败时约定保留上次值防闪烁，但失败若持续下去，
        "上次值"就变成了永久真理——她会一直以为自己有任务在跑。
        """
        if ok:
            self._read_ok_at = now
            return False
        return bool(self._read_ok_at and now - self._read_ok_at > READ_STALE_SEC)

    def _stable_active_n(self, n_raw, now):
        """活跃数防抖：**上升立即生效**，**下降需连续 ACTIVE_FALL_DEBOUNCE_S**。

        为什么只对下降防抖：来任务要马上拿本子（延迟看得出来），而"任务结束"
        是唯一会让桌宠回落的时刻——宿主 working 行/数据源短抖会让 n 掉到 0 又弹回，
        她就当着用户的面反复"举本子 → 坐下 → 拿本子"。几秒确认窗口即可滤掉这类抖动。
        """
        if n_raw >= self._n_stable:                  # 上升（或与已生效值相同）→ 立即
            self._n_stable, self._n_hold_since = n_raw, 0.0
        else:
            if self._n_hold_since <= 0.0:
                self._n_hold_since = now             # 开始计"已经降下来多久"
            if now - self._n_hold_since >= self._fall_debounce_s:
                self._n_stable, self._n_hold_since = n_raw, 0.0
        return self._n_stable

    def _sync_bubble_mode(self):
        """用「运行中会话数」的迁移驱动形态切换（每 tick 一次）。

          prev > 0 → n == 0   一轮对话任务结束        → OK
          n > 0 且当前是 OK                            → 回数据态
                              （新任务在跑却挂"完成"会误导，必须让位）
        首次取样只建基线（prev is None），不据它触发——否则启动瞬间就会误判。
        n 取**防抖后的稳定值**：写字动作与气泡共用它，两者不可能不一致。
        """
        now = time.time()
        n = self._stable_active_n(len(self.active), now)
        prev = self._prev_active_n
        self._prev_active_n = n
        # ★ 写字动作与气泡**共用同一处判据**（同一个 n）—— 见 wb_anim.WritingPhase 模块头。
        #   任务开始 / 进行中 / 完成三者不可能对不上 —— 否则会出现
        #   "气泡说完成了、她还在写"这种自相矛盾的画面。
        moved = self._wr.feed(n, now)
        if moved:
            self._report_event("write_" + moved,
                               detail=f"n={n} act={self._wr.act}")
            self._drawn_sig = None
        if prev is None:
            return
        if prev > 0 and n == 0:
            self._set_bubble_mode(BUBBLE_OK)
        elif n > 0 and self._bub_mode == BUBBLE_OK:
            self._set_bubble_mode(BUBBLE_DEFAULT)

    def _bubble_ok_click(self):
        """OK 态下点击气泡：累计连击 → 点满后短暂保持"完成"再恢复数据态。

        连击窗口：相邻两次点击间隔 > OK_CLICK_WINDOW_S 视为中断，计数从 1 重来。
        """
        now = time.time()
        if self._ok_clicks >= MOTION.OK_CLICKS_NEEDED:
            return                                  # 已点满、正处保持段，忽略多余点击
        if now - self._ok_last_click > MOTION.OK_CLICK_WINDOW_S:
            self._ok_clicks = 0                     # 超时 → 连击中断
        self._ok_clicks += 1
        self._ok_last_click = now
        self._ok_pulse_t0 = now
        self._play("pop")
        self._report_event("bubble_ok_click",
                           detail=f"{self._ok_clicks}/{MOTION.OK_CLICKS_NEEDED}")
        if self._ok_clicks >= MOTION.OK_CLICKS_NEEDED:
            # 不立刻切换：留一小段保持，让第 3 颗进度点点亮被看见，切换才不突兀
            self._ok_release_at = now + MOTION.OK_RELEASE_HOLD_S
        self._drawn_sig = None

    def _ok_animating(self, now):
        """是否还有 OK 相关的动画在跑（切换动画 / 点击脉冲 / 完成保持）。"""
        return (now < self._ok_anim_t0 + self._ok_anim_dur
                or now < self._ok_pulse_t0 + MOTION.OK_TAP_PULSE_S
                or now < self._ok_release_at)

    def _ok_visual(self, now):
        """返回 (字形缩放, 描边→橙插值 t01, 是否仍在切换动画中)。

        进场：0.55 → 1.0（ease_out_back 过冲，钳到 SCALE_MAX 守住克制上限）
        退场：1.0 → 0（ease_out_cubic 收小），橙度同步 1 → 0
        点击脉冲另行叠乘，不并入这里，免得两套动画互相干扰。
        """
        if self._ok_anim_dur and now < self._ok_anim_t0 + self._ok_anim_dur:
            t = MOTION.clamp01((now - self._ok_anim_t0) / self._ok_anim_dur)
            if self._bub_mode == BUBBLE_OK:
                gs = self._ok_anim_from \
                    + (1.0 - self._ok_anim_from) * MOTION.ease_out_back(t)
                return min(gs, MOTION.SCALE_MAX), t, True
            gs = self._ok_anim_from * (1.0 - MOTION.ease_out_cubic(t))
            return max(gs, 0.0), 1.0 - t, True
        if self._bub_mode == BUBBLE_OK:
            return 1.0, 1.0, False
        return 0.0, 0.0, False

    def _ok_pulse(self, now):
        """单次点击的脉冲包络：0 → OK_TAP_BUMP → 0（半个正弦，起止都归零）。"""
        if not self._ok_pulse_t0:
            return 0.0
        t = (now - self._ok_pulse_t0) / MOTION.OK_TAP_PULSE_S
        if t < 0 or t >= 1.0:
            return 0.0
        return MOTION.OK_TAP_BUMP * math.sin(math.pi * t)

    # ---- OK 态自动消失：用户回到对话窗口 + 窗口重新获得焦点 → 平滑淡出 ----
    def _refresh_wb_hwnd(self):
        """枚举顶层可见窗口，把标题含 WB_HOST_TITLE_HINTS 的记为 WorkBuddy 主窗口句柄。

        仅周期性调用（OK_WB_HWND_REFRESH_S），平时走缓存；句柄失效/窗口重建时自动自愈。
        """
        found = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        @WNDENUMPROC
        def cb(hwnd, lparam):
            if not _user32.IsWindowVisible(hwnd) or hwnd == self.hwnd:
                return True
            ln = _user32.GetWindowTextLengthW(hwnd)
            if ln <= 0:
                return True
            buf = ctypes.create_unicode_buffer(ln + 1)
            if _user32.GetWindowTextW(hwnd, buf, ln + 1) <= 0:
                return True
            title = (buf.value or "").lower()
            for hint in WB_HOST_TITLE_HINTS:
                if hint.lower() in title:
                    found.append(hwnd)
                    break
            return True

        try:
            _user32.EnumWindows(cb, 0)
        except Exception:
            pass
        if found:
            self._wb_hwnd = found[0]
        self._wb_hwnd_checked_at = time.time()

    def _is_wb_focused(self, now):
        """当前前台窗口是否对应 WorkBuddy 对话窗口。

        判定优先级：缓存句柄命中 → 前台窗口标题含 hint（句柄缓存失效时兜底）。
        桌宠自身窗口（hwnd）不算——点气泡/右键菜单时宠物短暂前台，不能误判成"已回对话"。
        """
        if (not self._wb_hwnd or not _user32.IsWindow(self._wb_hwnd)
                or now - self._wb_hwnd_checked_at > MOTION.OK_WB_HWND_REFRESH_S):
            self._refresh_wb_hwnd()
        fg = _user32.GetForegroundWindow()
        if not fg or fg == self.hwnd:
            return False
        if self._wb_hwnd and fg == self._wb_hwnd:
            return True
        ln = _user32.GetWindowTextLengthW(fg)
        if ln > 0:
            buf = ctypes.create_unicode_buffer(ln + 1)
            if _user32.GetWindowTextW(fg, buf, ln + 1) > 0:
                title = (buf.value or "").lower()
                for hint in WB_HOST_TITLE_HINTS:
                    if hint.lower() in title:
                        return True
        return False

    def _ok_focus_check(self, now):
        """每帧检测：进入 OK 后，对话窗口重新获得焦点（或本就一直在前台）→ 自动收起。

        触发条件（与需求一致）：
          · 对应对话完成（active: >0 → 0，由 _sync_bubble_mode 进 OK，本函数不重复判）；
          · 用户回到对话窗口（_is_wb_focused 由 False→True 的上升沿）；
          · 对话窗口重新获得焦点（同上上升沿，或"出现时本就在前台"走宽限）。
        最短显示时长保护：弹入瞬间（< OK_AUTODISMISS_FOCUS_MIN_VISIBLE_S）不被误收。
        平滑消失：复用 _set_bubble_mode(DEFAULT) 的 OK_POP_OUT_S 退场动画（orange→blue + 字形收小）。
        """
        if not self.ok_autodismiss_on:
            self._wb_fg_prev = self._is_wb_focused(now)
            return
        fg_now = self._is_wb_focused(now)
        if self._bub_mode == BUBBLE_OK:
            elapsed = now - self._ok_entered_at
            rising = fg_now and not self._wb_fg_prev          # 回到对话窗口 / 重新获得焦点
            never_left = self._ok_fg_at_entry and fg_now       # 出现时就在前台（用户没离开）
            if (elapsed >= MOTION.OK_AUTODISMISS_FOCUS_MIN_VISIBLE_S
                    and (rising or never_left)):
                self._ok_auto_dismiss(now)
        self._wb_fg_prev = fg_now

    def _ok_auto_dismiss(self, now=None):
        """用户已回对话窗口（视为已读）→ OK 泡泡平滑淡出。"""
        if self._bub_mode != BUBBLE_OK:
            return
        now = time.time() if now is None else now
        self._ok_auto_dismissed = True
        # 走默认退出路径：复用 OK_POP_OUT_S 平滑淡出，不播 pop（只是已读，不打扰）
        self._set_bubble_mode(BUBBLE_DEFAULT, now, sound=False)
        self._report_event("ok_auto_dismiss")

    # ---- 联动关闭：WorkBuddy 应用退出 → 桌宠窗口与进程一并退出 ----
    def _wb_pid_from_window(self):
        """从已探测的 WorkBuddy 窗口句柄取宿主 PID。"""
        if not self._wb_hwnd or not _user32.IsWindow(self._wb_hwnd):
            return None
        pid = wt.DWORD(0)
        _user32.GetWindowThreadProcessId(self._wb_hwnd, ctypes.byref(pid))
        return pid.value or None

    def _wb_snapshot_pid(self, name=WB_PROC_NAME):
        """进程快照按 exe 名找 PID。返回 (pid|None, snapshot_ok)。

        snapshot_ok=False 表示快照本身失败（无法判定），与"快照成功但没找到"（确实没运行）区分开。
        """
        class _PE32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260),
            ]
        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            return None, False
        try:
            e = _PE32()
            e.dwSize = ctypes.sizeof(_PE32)
            ok = _kernel32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile and e.szExeFile.lower() == name.lower():
                    return e.th32ProcessID, True
                ok = _kernel32.Process32NextW(snap, ctypes.byref(e))
            return None, True
        finally:
            _kernel32.CloseHandle(snap)

    def _open_wb_process(self):
        """打开 WorkBuddy 进程句柄（SYNCHRONIZE）：优先窗口 PID，退化到 exe 快照 PID。"""
        pid = self._wb_pid_from_window()
        if not pid:
            pid, _ok = self._wb_snapshot_pid(self._wb_proc_name)
        if not pid:
            return None
        h = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return None
        self._wb_pid = pid
        self._wb_proc_handle = h
        return h

    def _host_alive(self):
        """宿主（任一已注册 agent）是否仍活跃：True / False / None(无法判定)。

        v2（2026-09-25 解耦）：联动关闭的宿主从「WorkBuddy 进程」泛化为
        「任一活跃 agent」。判据直接取桌宠内已在跑的活跃探测器缓存
        ——零额外开销，判定口径与 wb_agent_presence 完全一致，不会两边不一。

        未探满一轮时返回 None：否则启动瞬间的空集合会被误判成"全退出了"而误关。
        """
        pres = getattr(self, "_presence", None)
        if pres is None:
            return None
        try:
            if getattr(pres, "_ticks", 0) <= 0:
                return None                      # 还没探过一轮 → 无法判定
            return bool(pres.active())
        except Exception:
            return None

    def _wb_alive(self, now):
        """宿主是否仍存活：True / False / None(无法判定)。

        v2（2026-09-25 解耦）：宿主 = **任一已注册 agent**，不再是 WorkBuddy 专用。
          ① 权威源 = 活跃探测器（声明式探针，新增 agent 自动覆盖）
          ② 兜底   = 原 WorkBuddy 三路探测（探测器不可用时回退，老环境行为不变）

        ① 有进程句柄 → WaitForSingleObject(0) 精确判定（正常退出/崩溃/托盘退出都是进程结束）；
        ② 句柄信号或缺失 → 窗口探测 + 进程快照复核（限频），能拿到新句柄就顺手补上。
        返回 None 仅当"没有可见窗口且进程快照也失败"——此时按存活处理，绝不误关。
        """
        # ① 权威源：活跃 agent 集合（非 None 即采纳，不再看 WorkBuddy）
        host = self._host_alive()
        if host is not None:
            return host

        # ② 兜底：原 WorkBuddy 三路探测
        if self._wb_proc_handle:
            r = _kernel32.WaitForSingleObject(self._wb_proc_handle, 0)
            if r == WAIT_TIMEOUT:
                return True
            # 句柄已信号（进程结束）或异常 → 关句柄，走独立复核（处理"关了又立刻重开"）
            _kernel32.CloseHandle(self._wb_proc_handle)
            self._wb_proc_handle = None
        if self._wb_probe_done and now - self._wb_probe_at < MOTION.LINKED_CLOSE_PROBE_S:
            return self._wb_alive_cache
        self._wb_probe_at = now
        self._wb_probe_done = True
        self._refresh_wb_hwnd()
        win_alive = bool(self._wb_hwnd and _user32.IsWindow(self._wb_hwnd))
        exe_pid, snap_ok = self._wb_snapshot_pid(self._wb_proc_name)
        if win_alive or (snap_ok and exe_pid):
            self._wb_alive_cache = True
            self._open_wb_process()               # 恢复精确等待
            return True
        if not snap_ok:
            return None                           # 无窗口 + 快照失败 → 无法判定
        self._wb_alive_cache = False
        return False

    def _check_linked_close(self, now):
        """每轮 tick：WorkBuddy 进程结束 → 桌宠窗口与进程一并退出。

        触发：连续 LINKED_CLOSE_CONFIRM_CHECKS 次判定"已消失"，且持续 ≥ LINKED_CLOSE_GRACE_S。
        兜底：_wb_alive() 返回 None（无法判定）→ 什么都不做（宁可留着，也不误杀）。
        基线：守望拉起（_linked_expected）或曾见 WorkBuddy 存活，二者之一满足才允许联动关；
              手动直接运行且从未见过 WorkBuddy（如测试）→ 永不自动关。
        """
        if not self.linked_close_on or self._linked_closed:
            return
        alive = self._wb_alive(now)
        if alive is None:
            return
        if alive:
            self._wb_seen_once = True
            self._wb_gone_since = 0.0
            self._wb_gone_checks = 0
            return
        if not (self._wb_seen_once or self._linked_expected):
            return
        if not self._wb_gone_since:
            self._wb_gone_since = now
            # 首次判定"看不见宿主" → 留痕（哪怕宽限期内又恢复，也留证据，便于查误杀）
            self._report_event("host_gone", detail=f"presence={self._host_alive()}")
        self._wb_gone_checks += 1
        if (self._wb_gone_checks >= MOTION.LINKED_CLOSE_CONFIRM_CHECKS
                and now - self._wb_gone_since >= MOTION.LINKED_CLOSE_GRACE_S):
            self._linked_quit()

    def _linked_quit(self):
        """联动关闭：关窗口 → WM_DESTROY → PostQuitMessage → 进程退出。"""
        if self._linked_closed:
            return
        self._linked_closed = True
        self._report_event("linked_close",
                           detail="host_gone %ds checks=%d"
                                  % (int(time.time() - (self._wb_gone_since or time.time())),
                                     self._wb_gone_checks))
        try:
            _user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        except Exception:
            try:
                _user32.PostQuitMessage(0)
            except Exception:
                pass

    # ---- 动作音效（跟着动作走，不是跟着点击走）----

    def _load_anim_sfx(self):
        """读 `assets/anim/<动作>/_sfx.wav`。缺哪个就少哪个，不报错。"""
        out = {}
        if winsound is None:
            return out
        # 注意：写字那段**也有音轨**，别漏（2026-09-28 漏过一次）
        for name in ("idle", "sleepy", "pickup", "write"):
            p = os.path.join(ASSETS_DIR, "anim", name, "_sfx.wav")
            try:
                with open(p, "rb") as f:
                    out[name] = f.read()
            except Exception:
                pass
        return out

    def _play_anim_sfx(self, act, clip_name):
        """动作切换 → 换音效。待机/困了循环播，其余播一遍。

        幂等：同一个 (动作, 素材) 重复调用不会重头开始（否则每帧都会重播）。
        """
        key = (act, clip_name)
        if self._anim_sfx_act == key:
            return
        self._anim_sfx_act = key
        if winsound is None or not self.sound_on:
            return
        data = self._anim_sfx.get(clip_name)
        if not data:
            self._stop_anim_sfx()          # 这个动作没音轨（如变困）→ 保持安静
            self._anim_sfx_act = key       # 但记住它，别每帧重试
            return
        flags = winsound.SND_MEMORY | winsound.SND_ASYNC
        # 循环播的三种：待机 / 困了 / 写字 —— 都是**持续状态**，
        # 播一遍就没声了，必须挂 SND_LOOP。素材与循环动画几乎等长，音画基本同步。
        if act in (ANIM.ACT_IDLE, ANIM.ACT_SLEEPY, ANIM.ACT_WRITE):
            flags |= winsound.SND_LOOP
        try:
            winsound.PlaySound(data, flags)
        except Exception:
            pass

    def _stop_anim_sfx(self):
        """停掉动作音效（切换动作、关掉开关、退出时用）。"""
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        self._anim_sfx_act = None

    # ---- 互动反应（傲娇四档）----
    def _play(self, key):
        if not (self.sound_on and key in self._sounds):
            return
        try:
            winsound.PlaySound(self._sounds[key],
                               winsound.SND_MEMORY | winsound.SND_ASYNC)
            # winsound 一次只播一个：互动音效把动作音效顶掉了，0.6s 后接回来
            self._sfx_resume_at = time.time() + 0.6
        except Exception:
            pass


    def _spawn_particles(self, kind, count, cx, cy):
        for _ in range(count):
            if kind == "heart":
                vx, vy = random.uniform(-45, 45), random.uniform(-120, -60)
                size, life = random.uniform(4, 7) * self.scale, random.uniform(0.8, 1.2)
            elif kind == "spark":
                # OK 态弹入的迸发小点：上抛更冲、寿命更短，像"啪"地一下
                vx, vy = random.uniform(-115, 115), random.uniform(-195, -95)
                size, life = random.uniform(2.2, 4.2) * self.scale, random.uniform(0.5, 0.9)
            else:
                vx, vy = random.uniform(-75, 75), random.uniform(-260, -170)
                size, life = random.uniform(2.5, 4.5) * self.scale, random.uniform(0.7, 1.0)
            self._particles.append({
                "x": cx + random.uniform(-14, 14), "y": cy,
                "vx": vx, "vy": vy, "life": life, "max": life,
                "kind": kind, "size": size,
                "phase": random.uniform(0, math.pi * 2)})
        # 提案 §5 克制：粒子并发上限 12（原 24），超出时丢弃最老的一批
        cap = MOTION.MAX_PARTICLE_CONCURRENCY
        if len(self._particles) > cap:
            self._particles = self._particles[-cap:]

    # ---- 生命力：动画帧驱动（常驻 66ms）----
    def _anim_tick(self):
        """逐帧动效。**菜单弹出期间照常跑**（2026-09-29 改）。

        原来这里的 `if self._menu_open: return` 是 owner-draw 时代的产物 —— 那时菜单
        是系统 `TrackPopupMenu`（同线程 + 系统擦除），重绘会和它抢 UI 线程。
        现在菜单是**独立的自绘分层窗口**（各自 Surface / 各自 ULW），谁也干扰不到谁，
        所以闸门只剩一个副作用：**菜单一开人物就冻住**（用户报障原话）。
        唯一要防的是「桌宠把自己重新置顶、把菜单压下去」→ 见 `_move_window`。
        """
        now = time.time()
        self._anim_last_tick = now        # 高精度时钟看门狗用（见 tick）
        # ⓪ 拖拽卡死兜底（见 _clear_stuck_drag）
        self._clear_stuck_drag()
        # ⓪.5 后台重建好的帧缓存该换上了（改「桌宠大小」后不阻塞 UI）
        self._wait_cache_new()
        dt = ANIM_MS / 1000.0
        # ① 粒子物理
        alive = []
        for p in self._particles:
            p["life"] -= dt
            if p["life"] <= 0:
                continue
            if p["kind"] == "heart":
                p["vy"] += 60 * dt               # 爱心轻飘
            else:
                p["vy"] += 520 * dt              # 水滴重力
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            alive.append(p)
        self._particles = alive
        # ② 窗口漂移插值（随机游动）
        if self._drift and not self._dragging:
            d = self._drift
            t = min(1.0, (now - d["t0"]) / d["dur"])
            e = t * t * (3 - 2 * t)              # smoothstep 缓动
            nx = int(d["x0"] + (d["x1"] - d["x0"]) * e)
            ny = int(d["y0"] + (d["y1"] - d["y0"]) * e)
            self._move_window(self.hwnd, nx, ny)
            if t >= 1.0:
                ret = d.get("ret", False)
                self._drift = None
                if not ret:                      # outward 完成 → 稍后漂回家
                    self._drift_back_at = now + random.uniform(2.0, 4.0)
        elif self._drift_back_at and now >= self._drift_back_at and not self._dragging:
            self._drift_back_at = 0.0
            if self._home:                       # 漂回家（仅当离家还有点距离）
                x, y = self._window_xy(self.hwnd)
                if abs(x - self._home[0]) + abs(y - self._home[1]) > 6:
                    self._start_drift(self._home[0] - x, self._home[1] - y,
                                      dur=1.8, ret=True)
        # ③ 启动打招呼（一次性）
        if self._greet_at and now >= self._greet_at:
            self._greet_at = 0.0
            self._greet()
        # ③.5 OK 态"完成"保持结束 → 复位气泡形态（点满后走这条）
        if self._ok_release_at and now >= self._ok_release_at:
            self._ok_release_at = 0.0
            self._set_bubble_mode(BUBBLE_DEFAULT, now)
        # ③.6 OK 态自动消失：用户回到对话窗口 + 窗口重新获得焦点 → 平滑淡出
        self._ok_focus_check(now)
        # ③.7 跟随模式：前台 → agent（钩子即时/轮询兜底 → 解析 → 去抖 → 徽章）
        self._follow_tick(now)
        # ④ 空闲自主行为调度（用户交互/反应期间暂停）
        if (now - self._last_interact > IDLE_AFTER
                and now >= self._next_behavior
                and not self._dragging):
            self._behavior()
        # ⑤ 视线 / 悬停：由光标位置推导（提案 §3）
        self._update_pointer_state()
        # ⑤ 提案 §1 待机三件套 + §3 微交互 + §2 情绪：统一计算本帧的位移量
        self._update_motion(now)
        # ⑥ 动作流转：一次性动作播完自动接下一段；待机久了开始犯困
        self._wr.note_activity(self._last_interact)   # 用户多久没搭理（拖拽/点击都会刷新）
        moved = self._wr.tick(now)
        if moved:
            self._drawn_sig = None
        # 互动音效早该播完了 → 把动作音效接回来（否则点一下之后就永远静音）
        if self._sfx_resume_at and now >= self._sfx_resume_at:
            self._sfx_resume_at = 0.0
            self._anim_sfx_act = None
            self._play_anim_sfx(self._wr.act, self._wr.clip_name(self._clips))
        # ⑥ 脏检测：把全部动效量化后拼成签名，只有签名变化才重绘（省电关键）
        sig = self._sig()
        msig = self._motion_signature(now)
        dirty = (bool(self._particles) or self._drift is not None
                 or now < self._wobble_until
                 or self._ok_animating(now)          # 气泡形态切换 / 点击脉冲 / 完成保持
                 or self._anim_play_now()         # 帧序列在播 → 逐帧重绘（常态恒为真）
                 or msig != self._motion_sig
                 or sig != self._drawn_sig)
        if dirty:
            self._motion_sig = msig
            self.draw()
            self._drawn_sig = sig
            # 提案 §6：单帧绘制过慢连续 N 帧 → 自动降一档
            self._auto_degrade(now)

    # ---- 提案 §3：点击身体部位切形态 ----



    # ---- 提案 §1：待机动效与微交互的逐帧计算 ----
    def _body_tap_feedback(self, lay):
        """点身体的轻量反馈（2026-09-28 起**不再切形态**）：晃一下 + 冒颗爱心 + 音效。

        单击与双击身体**共用这一处** —— 以前两处各自调 `_react()`，那套「按点击频率
        分档切形态」已随 v3 立绘删除。
        """
        fb = getattr(MOTION, "CLICK_FEEDBACK_S", 1.2)
        self._wobble_until = time.time() + fb
        self._wobble_amp = 3.0 * self.scale
        self._wobble_dur = fb
        self._spawn_particles("heart", 1, lay["W"] / 2,
                              self._anim_content_top(lay) - 6 * lay["sc"])
        self._play("chirp")
        self._drawn_sig = None

    def _long_press_release(self, lay):
        """提案 §3 长按：憋了半天 → 松手时噗地喷几滴水花。"""
        cx = lay["W"] / 2
        head_y = lay["bubble_h"] + lay["pet_h"] * 0.16
        self._spawn_particles("drop", 3, cx, head_y)
        self._quote = random.choice(QUOTES_TSUNDERE)
        self._quote_dur = "长按"
        self._talk_until = time.time() + 2.0
        self._wobble_until = time.time() + MOTION.LONGPRESS_RELEASE_S
        self._wobble_amp = 5.0 * self.scale
        self._wobble_dur = MOTION.LONGPRESS_RELEASE_S
        self._play("splash")
        self._arm_watch()
        self._report_event("long_press")
        self._drawn_sig = None

    def _gaze_box_center(self, wx, wy, lay):
        """判定矩形中心（屏幕坐标）。

        中心取**立绘实际矩形**中心：窗口上部是气泡区，用窗口中心会让判定范围整体偏上。
        立绘矩形还没记录（首帧前）就退回窗口中心。
        """
        spr = getattr(self, "_spr_rect", None)
        if spr and spr[2] > 0 and spr[3] > 0:
            return wx + spr[0] + spr[2] / 2.0, wy + spr[1] + spr[3] / 2.0
        return wx + lay["W"] / 2.0, wy + lay["H"] / 2.0

    def _cursor_in_gaze_box(self, pt, wx, wy, lay):
        """光标是否落在判定矩形内（固定 MOTION.GAZE_RECT_W×H 屏幕像素，**不随 scale 缩放**）。

        「朝向鼠标」（左右翻转）与「视线微移」**共用这一个范围** —— 用户要求的语义就是
        「鼠标在范围内才跟随」。
        """
        cx, cy = self._gaze_box_center(wx, wy, lay)
        half_w = MOTION.GAZE_RECT_W / 2.0
        half_h = MOTION.GAZE_RECT_H / 2.0
        return (cx - half_w) <= pt.x <= (cx + half_w) \
            and (cy - half_h) <= pt.y <= (cy + half_h)

    def _update_pointer_state(self):
        """光标位置 → 悬停态 + 视线微移（提案 §3）。

        放在动画帧里统一算（而不是 WM_MOUSEMOVE），分层窗口不依赖鼠标离开消息也能正确复位。

        视线判定范围：**固定 MOTION.GAZE_RECT_W×H（443×465）屏幕像素**的矩形，
        见 `_cursor_in_gaze_box`；光标进入才触发微移，不再要求光标压在桌宠自身窗口内。
        ⚠️ 不乘 scale —— 用户要的就是这个绝对尺寸。
        悬停态（hover 放大/眨眼关注）仍保持只在桌宠窗口内触发，避免光标一过附近就放大。
        """
        hover = False
        gaze_inside, dx_ratio = False, 0.0
        try:
            pt = POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            x, y = self._window_xy(self.hwnd)
            lay = self._layout()
            # 悬停：只在桌宠自身窗口内
            if x <= pt.x <= x + lay["W"] and y <= pt.y <= y + lay["H"]:
                hover = True
            # 视线：固定 443×465 判定矩形（与朝向翻转共用）
            if self._cursor_in_gaze_box(pt, x, y, lay):
                gaze_inside = True
                cx, _ = self._gaze_box_center(x, y, lay)
                dx_ratio = (pt.x - cx) / (MOTION.GAZE_RECT_W / 2.0)
        except Exception:
            pass
        self._hovering = hover
        if gaze_inside:
            self._gaze_target = max(-MOTION.GAZE_MAX_PX, min(
                MOTION.GAZE_MAX_PX, dx_ratio * MOTION.GAZE_MAX_PX)) * self.scale
        else:
            self._gaze_target = 0.0

    def _update_motion(self, now):
        """计算本帧所有位移量（呼吸 / 尾鳍 / 漂浮 / 眨眼 / 视线 / hover / 按压 / 情绪）。

        所有幅度都乘 scale 并受「克制上限」裁剪，保证大尺寸下也不会夸张。
        """
        sc = self.scale
        idle_long = (now - self._last_interact) > MOTION.IDLE_DOWNGRADE_S
        q = self._quality

        # ① 呼吸（非对称：吸 1.4s / 呼 1.8s）—— 任何档位都保留，是最基础的生命感
        b = MOTION.breath_offset(now, self._bob_phase) * sc
        self._breath = b if q != MOTION.QUALITY_OFF else 0.0

        # ② 尾鳍摆动（与呼吸周期 1:2 错开）
        if MOTION.allow_tail_sway(q) and not idle_long:
            self._tail_dx = MOTION.tail_offset(now, self._phase_tail) * sc
        else:
            self._tail_dx = 0.0

        # ③ 上下漂浮 + 阴影缩放（仅完整档；空闲久了也停）
        if MOTION.allow_float(q) and not idle_long \
                and not MOTION.EMOTION_SUPPRESS_LOOP.get(self._emotion, False):
            self._float_dy = MOTION.float_offset(now, self._phase_float) * sc
            self._shadow_scale = MOTION.shadow_scale(now, self._phase_float)
        else:
            self._float_dy = 0.0
            self._shadow_scale = 1.0

        # ④ 原「眨眼状态机」随 v3 立绘一并删除 —— 写字帧序列里她本来就在眨，
        #    不需要再用贴片去驱动一层眼皮。

        # ⑤ 视线：头部朝光标方向轻微偏移（±1.5px，lerp 平滑）
        if q == MOTION.QUALITY_OFF:
            self._gaze_target = 0.0
        self._gaze_dx += (self._gaze_target - self._gaze_dx) * MOTION.GAZE_LERP

        # ⑥ 悬停淡入淡出（ease-out-cubic 进出）
        target_t = 1.0 if (self._hovering and q != MOTION.QUALITY_OFF) else 0.0
        step = dt_hover = (ANIM_MS / 1000.0) / (MOTION.HOVER_ENTER_S if target_t
                                                else MOTION.HOVER_EXIT_S)
        if target_t > self._hover_t:
            self._hover_t = min(target_t, self._hover_t + step)
        else:
            self._hover_t = max(target_t, self._hover_t - step)

        # ⑦ 点击压缩回弹：squash(1.06×0.92) → back-out 回弹 → 归位
        self._squash = self._squash_curve(now)

        # ⑧ 拖拽倾斜：松手后 4 帧内回正
        if not self._dragging:
            self._drag_tilt *= 0.75
            if abs(self._drag_tilt) < 0.1:
                self._drag_tilt = 0.0

        # ⑨ 情绪状态到期自动回落待机
        if self._emotion != MOTION.EMOTION_NEUTRAL and self._emotion_until \
                and now > self._emotion_until:
            self._emotion = MOTION.EMOTION_NEUTRAL
            self._emotion_until = 0.0


    # ---- 跟随模式（设计文档 P1）：前台 → agent，去抖 + 未知态 ----
    def _today_timeline(self):
        """P2 连续锚点：今天跨 agent 轮次统计 + 最近轮次。

        ⚠️ **绝不在 UI 线程查库**（2026-09-26 性能修复）。
        原来这里是 10s 缓存 + 过期就同步查；但 `today_timeline()` 打在一个**视图**
        `v_turn_total` 上（视图无法建索引 → 跨 1GB 基础表扫描），实测**单次 3.4~3.7 秒**。
        而这个函数被两处 UI 线程调用：
          · 右键菜单构建（`context_menu`）
          · 气泡文案构建（row3 副标）
        后果：右键要等 3.6 秒才弹菜单；气泡刷新时只要缓存过期同样冻 3.6 秒
        —— 用户反馈的"右键卡顿"与"平时卡"就是这个。

        现在的策略：**只读缓存，过期就丢给后台线程去刷**，本函数立即返回。
        代价是数据最多晚一轮（第一次打开可能显示"统计中"），换来 UI 永不卡。
        """
        now = time.time()
        cached = self._timeline_cache
        if not cached or now - cached[0] > 10.0:
            self._kick_timeline_refresh()        # 后台去查（非阻塞）
        return cached[1] if cached else ([], [])

    def _kick_timeline_refresh(self):
        """在后台线程刷新时间线缓存（同一时刻只跑一个）。"""
        if self._timeline_busy:
            return
        self._timeline_busy = True

        def work():
            try:
                data = today_timeline(self.db_path)
                self._timeline_cache = (time.time(), data)
            except Exception:
                pass
            finally:
                self._timeline_busy = False

        threading.Thread(target=work, daemon=True, name="timeline-refresh").start()

    def _install_follow_hook(self):
        """前台切换事件钩子（设计 §2.2 首选）。装不上 → 动画帧轮询兜底。"""
        try:
            self._follow_winproc = WINEVENTPROC(self._on_fg_event)
            hook = SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None,
                self._follow_winproc, 0, 0, WINEVENT_OUTOFCONTEXT)
            self._follow_hook = hook or None
        except Exception:
            self._follow_hook = None
        return self._follow_hook

    def _on_fg_event(self, _hook, _event, hwnd, id_object, _id_child,
                     _thread, _time):
        """WinEvent 回调：⚠️ 只记 hwnd（设计 §2.2 血泪教训——回调做重活卡消息循环）。
        id_object==0（OBJID_WINDOW）才计：菜单/滚动条等子对象事件不算前台切换。"""
        if id_object == 0 and hwnd:
            self._follow_hwnd = hwnd
            self._follow_last_seq += 1

    def _fg_window_info(self, hwnd):
        """hwnd → (进程名小写, 窗口标题)。任一拿不到 → (None, None)（按未知态处理）。"""
        try:
            pid = wt.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc = None
            if pid.value:
                h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid.value)
                if h:
                    try:
                        n = wt.DWORD(520)
                        buf = ctypes.create_unicode_buffer(260)
                        if _kernel32.QueryFullProcessImageNameW(
                                h, 0, buf, ctypes.byref(n)):
                            proc = os.path.basename(buf.value).lower()
                    finally:
                        _kernel32.CloseHandle(h)
            ln = _user32.GetWindowTextLengthW(hwnd)
            title = ""
            if ln > 0:
                tbuf = ctypes.create_unicode_buffer(ln + 1)
                if _user32.GetWindowTextW(hwnd, tbuf, ln + 1) > 0:
                    title = tbuf.value or ""
            return proc, title
        except Exception:
            return None, None

    def _follow_hint_map(self, now):
        """登记册 → 前台匹配用名片（procs + title_hints），5s 缓存（开关即时生效）。"""
        if self._follow_hint_cache and now - self._follow_hint_cache[0] < 5.0:
            return self._follow_hint_cache[1]
        try:
            m = FOLLOW.hint_map_from_registry(REG.load()["agents"])
        except Exception:
            m = {}
        self._follow_hint_cache = (now, m)
        return m

    def _follow_tick(self, now):
        """动画帧消费（66ms）：拿前台 hwnd（钩子即时 / 逐帧轮询兜底）→
        解析 → 去抖 → 提交。GetForegroundWindow 与查进程名都极廉价（设计 §2.2）。
        P3：pin 锁定期间跟随不覆盖（手动聚焦走「手动聚焦」子菜单）。"""
        if not self.follow_on:
            if self._follow_focus:
                self._follow_focus = None
                self._drawn_sig = None
            return
        if self.focus_pin:
            return                      # 锁定（pin）：当前聚焦不被跟随覆盖
        self._follow_poll_n += 1
        hwnd = None
        if self._follow_last_seq != self._follow_seen_seq:
            self._follow_seen_seq = self._follow_last_seq
            hwnd = self._follow_hwnd            # 钩子事件待消费
        elif (self._follow_poll_n % MOTION.FOLLOW_POLL_EVERY == 0
                or self._follow_hook is None):
            hwnd = _user32.GetForegroundWindow()   # 轮询兜底（含钩子静默失效）
        if not hwnd:
            return
        proc, title = self._fg_window_info(hwnd)
        key = FOLLOW.resolve(self._follow_hint_map(now), proc, title)
        ev = self._follow_tracker.update(key, now)
        if ev:
            self._follow_commit(ev, now)

    def _follow_commit(self, ev, now):
        """确认一次身份切换：更新聚焦徽章状态 + 埋点（follow_change/return/unknown）。
        P4：切离某 agent 时检测"似乎未结束"（最近一轮 < 2min）→ 接续角标；
        回到该 agent → 角标消失。全程无主动打扰。"""
        key = ev.get("key")
        accent = None
        if key:
            try:
                spec = REG.load()["agents"].get(key) or {}
                accent = spec.get("accent") or None
            except Exception:
                accent = None
        self._follow_focus = {"key": key, "accent": accent,
                              "kind": ev["event"], "t0": now}
        self._report_event("follow_" + ev["event"],
                           detail=f"{ev.get('from')}->{key}")
        self._drawn_sig = None
        # ---- P4 接续角标 ----
        prev = ev.get("from")
        if prev:
            cand = FOLLOW.handoff_last_turn(self.db_path, prev, now)
            if cand:
                _last, title = cand
                if not (self._handoff and self._handoff.get("key") == prev):
                    self._report_event("handoff_badge_shown", detail=prev)
                self._handoff = {"key": prev, "title": title, "since": now}
                self._drawn_sig = None
            elif self._handoff and self._handoff.get("key") == prev:
                self._handoff = None          # 该 agent 已无未结束会话
                self._drawn_sig = None
        if key and self._handoff and self._handoff.get("key") == key:
            self._handoff = None              # 用户回到该 agent → 角标消失
            self._drawn_sig = None

    def _draw_follow_badge(self, s, x, pw, ph_draw, y_bottom, now):
        """领结位的身份色徽章（§3.1：跟随只换"身份"，轻量元素）。
        未知态：灰 + 半透明（§7#9 诚实 > 好看）。"""
        if not self.follow_on or not getattr(MOTION, "FOLLOW_BADGE_ON", True):
            return          # 徽章默认关闭（用户不想在角色旁边看到那个小圆点）
        f = self._follow_focus
        if f and f.get("key"):
            rgb = 0x7A86C9
            try:
                rgb = int(str(f.get("accent") or "7A86C9").lstrip("#"), 16) & 0xFFFFFF
            except Exception:
                pass
            alpha = MOTION.FOLLOW_BADGE_ALPHA
            u = MOTION.clamp01((now - f.get("t0", 0.0)) / MOTION.FOLLOW_FADE_S)
            pop = 1.0 + (0.35 if f.get("kind") == "change" else 0.0) \
                * (1.0 - MOTION.ease_out_cubic(u))
        else:
            rgb, alpha, pop = 0x8A93A8, MOTION.FOLLOW_UNKNOWN_ALPHA, 1.0
        sc = self.scale
        r = MOTION.FOLLOW_BADGE_R_PX * sc * max(0.6, pop)
        cx = x + pw - 12 * sc
        cy = y_bottom - ph_draw * 0.42          # 领结高度（Q 版立绘的领结位）
        argb = (int(alpha * 255) << 24) | rgb
        s.ellipse(argb, cx - r, cy - r, r * 2, r * 2,
                  line_argb=(0xE0 << 24) | 0x39406B, line_w=1)
        # P4 接续角标：刚离开的 agent "似乎未结束" → 樱花色小点（右上角）
        h = self._handoff
        if h and now - h.get("since", 0) <= MOTION.HANDOFF_SHOW_MAX_S:
            dr = MOTION.HANDOFF_DOT_R_PX * sc
            dx, dy = cx + r * 0.82, cy - r * 0.82
            s.ellipse((0xF2 << 24) | (MOTION.COLOR_HEART & 0xFFFFFF),
                      dx - dr, dy - dr, dr * 2, dr * 2)

    def _squash_curve(self, now):
        """按压 → 回弹过冲 → 归位（提案 §3）：返回纵向缩放系数。"""
        if not self._squash_t0:
            return 1.0
        t = now - self._squash_t0
        if t < MOTION.CLICK_SQUASH_S:               # 压扁段
            u = t / MOTION.CLICK_SQUASH_S
            return 1.0 - (1.0 - MOTION.CLICK_SQUASH_Y) * MOTION.ease_out_cubic(u)
        t -= MOTION.CLICK_SQUASH_S
        if t < MOTION.CLICK_RECOVER_S:              # 回弹段（轻微过冲后归位）
            u = t / MOTION.CLICK_RECOVER_S
            return MOTION.CLICK_SQUASH_Y + (1.0 - MOTION.CLICK_SQUASH_Y) \
                * MOTION.ease_out_back(u, 1.7)
        self._squash_t0 = 0.0
        return 1.0

    def _motion_signature(self, now):
        """把本帧所有动效量化成可比较的元组（脏检测用）。"""
        return (
            int(round(self._breath)),
            int(round(self._float_dy)),
            int(round(self._tail_dx * 2)),
            int(round(self._gaze_dx * 2)),
            int(round(self._hover_t * 12)),
            int(round(self._squash * 100)),
            int(round(self._drag_tilt)),
            self._emotion,
        )

    def _auto_degrade(self, now):
        """提案 §6：单帧绘制 > 8ms 连续 3 帧 → 自动降档；30s 后尝试恢复一档。"""
        if self._frame_ms <= 0:
            return
        if self._frame_ms > MOTION.QUALITY_AUTO_DEGRADE_MS:
            self._slow_frames += 1
        else:
            self._slow_frames = 0
        if self._slow_frames >= MOTION.QUALITY_REPEAT_DEGRADE_FRAMES:
            self._slow_frames = 0
            if self._quality == MOTION.QUALITY_FULL:
                self.set_quality(MOTION.QUALITY_LITE)
                self._report_event("auto_degrade", detail="full→lite")
            elif self._quality == MOTION.QUALITY_LITE:
                self.set_quality(MOTION.QUALITY_OFF)
                self._report_event("auto_degrade", detail="lite→off")
            self._degraded_at = now
        # 降级后一段时间且帧耗时恢复正常 → 回升一档
        if self._degraded_at and now - self._degraded_at > MOTION.QUALITY_AUTO_RECOVERY_S \
                and self._frame_ms < MOTION.QUALITY_AUTO_DEGRADE_MS / 2:
            self._degraded_at = 0.0
            if self._quality == MOTION.QUALITY_OFF:
                self.set_quality(MOTION.QUALITY_LITE)
            elif self._quality == MOTION.QUALITY_LITE:
                self.set_quality(MOTION.QUALITY_FULL)

    # ---- 提案 §2：情绪状态 ----
    def set_emotion(self, name, duration=None):
        """切换情绪（成功 / 失败 / 加载中 / 空数据 / 欢迎）。带节流与自动回落。"""
        now = time.time()
        if name == self._emotion and self._emotion_until > now:
            return                                   # 同状态节流（提案 §5）
        dur = duration if duration is not None else MOTION.EMOTION_DURATION.get(name, 0.0)
        self._emotion = name
        self._emotion_until = (now + dur) if dur else 0.0
        self._drawn_sig = None
        self._motion_sig = None
        self._report_event("emotion", detail=name)
        if name == MOTION.EMOTION_SUCCESS:
            lay = self._layout()
            self._spawn_particles("heart", 3, lay["W"] / 2,
                                  lay["bubble_h"] + lay["pet_h"] * 0.3)
            self._play("chirp")


    def _sync_data_emotion(self):
        """提案 §2：根据数据状态自动同步情绪（服务异常→委屈 / 空数据→空态）。

        只在 idle/empty/fail 之间切换（不覆盖成功/欢迎等高优先级主动情绪），
        且仅在目标状态变化时动作一次，避免每次数据 tick 都重复触发。
        """
        if self._emotion not in (MOTION.EMOTION_NEUTRAL, MOTION.EMOTION_EMPTY,
                                 MOTION.EMOTION_FAIL):
            return
        if not self.db_ok or not self.api_ok:
            target = MOTION.EMOTION_FAIL          # 服务异常 → 委屈（不是怒容）
        elif not self.active:
            target = MOTION.EMOTION_EMPTY          # 今天还没对话 → 空态
        else:
            target = MOTION.EMOTION_NEUTRAL        # 一切正常 → 待机
        if target == self._emotion:
            return
        self._emotion = target
        self._emotion_until = 0.0                  # 持续态（直到数据变化）
        self._drawn_sig = None
        self._motion_sig = None
        self._report_event("emotion", detail=target)

    # ---- 主动互动与自主玩耍 ----
    def _greet(self):
        """启动打招呼：按时段问候 + 开心脸 + 爱心 + 摇摆。"""
        if not self.bubble_on:
            return
        hour = int(time.strftime("%H"))
        hello = "早上好" if hour < 11 else ("下午好" if hour < 18 else "晚上好")
        self._quote = f"（在笔记本上写：{hello}，今天也一起加油吧）"
        self._quote_dur = "打招呼"
        self._talk_until = time.time() + 4.0
        self._wobble_until = time.time() + 0.6
        self._wobble_amp = 4.0 * self.scale
        self._wobble_dur = 0.6
        lay = self._layout()
        self._spawn_particles("heart", 3, lay["W"] / 2,
                              lay["bubble_h"] + lay["pet_h"] * 0.3)
        self._play("chirp")
        self._arm_watch()
        self._report_event("greet")
        self._drawn_sig = None

    def _behavior(self):
        """空闲自主玩耍：随机游动 / 翻身 / 换表情 / 扭扭。

        ⚠️ 2026-09-27：原「吐蓝色水泡泡」分支已按用户要求**整体删除**
        （含 `_spawn_particles("bubble", ...)` 与配套台词）。若要恢复，见 git 历史。
        """
        now = time.time()
        self._next_behavior = now + random.uniform(BEHAVIOR_MIN, BEHAVIOR_MAX)
        choice = random.choice(["swim", "flip", "emote", "wiggle"])
        self._report_event("behavior", detail=choice)
        if choice == "swim":                     # 随机游动（窗口小幅漂移，稍后漂回）
            dx = random.choice([-1, 1]) * random.uniform(20, 50)
            dy = random.uniform(-24, 24)
            self._start_drift(dx, dy, dur=random.uniform(1.4, 2.0))
        else:                                    # 扭扭身子
            self._wobble_until = now + 0.6
            self._wobble_amp = 3.5 * self.scale
            self._wobble_dur = 0.6
        self._drawn_sig = None

    def _start_drift(self, dx, dy, dur=1.6, ret=False):
        """窗口漂移动画：从当前位置平滑移到偏移目标（钳制在家 ±60px 与屏幕内）。"""
        lay = self._layout()
        x, y = self._window_xy(self.hwnd)
        ax, ay, aw, ah = work_area()
        tx, ty = x + dx, y + dy
        if self._home:
            tx = max(self._home[0] - 60, min(tx, self._home[0] + 60))
            ty = max(self._home[1] - 60, min(ty, self._home[1] + 60))
        tx = max(ax + 4, min(tx, ax + aw - lay["W"] - 4))
        ty = max(ay + 4, min(ty, ay + ah - lay["H"] - 4))
        if abs(tx - x) + abs(ty - y) < 4:
            return
        self._drift = {"x0": x, "y0": y, "x1": tx, "y1": ty,
                       "t0": time.time(), "dur": dur, "ret": ret}


    # ---- 绘制 ----
    def draw(self):
        s = self.surf
        s.clear()
        lay = self._layout()
        now = time.time()
        if self.bubble_on:
            self._draw_bubble(s, lay, now)
        self._draw_pet(s, lay)
        s.present(self.hwnd)


    def _anim_content_top(self, lay):
        """写字帧**实际内容顶**的屏幕 y（拿不到就退回「气泡区下沿」）。

        为什么还是动态取：帧上方也有透明留白（content_box y0 = 0.357），
        按画布矩形算会让气泡离脑袋空出一大截。`_draw_bubble` 比 `_draw_pet` 先跑，
        所以这里读到的是**上一帧**的矩形（1 帧 66ms，肉眼无差别）。
        """
        spr = getattr(self, "_spr_rect", None)
        cbox = (getattr(self, "_spr_cbox", {}) or {}).get("__anim__")
        if spr and cbox:
            _x, y, _w, h = spr
            return y + cbox[1] * h
        return lay["bubble_h"]

    def _bubble_box(self, lay):
        """主气泡椭圆的盒 (x, y, w, h)。

        y 锚在「帧内容顶上方 BUBBLE_CHAIN_PX」—— **不再钉死在窗口顶部**。
        顶出窗口上沿时钳到 2px。**唯一的调参旋钮 = BUBBLE_CHAIN_PX**。
        """
        sc = lay["sc"]
        bh = lay["bub_h"]
        by = self._anim_content_top(lay) - BUBBLE_CHAIN_PX * sc - bh
        return 6 * sc, max(2 * sc, by), lay["W"] - 12 * sc, bh

    def _static_content_top(self, lay):
        """**不开帧**也算得出的「人物可见内容顶」（拖动上界用；没素材 → None）。

        与 `_draw_anim_frame` 的矩形算法同源，只忽略 hover / squash / 呼吸
        （几 px 级动态项）—— 这样启动首帧（`_spr_rect` 还是 None）也算得准，
        用户上次把桌宠停在贴屏顶部的位置能原样恢复。
        """
        clips = getattr(self, "_clips", None) or {}
        name = (getattr(self, "_anim_key", None) or (None, None))[1]
        clip = clips.get(name) or next(iter(clips.values()), None)
        if clip is None:
            return None
        sc = lay["sc"]
        ph_draw = (lay["pet_h"] - 8 * sc) * clip.head_scale
        return lay["H"] - 4 * sc - ph_draw + clip.content_box[1] * ph_draw

    def _dead_top(self, lay):
        """窗口上沿 → 「可见内容最上沿」的距离（这一段全是透明留白）。

        气泡开着算气泡上沿（气泡也是可见内容，不能让它被推出屏幕），
        关气泡才算到人物头顶。拖动上界用它把这段留白"还"给用户。
        """
        if self.bubble_on:
            return self._bubble_box(lay)[1]
        top = self._static_content_top(lay)
        return self._anim_content_top(lay) if top is None else top

    def _in_bubble(self, lay, py):
        """纵向坐标是否落在**主气泡椭圆**上（双击开看板 / 单击切台词的分区判定）。

        不能再用旧的 `0 <= py <= lay["bubble_h"]`：气泡改为锚定头顶后，
        它的下沿会落进"立绘区"上半段，旧判据会漏判（点气泡下半截变成戳身体）。
        """
        if not self.bubble_on:
            return False
        _, by, _, bh = self._bubble_box(lay)
        return by <= py <= by + bh

    def _draw_thought_circles(self, s, lay, line_argb):
        """想法小圆：沿「主气泡底部 → 立绘头顶」由大到小排列。

        漫画思考泡惯例：圆从大到小指向源头，给眼睛一条"从头顶冒出"的引导线。
        数据态与 OK 态共用同一套几何，只换描边色 —— 形态切换时小圆不跳位。

        终点锚在**写字帧的实际内容顶**（见 `_anim_content_top`），
        并让颗数随跨度自适应：**首颗贴住气泡下沿、末颗贴住头顶**，
        跨度大就按 ~30px 步距多排几颗 —— 不再出现"小圆飘在半空、离头一大截"。
        """
        sc = lay["sc"]
        W = lay["W"]
        bx, by, bw, bh = self._bubble_box(lay)      # 气泡锚定在头顶上方（见 _bubble_box）
        p0x = W / 2 - 22 * sc                       # 起点：主气泡底偏左
        p0y = by + bh + 4 * sc                      # 距主气泡下沿 4px
        p1x = W / 2 - 4 * sc                        # 终点：头顶偏中
        p1y = self._anim_content_top(lay) - 4 * sc      # 帧内容顶上方 4px

        r_big, r_sml = 8.0 * sc, 3.8 * sc           # 大→小：近气泡的大、近头顶的小
        y0 = p0y + r_big                            # 首颗圆心：贴着气泡下沿
        y1 = p1y - r_sml                            # 末颗圆心：贴着头发顶
        if y1 - y0 < 8 * sc:                        # 极端贴近时留一点间距，防两颗重合
            y1 = y0 + 8 * sc
        usable = y1 - y0
        n = 2 if usable < 34 * sc else min(6, int(round(usable / (30.0 * sc))) + 1)
        span = max(1.0, p1y - p0y)
        for i in range(n):
            t = i / (n - 1)
            y = y0 + usable * t
            x = p0x + (p1x - p0x) * ((y - p0y) / span)
            r = r_big + (r_sml - r_big) * t
            s.ellipse(C_BUBBLE, x - r, y - r, r * 2, r * 2,
                      line_argb=line_argb, line_w=(3.0 - 0.6 * t) * sc)

    def _draw_bubble(self, s, lay, now):
        """气泡分形态绘制：OK 态走 _draw_bubble_ok，其余走数据态文案（≤2 行）。"""
        if self._bub_mode == BUBBLE_OK or self._ok_animating(now):
            self._draw_bubble_ok(s, lay, now)
            return
        sc = lay["sc"]
        W = lay["W"]
        bx, by, bw, bh = self._bubble_box(lay)      # 气泡锚定在头顶上方（见 _bubble_box）
        # 主椭圆（白底 + 藏蓝描边）
        s.ellipse(C_BUBBLE, bx, by, bw, bh, line_argb=C_BUBBLE_LINE, line_w=3.5 * sc)
        self._draw_thought_circles(s, lay, C_BUBBLE_LINE)
        # 文案（藏蓝粗体，垂直居中；第三行已下线 —— 2026-09-29 用户反馈"任何时候都不要出现"）
        if self._in_talk():
            lines = [self._quote_dur, self._quote, ""]
            sizes = (13 * sc, 15 * sc, 9.5 * sc)
            bolds = (True, True, False)
            cols = (C_TXT_HEAD, C_TXT_MAIN, C_TXT_DIM)
        else:
            lines = list(self._bubble_lines())
            sizes = (14.5 * sc, 12.5 * sc, 9.5 * sc)
            bolds = (True, True, False)
            cols = (C_TXT_HEAD, C_TXT_MAIN, C_TXT_DIM)
            # 积分制会话（latest_turn.credit>0，见 _bubble_lines）→
            # "本轮 X 积分" 染樱花粉（看板 --sakura）；非积分保持墨色
            if (self.latest_turn or {}).get("credit"):
                cols = (C_TXT_HEAD, C_TXT_PINK, C_TXT_DIM)
        # 空行跳过（空态气泡只留一行引导）并垂直聚拢，避免留出空行位
        rows = [(ln, sz, bd, col) for ln, sz, bd, col
                in zip(lines, sizes, bolds, cols) if ln]
        total_h = sum(sz * 1.5 for _, sz, _, _ in rows) + 8 * sc * 2
        ty = by + (bh - total_h) / 2
        for ln, sz, bd, col in rows:
            s.ctext(ln, W / 2, ty, sz, col, bold=bd, maxw=bw - 56 * sc)
            ty += sz * 1.5 + 8 * sc

    def _draw_bubble_ok(self, s, lay, now):
        """OK 完成态：整颗气泡换成 OK 样式 —— OK 图形做绝对主视觉。

        复用数据态的元素（同椭圆几何 / 同白底 / 同想法小圆尾巴 / 同字形体系），
        只做必要调整：主色蓝→橙、内容由「三行文案」换成「OK 图形」。
        几何全部取自 ok_spec()，形状参数改一处即全局同步。

        三处视觉反馈叠在一起，保证"状态变了"一眼可见：
          ① 描边蓝→橙渐变（切换动画期间插值，最强信号）
          ② 图形弹入（0.55→1.0 过冲）/ 退场收小
          ③ 每次点击图形脉冲放大，配进度点点亮
        """
        # 气泡位置与数据态同源（锚定头顶），否则形态切换时气泡会上下跳一下
        sp = ok_spec(lay, bub_y=self._bubble_box(lay)[1])
        sc = lay["sc"]
        gs, k, _anim = self._ok_visual(now)
        # 保持段（已点满）用满橙，其余按退场动画的 k 插值
        if self._ok_clicks >= MOTION.OK_CLICKS_NEEDED:
            k = 1.0
        # 图形缩放 = 切换动画 × 点击脉冲（两条曲线独立，避免互相覆盖）
        gs *= (1.0 + self._ok_pulse(now))
        line_c = _lerp_argb(C_BUBBLE_LINE, MOTION.COLOR_OK, k)
        s.ellipse(C_BUBBLE, sp["bub_x"], sp["bub_y"], sp["bub_w"], sp["bub_h"],
                  line_argb=line_c, line_w=3.5 * sc)
        self._draw_thought_circles(s, lay, line_c)

        # 主视觉：OK 图形居中放大（纵向/横向都落在椭圆内接区内，越界会被曲线切掉）
        if gs > 0.01:
            dw, dh = sp["g_w"] * gs, sp["g_h"] * gs
            if self._ok_glyph_cache:
                tmp = self._ok_glyph_cache[0]
                s.blit(tmp, sp["g_cx"] - dw / 2, sp["g_cy"] - dh / 2, dw, dh)
            else:
                # 图形资源缺失的兜底：程序化橙环，至少状态可辨
                r = dh / 2
                s.ellipse(MOTION.COLOR_OK, sp["g_cx"] - r, sp["g_cy"] - r, r * 2, r * 2)
                r2 = r * 0.46
                s.ellipse(C_BUBBLE, sp["g_cx"] - r2, sp["g_cy"] - r2, r2 * 2, r2 * 2)

        # 文案：把"还要点几次"直接讲出来（仅部分方案显示；计数可视化不只靠感觉）
        if sp["cap_y"] is not None:
            need = MOTION.OK_CLICKS_NEEDED
            left = max(0, need - self._ok_clicks)
            if self._ok_clicks == 0:
                cap = "任务完成 · 点我恢复" if need == 1 else f"任务完成 · 点我 {need} 次"
            elif left > 0:
                cap = f"还差 {left} 次回到数据…"
            else:
                # 点满：只陈述"正在恢复"，不写拟人台词——古见几乎不说话（人设红线）
                cap = "完成 · 正在恢复…"
            s.ctext(cap, sp["W"] / 2, sp["cap_y"], sp["cap_font"], MOTION.COLOR_OK,
                    bold=left == 0, maxw=sp["bub_w"] - 56 * sc)

        # 进度点：点亮 = 已点击次数。只需要点 1 下时无所谓"进度"，整组不画，气泡保持干净
        if sp["dots_cy"] is not None and MOTION.OK_CLICKS_NEEDED > 1:
            rr, gap, cyy = sp["dot_r"], sp["dot_gap"], sp["dots_cy"]
            x0 = sp["W"] / 2 - gap * (MOTION.OK_CLICKS_NEEDED - 1) / 2
            for i in range(MOTION.OK_CLICKS_NEEDED):
                cx = x0 + i * gap
                if i < self._ok_clicks:
                    s.ellipse(MOTION.COLOR_OK, cx - rr, cyy - rr, rr * 2, rr * 2)
                else:
                    s.ellipse(C_BUBBLE, cx - rr, cyy - rr, rr * 2, rr * 2,
                              line_argb=_lerp_argb(C_BUBBLE_LINE, MOTION.COLOR_OK, 0.75),
                              line_w=1.5 * sc)

    # ---- 提案 §1/§3：立绘平移与眨眼绘制 ----
    def _blit_pet(self, s, cached, img, x, y, w, h):
        """立绘合成：整张统一水平平移（视线跟随 + 拖拽倾斜），1:1 alpha 直拷。

        之前曾用 split=0.55 做"上半身/下半身"水平切分以实现尾鳍独立摆动和拖拽头身反向
        倾斜，但实测在视线跟随 ±1.5px 与尾鳍 ±3px 共同作用下，衣领蕾丝等高对比带会被
        切成两段错开 2-4px，露出肉眼可见的"白缝"。在 1:1 预缩放 + AlphaBlend 直拷这条
        省电路径下，要"像素域多层独立位移 + 完美无白缝"代价极高（需 GDI+ 重采样或亚像素
        overlap），权衡后改为：所有水平位移合成一个 head_dx，整张平移；视差效果由整张
        立绘 ±1.5px 内的微动体现（用户能感受到"她看着我"，且物理上不可能有缝）。
        """
        head_dx = self._gaze_dx + self._drag_tilt
        if cached is None:
            if img is not None:
                # 没命中预缩放缓存（尺寸过渡/极端缩放）：直接重采样，
                # 但**同样要应用视线/拖拽位移**，否则那几帧立绘会横向跳一下
                s.image(img, x + head_dx, y, w, h)
            return
        if abs(head_dx) < 0.2:
            s.blit(cached, x, y, w, h)         # 完全静止 → 1:1 整块，最快
            return
        # 整张平移：单次 blit 走 GDI 硬件 alpha 混合，自带抗锯齿，不存在接缝。
        s.blit(cached, x + head_dx, y, w, h)





    # ================= 帧序列：桌宠唯一形态「写字的她」=================
    # 触发相位与气泡 OK 态**同源**（都是运行中会话数 n），见 wb_anim.WritingPhase。
    # 常态（hidden / appear / writing）循环播「写字段」；
    # 任务完成（present）播一遍「翻页 + 举本子展示」段，播完回到写字循环。

    def _anim_image(self, i, name=None):
        """按「动作 + 帧号」加载并缓存 GDI+ 位图。

        以前只有写字一个动作，缓存 key 就是帧号；现在按动作分桶
        （不同动作的帧号会撞车，混在一起会串帧）。
        """
        if name is None:
            name = self._wr.clip_name(self._clips)
        clip = (getattr(self, "_clips", {}) or {}).get(name)
        if clip is None:
            return None
        bucket = self._anim_imgs.setdefault(name, {})
        if i in bucket:
            return bucket[i]
        path = clip.frame_path(i)
        img = P()
        if _LoadImage(path, ctypes.byref(img)) != 0 or not img:
            log_exception(f"[anim] 缺帧 {path}")
            bucket[i] = None
        else:
            w, h = U32(0), U32(0)
            _GetImageW(img, ctypes.byref(w))
            _GetImageH(img, ctypes.byref(h))
            bucket[i] = (img, w.value, h.value)
        return bucket[i]

    def _anim_play_now(self):
        """现在该不该播帧序列 —— 帧序列是桌宠**唯一**的表现方式，有素材就恒为真。"""
        return bool(self._clips)

    def _draw_anim_frame(self, s, lay, now):
        """画当前帧，并把 _spr_rect/_spr_key 指到帧序列的内容框上
        （气泡锚点 / 命中区 / 视线全部照常工作）。返回是否真的画了。"""
        name = self._wr.clip_name(self._clips)
        clip = (getattr(self, "_clips", {}) or {}).get(name)
        if clip is None:
            return False
        # 帧号按**单调高精度时钟**（perf_counter）算，不用传进来的墙钟 `now`：
        # Windows 的 `time.time()` 只有 ~15.6ms 粒度，采样抖动会让 `int(dt*fps)`
        # 每隔十几帧漏掉一帧（实测 145 tick 里 9 次）——那是肉眼能看见的微顿。
        # ⚠️ `_anim_t0` 从此是 perf_counter 基准（只在下面重置、只在 index 里相减）。
        mono = time.perf_counter()
        if self._anim_key != (self._wr.act, name):
            self._anim_key = (self._wr.act, name)
            self._anim_t0 = mono
            # 内容框也跟着换 —— 各动作的近景/全身程度不同，锚点必须跟着走
            self._spr_cbox["__anim__"] = list(clip.content_box)
            # 音效跟着动作走（待机/困了循环，其余播一遍）
            self._play_anim_sfx(self._wr.act, name)
        i = clip.index(self._wr.act, self._anim_t0, mono)
        # 循环动作（idle / sleepy / write）：**固定步进 —— 每 tick 恰好 +1 帧**。
        # 为什么不按墙钟取帧（2026-09-30 用户报障「写字不流畅」）：
        #   墙钟取帧 `int(dt*fps)` 会时而 +2（定时器被系统拉长）时而 +0（`time.time()`
        #   只有 15.6ms 粒度 → 采样抖动），肉眼就是"一顿一顿"。固定步进 = **每一帧都播、
        #   顺序不乱、节奏均匀**；代价只是节奏跟着 tick 走（tick 慢了画面略慢，但绝不跳帧）
        #   —— 正合本项目"流畅优先、帧数不要减少"的口径。
        # 一次性动作仍按墙钟走（必须赶在状态切换前播到末帧），换动作时用墙钟算出的
        # `i`（此时 `_anim_t0` 刚重置 → 从头开始）起头。
        _prev_i = getattr(self, "_anim_i_prev", None)
        _same = (getattr(self, "_anim_i_key", None) == (self._wr.act, name)
                 and _prev_i is not None)
        if _same and clip.act_is_loop(self._wr.act):
            # 循环区间由素材给（写字段可能带「进入写字」前摇 → 首轮播完再循环尾巴）
            _lo, _hi = clip.loop_range(self._wr.act)
            _nxt = _prev_i + 1
            i = _nxt if _nxt <= _hi else _lo
        self._anim_i_key = (self._wr.act, name)      # 换动作/换素材时这里自然重置
        self._anim_i_prev = i
        cache = self._anim_caches.get(name) or []
        ent = cache[i] if i < len(cache) else None
        if not ent:
            return False
        sc = lay["sc"]
        W, H = lay["W"], lay["H"]
        ph = lay["pet_h"] - 8 * sc
        hover_s = 1.0 + (MOTION.HOVER_SCALE - 1.0) * MOTION.ease_out_cubic(self._hover_t)
        ph_draw = ph * hover_s * self._squash * clip.head_scale
        pw = ph_draw * clip.canvas[0] / float(clip.canvas[1])
        x = (W - pw) / 2
        y_bottom = H - 4 * sc + self._breath + self._float_dy
        # 矩形/键指向帧序列：气泡锚点与命中区按它的内容框算（meta 里有归一化内容框）
        self._spr_rect = (x, y_bottom - ph_draw, pw, ph_draw)
        self._spr_key = ("__anim__", "")
        sw = pw * 0.72 * self._shadow_scale
        s.ellipse(C_SHADOW, W / 2 - sw / 2, y_bottom - 6 * sc, sw,
                  8 * sc * self._shadow_scale)
        if abs(ent[2] - ph_draw) <= max(2.0, ph_draw * 0.16) or self._cache_building:
            # 尺寸差得远通常跳过（别硬拉伸出鬼影）；但**后台正在按新尺寸重建**例外 ——
            # 那 1~3 秒里宁可略糊也要看得见她（否则缩放跳档时整帧不画 = 消失）。
            self._blit_pet(s, ent[0], None, x, y_bottom - ph_draw, pw, ph_draw)
        else:
            pass
        return True


    # ---- 形态尺寸归一（点击换姿势时的"忽大忽小"）----



    def _draw_pet(self, s, lay):
        """桌宠本体 —— 唯一形态「写字的她」（AI 视频 → 透明帧序列）。

        2026-09-28 之前这里是八套立绘 + 眨眼贴片 + 腮红 + 状态交叉溶解 +
        点击切形态，现已**全部删除**；只剩帧序列一条路径，**没有回退分支**。
        """
        if self._anim_play_now() and self._draw_anim_frame(s, lay, time.time()):
            return
        if getattr(self, "_anim_missing_logged", False):
            return
        self._anim_missing_logged = True
        # 两种情形分开说清（曾混成一句"素材缺失"+假异常栈，把排查带进沟里——
        # 实际绝大多数是启动首绘/重建窗口期的瞬时缓存未命中，下一帧自愈）。
        try:
            with open(ERR_LOG, "a", encoding="utf-8") as _f:
                if not self._clips:
                    _msg = ("[anim] 素材缺失 assets/anim/<动作>/ —— 桌宠无内容可画"
                            "（跑 tools/video_to_pet_frames.py 重建）")
                else:
                    _sizes = {k: len(v) for k, v in self._anim_caches.items()}
                    _msg = (f"[anim] 帧缓存瞬时未命中（自愈型，观察即可）："
                            f"act={self._wr.act} clip={self._wr.clip_name(self._clips)} "
                            f"cache={_sizes}")
                _f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {_msg}\n")
        except Exception:
            pass


    # ---- 消息 ----
    def on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        if msg == WM_CLOSE:
            # ⚠️ 外部关闭请求（守望 kill_whale / 任务栏关窗）走这里：必须留痕，
            #    否则"桌宠无声消失"完全没法归因（2026-09-30 排查时吃过这个亏）。
            self._report_event("close_req", detail="wm_close")
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_TIMER:
            return self._on_timer(hwnd, wparam)
        if msg == WM_APP_ANIM:                  # 高精度动画时钟投递的 tick
            self._anim_pending = False
            self._anim_tick()
            return 0
        if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK,
                   WM_MOUSEMOVE, WM_RBUTTONUP):
            return self._pet_mouse(hwnd, msg, wparam, lparam)
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_timer(self, hwnd, timer_id):
        if timer_id == ID_TIMER_TICK:
            self.tick()
        elif timer_id == ID_TIMER_ANIM:
            self._anim_tick()
        elif timer_id == ID_TIMER_WATCH:
            # 台词模式期间：外部任意点击 → 回数据模式
            if self._in_talk() and (_user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
                pt = POINT()
                _user32.GetCursorPos(ctypes.byref(pt))
                lay = self._layout()
                x, y = self._window_xy(hwnd)
                if not (x <= pt.x <= x + lay["W"] and y <= pt.y <= y + lay["H"]):
                    self._talk_until = 0.0
                    self._drawn_sig = None
        return 0

    def _pet_mouse(self, hwnd, msg, wparam, lparam):
        if self._menu_open:
            return 0        # 菜单模态中：鼠标全归菜单（capture 集中制），桌宠一律不响应
        lay = self._layout()
        if msg == WM_LBUTTONDOWN:
            pt = POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            self._down = (pt.x, pt.y)
            self._win0 = self._window_xy(hwnd)
            self._dragging = True
            self._moved = False
            self._pressed = True
            self._drift = None                     # 用户抓住桌宠 → 取消漂移
            self._drift_back_at = 0.0
            self._last_interact = time.time()
            self._drawn_sig = None                 # 重绘按压 Q 弹态
            # 提案 §3：记录按下时刻，驱动 squash 曲线 / 长按判定
            self._press_at = time.time()
            self._squash_t0 = self._press_at
            _user32.SetCapture(hwnd)
            return 0
        if msg == WM_MOUSEMOVE and self._dragging and (wparam & MK_LBUTTON):
            pt = POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            dx, dy = pt.x - self._down[0], pt.y - self._down[1]
            if abs(dx) + abs(dy) > 3:
                self._moved = True
                bx0, by0, bx1, by1 = self._drag_bounds(lay)
                nx = max(bx0, min(self._win0[0] + dx, bx1))
                ny = max(by0, min(self._win0[1] + dy, by1))
                self._move_window(hwnd, int(nx), int(ny))
                # 提案 §3：拖拽时身体朝运动方向倾斜（上限 ±6px，松手 4 帧内回正）
                lim = MOTION.DRAG_TILT_MAX_PX * self.scale
                self._drag_tilt = max(-lim, min(lim, dx * 0.08))
            return 0
        if msg == WM_LBUTTONDBLCLK:
            # 系统原生双击（CS_DBLCLKS）：第二次 DOWN 被翻译成本消息。
            # 仅**想法泡泡**上的双击开看板；身体上的双击 = 一次快速连击互动。
            self._swallow_up = True               # 吞掉配对的第二次 UP
            self._pressed = True                  # 保留 Q 弹反馈
            self._drawn_sig = None
            _user32.SetCapture(hwnd)
            py = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            self._last_interact = time.time()
            if self._in_bubble(lay, py):
                self._report_event("double_click", detail="bubble")
                self.open_dashboard()
            else:
                self._report_event("double_click", detail="body")
                self._body_tap_feedback(lay)
            return 0
        if msg == WM_LBUTTONUP:
            _user32.ReleaseCapture()
            self._dragging = False
            was_press = self._pressed
            self._pressed = False
            self._drawn_sig = None
            held_ms = (time.time() - self._press_at) * 1000 if self._press_at else 0.0
            self._squash_t0 = time.time()          # 提案 §3：进入回弹段
            if self._swallow_up:                  # 双击配对的 UP，直接吞掉
                self._swallow_up = False
                return 0
            if self._moved:                        # 拖动结束：贴边吸附 + 记忆
                x, y = self._window_xy(hwnd)
                nx, ny = self._snap(x, y)
                self._move_window(hwnd, nx, ny)
                self._home = (nx, ny)
                save_pos(POS_FILE, nx, ny)
                self._last_interact = time.time()
                self._report_event("drag_snap", ok=True, detail=f"{nx},{ny}")
                return 0
            if was_press:                          # 单击：按区域即时分发（不等双击窗口）
                self._last_interact = time.time()
                py = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                px = ctypes.c_short(lparam & 0xFFFF).value
                if self._in_bubble(lay, py):
                    if self._bub_mode == BUBBLE_OK:
                        # OK 态：单击计入连击（点满 3 次才回到数据态），不切台词
                        self._bubble_ok_click()
                    else:
                        self._report_event("bubble_click")
                    self._drawn_sig = None
                elif held_ms >= MOTION.LONGPRESS_MS:
                    self._long_press_release(lay)   # 提案 §3：长按憋气后"噗"地喷水
                else:
                    # 身体点击：2026-09-28 起「按区域切形态」随 v3 立绘删除
                    # （只剩写字一种形态）。反馈与双击同源，见 `_body_tap_feedback`。
                    self._body_tap_feedback(lay)
            return 0
        if msg == WM_HOTKEY and wparam == ID_HOTKEY_FOLLOW:
            self._hotkey_cycle_focus()      # P3 全局热键：手动聚焦轮换
            return 0
        if msg == WM_RBUTTONUP:
            if self._menu_open:
                return 0                 # 菜单模态中忽略再入右键（防嵌套菜单）
            self._report_event("menu_open")
            self.context_menu(hwnd)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 菜单 / 看板 ----
    # （owner-draw 的 measure/draw/AppendMenuW 协议代码已随 TrackPopupMenu 一并退役；
    #   菜单渲染改由 MenuSession 自绘分层窗口完成，见模块级 MenuSession 段落。）

    def context_menu(self, hwnd):
        pt = POINT()
        _user32.GetCursorPos(ctypes.byref(pt))

        def _it(label, cmd, **kw):
            return {"label": label, "cmd": cmd, **kw}

        items = [_it("打开完整看板", IDM_DASHBOARD),
                 _it("想法气泡", IDM_BUBBLE, checked=self.bubble_on),
                 _it("音效", IDM_SOUND, checked=self.sound_on),
                 # P2：行为开关（此前只能改 wb_motion 的 *_ON 后重启）
                 _it("完成态自动收起", IDM_OK_AUTO, checked=self.ok_autodismiss_on),
                 _it("随 Agent 退出联动关闭", IDM_LINKED, checked=self.linked_close_on),
                 _it("跟随前台切换聚焦", IDM_FOLLOW, checked=self.follow_on),
                 _it("锁定聚焦（pin）", IDM_PIN, checked=self.focus_pin),
                 {"sep": True}]
        # P2 连续锚点：今日时间线（跨 agent 轮次，点条目开看板）
        _tl_summary, _tl_recent = self._today_timeline()
        sub_tl = [_it("今天：" + (format_timeline_summary(_tl_summary) or "暂无轮次"),
                      IDM_TIMELINE0), {"sep": True}]
        sub_tl += [_it(f"{hhmm}  {agent} · {title[:20]}", IDM_TIMELINE0 + 1 + i)
                   for i, (hhmm, agent, title) in enumerate(_tl_recent)]
        if not _tl_recent:
            sub_tl.append(_it("（今天还没有轮次）", 0, disabled=True))
        items.append({"label": "今日时间线", "sub": sub_tl})
        # P3 手动聚焦：锁定或跟随失效时的兜底（登记册启用 agent，accent 打点）
        _agents = REG.load()["agents"]        # 只读一次（原先进出两次，每次都要解析 JSON）
        _focusables = [(k, v) for k, v in sorted(_agents.items(),
                                                 key=lambda kv: kv[1].get("order", 100))
                       if v.get("enabled")]
        items.append({"label": "手动聚焦（Ctrl+Alt+F9 轮换）",
                      "sub": [_it(f"{v.get('label') or k}", IDM_FOCUS0 + i,
                                  checked=(self._follow_focus or {}).get("key") == k)
                              for i, (k, v) in enumerate(_focusables)]})
        # P4 接续：仅当角标活跃时出现（用户触发才生成——零隐私风险、无主动打扰）
        _h = self._handoff
        if _h and time.time() - _h.get("since", 0) <= MOTION.HANDOFF_SHOW_MAX_S:
            _hlabel = ((_agents.get(_h["key"]) or {}).get("label") or _h["key"])
            items.append(_it(f"生成接续摘要（{_hlabel}）→ 剪贴板", IDM_HANDOFF))
        items.append({"sep": True})
        # 大小子菜单（0.6–2.5x，对齐上游挂件）
        items.append({"label": "桌宠大小", "sub": [
            _it(f"{sc:.1f}x" + ("（默认）" if sc == 1.0 else ""), IDM_SCALE0 + i,
                checked=abs(self.scale - sc) < 1e-6)
            for i, sc in enumerate(SCALES)]})
        # 动效质量子菜单（提案 §6：完整 / 精简 / 关闭，用户可手动降级）
        items.append({"label": "动效质量", "sub": [
            _it(label, IDM_QUALITY0 + i, checked=self._quality == q)
            for i, (q, label) in enumerate(QUALITY_CHOICES)]})
        # 修复卡死会话（2026-09-28 事故）：宿主崩溃留下残留 working → 气泡永远"运行中"。
        # 显示层已自动兜底（wb_hover_core 残留过滤）；这一项做真修复=写库落回 completed。
        # 带 TTL 的记忆（见 _stale_list_cached）：菜单在 UI 线程上，不该每次弹都查库
        self._stale_list = self._stale_list_cached()
        if self._stale_list:
            _mins = max(v["age_sec"] for v in self._stale_list) // 60
            items.append(_it(f"修复卡死会话（{len(self._stale_list)} 个 · 停更 {_mins} 分）",
                             IDM_REPAIR, danger=True))
        items.append({"sep": True})
        # 自更新：只有「配了更新源」或「已知有新版」才出现 —— 默认不联网就别摆个没用的项
        try:
            import wb_update as _UP
            _up_src = _UP.source()
        except Exception:
            _up_src = ""
        _up_m = (self._update_info or {}).get("manifest") or {}
        if (self._update_info or {}).get("available") and _up_m.get("version"):
            items.append(_it(f"更新到 v{_up_m['version']}", IDM_UPDATE))
        elif _up_src:
            items.append(_it("检查更新", IDM_UPDATE))
        items.append(_it("退出古见同学", IDM_QUIT, danger=True))
        # ★ 菜单是**模态**的：MenuSession.run 自己跑消息循环。这里只闸住 `tick`
        #   （查库 + 重绘气泡）—— 菜单开着时气泡内容不必刷新，省得在模态循环里插一次
        #   SQLite 查询。**动画照常跑**（`_anim_tick` 不再被闸）：菜单是独立分层窗口，
        #   和桌宠重绘互不干扰；闸住动画只会让人物当着用户的面冻住（2026-09-29 修复）。
        self._menu_open = True
        try:
            dpi = (_user32.GetDpiForWindow(hwnd) or 96) if hwnd else 96
            cmd = MenuSession(items, dpi=dpi).run(hwnd, (pt.x, pt.y))
        finally:
            self._menu_open = False
        self._drawn_sig = None          # 菜单关了强制重绘一帧（补上定格期间的变化）
        if cmd == IDM_TIMELINE0:
            self._report_event("timeline_open", detail="dashboard")
            self.open_dashboard()
        elif IDM_TIMELINE0 < cmd <= IDM_TIMELINE0 + len(_tl_recent):
            idx = cmd - IDM_TIMELINE0 - 1
            if 0 <= idx < len(_tl_recent):
                hhmm, agent, title = _tl_recent[idx]
                self._report_event("timeline_open",
                                   detail=f"{hhmm} {agent} {title[:20]}")
            self.open_dashboard()
        elif IDM_FOCUS0 <= cmd < IDM_FOCUS0 + len(_focusables):
            key = _focusables[cmd - IDM_FOCUS0][0]
            self._set_manual_focus(key)
            self._report_event("menu_dashboard")
            self.open_dashboard()
        elif cmd == IDM_BUBBLE:
            self.bubble_on = not self.bubble_on
            self._report_event("menu_bubble", detail=str(self.bubble_on))
            self._save_settings()
            self._recreate_window()
        elif cmd == IDM_SOUND:
            self.sound_on = not self.sound_on
            if self.sound_on and not self._sounds:
                self._sounds = _make_sounds()
            if self.sound_on:
                self._anim_sfx_act = None       # 立刻把当前动作的音效接上
            else:
                self._stop_anim_sfx()           # 关掉就立刻安静
            self._report_event("menu_sound", detail=str(self.sound_on))
            self._save_settings()
        elif cmd == IDM_OK_AUTO:
            self._toggle_ok_autodismiss()
        elif cmd == IDM_LINKED:
            self._toggle_linked_close()
        elif cmd == IDM_FOLLOW:
            self._toggle_follow()
        elif cmd == IDM_PIN:
            self._toggle_focus_pin()
        elif cmd == IDM_HANDOFF:
            self._gen_handoff()
        elif cmd == IDM_UPDATE:
            self._on_update_menu()
        elif cmd == IDM_REPAIR:
            try:
                n = repair_stale_working(self.db_path, self.wb_db)
            except Exception:
                n = -1
            self._report_event("menu_repair_stale", ok=(n > 0), detail=str(n))
            if n > 0:
                self._say_once(f"修复了 {n} 个卡死的会话状态", "修复完成", 6.0)
            elif n == 0:
                self._say_once("没有需要修复的会话", "提示", 4.0)
            else:
                self._say_once("修复失败：workbuddy.db 写不进去（宿主在运行？）", "修复失败", 6.0)
            self._drawn_sig = None
        elif cmd == IDM_QUIT:
            self._report_event("menu_quit")
            _user32.PostQuitMessage(0)
        elif IDM_SCALE0 <= cmd < IDM_SCALE0 + len(SCALES):
            self.scale = SCALES[cmd - IDM_SCALE0]
            self._report_event("menu_scale", detail=str(self.scale))
            self._save_settings()
            self._recreate_window()
        elif IDM_QUALITY0 <= cmd < IDM_QUALITY0 + len(QUALITY_CHOICES):
            self.set_quality(QUALITY_CHOICES[cmd - IDM_QUALITY0][0])

    def _dashboard_port(self):
        try:
            return urlparse(self.dashboard).port or 8801
        except Exception:
            return 8801

    def _ensure_dashboard_server(self):
        with self._spawn_lock:
            if self._spawning_dash:
                return
            self._spawning_dash = True
        try:
            # 打包后没有 .py 可跑 → 「自身 exe + --api」；源码运行与改造前**逐字符一致**
            cmd = RT.self_cmd("api", port=self._dashboard_port())
            if not RT.FROZEN and not os.path.isfile(cmd[1]):
                log_exception(f"[dashboard] 找不到看板服务脚本 {cmd[1]}")
                return
            port = self._dashboard_port()
            self._report_event("ensure_server", ok=True,
                               detail=f"启动 wb_api --port {port}")
            tmp = os.environ.get("TEMP") or "C:/Windows/Temp"
            with open(os.path.join(tmp, "wb-usage.log"), "ab") as fout, \
                 open(os.path.join(tmp, "wb-usage.err.log"), "ab") as ferr:
                subprocess.Popen(
                    cmd,
                    cwd=RT.EXE_DIR if RT.FROZEN else os.path.dirname(cmd[1]),
                    stdin=subprocess.DEVNULL, stdout=fout, stderr=ferr,
                    creationflags=subprocess.DETACHED_PROCESS
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            log_exception("[dashboard] 拉起看板服务失败")
        finally:
            self._spawning_dash = False

    # ---- 自更新（检查 / 下载 / 替换）----
    # ⚠️ 铁律：**网络与文件操作一律放后台线程**，绝不进 Win32 消息循环
    #    （本项目既有教训：把带超时的 urlopen 放进 timer tick 会卡死拖动与点击）。

    def _say_once(self, text, dur="提示", secs=8.0):
        """走既有的「说话」通道在气泡里提示一句（不打断动画、不加音效）。"""
        try:
            self._quote = text
            self._quote_dur = dur
            self._talk_until = time.time() + secs
            self._drawn_sig = None          # 强制重绘
            self._arm_watch()
        except Exception:
            pass

    def _notify_update(self, info):
        m = (info or {}).get("manifest") or {}
        self._say_once(f"有新版本 v{m.get('version')}，右键可以更新", "发现更新", 10.0)

    def _start_update_watch(self):
        """后台自更新检查：启动查一次，之后每 24 小时一次。

        **没配更新源就直接退出** —— 默认零联网（隐私安全）。
        查到新版只**提示**，绝不自动替换：换版本必须用户点。
        """
        try:
            import wb_update                       # 提前失败就别起线程
        except Exception:
            return

        def job():
            first = True
            while True:
                try:
                    if not wb_update.source():
                        return                     # 未配置 → 完全不联网
                    info = wb_update.check()
                    self._update_info = info
                    if info.get("available"):
                        self._notify_update(info)
                except Exception:
                    pass
                time.sleep(600 if first else 24 * 3600)
                first = False

        threading.Thread(target=job, daemon=True, name="komi-update-watch").start()

    def _start_update_check(self, notify=False):
        """右键「检查更新」：后台查一次，结果写回 self._update_info。"""
        if self._update_busy:
            return
        self._update_busy = True

        def job():
            try:
                import wb_update
                info = wb_update.check()
                self._update_info = info
                if notify:
                    if info.get("available"):
                        self._notify_update(info)
                    else:
                        self._say_once(str(info.get("msg") or "检查完成"), "更新")
            except Exception as e:
                if notify:
                    self._say_once(f"检查更新失败：{e}", "更新")
            finally:
                self._update_busy = False
        threading.Thread(target=job, daemon=True, name="komi-update-check").start()

    def _start_update_apply(self):
        """下载 + 解压 + 交给新版本替换。完成后**本进程必须退出**（否则文件被锁着换不掉）。"""
        if self._update_busy:
            return
        self._update_busy = True
        self._say_once("正在下载新版本…", "更新中", 30.0)

        def job():
            try:
                import wb_update
                m = wb_update.run_apply_new()
                self._say_once(f"v{m['version']} 就绪，正在更新…", "更新中", 10.0)
                time.sleep(1.2)                    # 让气泡那句话有机会显示
                _user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)   # 走正常退出路径
            except Exception as e:
                self._update_busy = False
                self._say_once(f"更新失败：{e}", "更新", 12.0)
        threading.Thread(target=job, daemon=True, name="komi-update-apply").start()

    def _on_update_menu(self):
        info = self._update_info or {}
        if info.get("available"):
            self._report_event("update_apply",
                               detail=str((info.get("manifest") or {}).get("version")))
            self._start_update_apply()
        else:
            self._report_event("update_check")
            self._start_update_check(notify=True)

    def _stale_list_cached(self, ttl=20.0):
        """残留会话清单（右键菜单用）：带 TTL 的记忆。

        菜单整条路径都跑在 UI 线程上，而 `stale_working_sessions` 是两次 SQL（宿主库 +
        数仓），实测 ~10ms —— 每次弹菜单都查一遍没必要。它只决定「修复卡死会话」那一项
        显不显示，20 秒的新鲜度完全够（周期自愈另有 10 分钟一轮，见 _start_stale_repair）。
        """
        now = time.time()
        if self._stale_ts and now - self._stale_ts < ttl:
            return self._stale_list
        try:
            self._stale_list = stale_working_sessions(self.db_path, self.wb_db)
        except Exception:
            self._stale_list = []
        self._stale_ts = now
        return self._stale_list

    def _start_stale_repair(self):
        """启动自愈 + **周期自愈**：把残留的 working 会话落回终态。

        背景（2026-09-28 事故）：宿主崩溃/被杀不会把 working 落回终态，
        桌宠会永远显示"会话运行中"。显示层有停更过滤兜底（wb_hover_core），
        这里做的是真修复=写库。判定与写法见 repair_stale_working：
        只动停更超阈值的行、UPDATE 复查 status='working'，误杀面趋近于零。
        SQLite 写很快，但仍按铁律放后台线程，绝不进消息循环。

        2026-09-29：原来只跑**启动那一次** —— 中途卡死的会话得等下次重启才治
        （用户现场就是"一直卡在写字状态"）。现在改成每 REPAIR_EVERY_SEC 跑一轮，
        首轮仍按启动自愈口径记录事件（`startup_repair_stale`），后续只记
        `periodic_repair_stale`（不弹气泡，避免打扰）。
        """
        def job():
            time.sleep(5.0)               # 等系统落定（数仓/库句柄就绪、宿主若在启动先让它走）
            first = True
            while True:
                try:
                    n = repair_stale_working(self.db_path, self.wb_db)
                    if n > 0:
                        if first:
                            self._report_event("startup_repair_stale", ok=True, detail=str(n))
                            self._say_once(f"修复了 {n} 个卡死的会话状态", "开机自愈", 6.0)
                        else:
                            self._report_event("periodic_repair_stale", ok=True, detail=str(n))
                except Exception:
                    log_exception("[stale-repair] 自愈失败")
                first = False
                time.sleep(REPAIR_EVERY_SEC)
        threading.Thread(target=job, daemon=True, name="komi-stale-repair").start()

    def _start_api_guard(self):
        """看板 API 的常驻守护：**这不放在消息循环里**（见下）。

        为什么需要：
          看板 API（`wb_api.py`）在 Windows 上**没有守护进程**。它只有登录自启那一次，
          中途如果退出（例如它内置看门狗自杀、或被任务管理器/更新打断），就没人拉起，
          表现为「看板偶尔打不开」——只有用户点开看板时桌宠才会自愈重启一次。
          而**桌宠本身有登录自启的守望 `wb_whale_watcher` 保活**，天然可靠，
          所以把"盯一下 API 端口"这件事交给桌宠最合适。

        ⚠️ 硬约束：**绝不能在 Win32 消息循环里做带超时的网络请求**。
          本项目踩过：urlopen(timeout=0.5) 放进每秒的 timer tick，遇到"端口只 accept
          不响应"的服务端会每 tick 干等满超时 → 消息循环卡顿、拖动/点击全延迟。
          所以这里用**后台 daemon 线程**（sleep 间隔也放得宽），主循环完全不受影响。

        行为：每 API_GUARD_INTERVAL_S 秒探一次；不通才拉起，并带冷却避免疯狂重试。
        """
        def loop():
            while True:
                try:
                    time.sleep(API_GUARD_INTERVAL_S)
                    if api_healthy(self.dashboard):
                        continue                 # 正常 → 什么都不做（后台线程，不影响 UI）
                    now = time.time()
                    if now - getattr(self, "_api_guard_last_spawn", 0.0) < API_GUARD_COOLDOWN_S:
                        continue
                    self._api_guard_last_spawn = now
                    self._report_event("api_guard_spawn",
                                       detail=f"端口不通，拉起 {self.dashboard}")
                    self._ensure_dashboard_server()
                except Exception:
                    pass      # 守护线程不能因为任何异常退出

        threading.Thread(target=loop, daemon=True, name="api-guard").start()

    def open_dashboard(self):
        self._report_event("open_dashboard", detail="请求打开 " + self.dashboard)
        threading.Thread(target=self._open_dashboard_worker, daemon=True).start()

    def _open_dashboard_worker(self):
        url = self.dashboard
        got = False
        try:
            got = api_healthy(url)
            if not got:
                self._ensure_dashboard_server()
                deadline = time.time() + DASH_READY_WAIT
                while time.time() < deadline:
                    time.sleep(0.25)
                    if api_healthy(url):
                        got = True
                        break
            if got:
                webbrowser.open(url)
                self._report_event("open_dashboard", ok=True, detail=url)
                # 提案 §2：成功 → 开心脸 + 爱心 + 上挺
                self.set_emotion(MOTION.EMOTION_SUCCESS)
                return
            # 提案 §2：失败 → 委屈脸 + 下沉（不是怒容、不用红色）
            self.set_emotion(MOTION.EMOTION_FAIL)
            self._report_event("open_dashboard", ok=False,
                               detail=f"{url} 服务未就绪")
            log_exception(f"[dashboard] 打开看板失败：{url} 服务未就绪")
            self._msgbox(f"WorkBuddy 用量看板\n\n{url} 服务未能启动。\n"
                         f"请运行看板服务后重试：\n"
                         f"python scripts\\wb_usage\\wb_api.py "
                         f"--port {self._dashboard_port()}")
        except Exception:
            log_exception("[dashboard] open_dashboard_worker 异常")

    def _msgbox(self, text):
        try:
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
            u.MessageBoxW.restype = ctypes.c_int
            u.MessageBoxW(None, text, "WorkBuddy 古见同学桌宠", 0x40 | 0x1000)
        except Exception:
            pass

    # ---- 主循环 / tick ----
    def run(self):
        msg = MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _sig(self):
        a = self.active
        talk = 1 if self._in_talk() else 0
        # latest_turn 关键字段入签名：数据到达时（即使 working 状态没变）也能触发气泡重绘
        lt = self.latest_turn or {}
        lt_sig = (lt.get("credit"), lt.get("total_tokens"),
                  lt.get("first_ts"), lt.get("last_ts"),
                  lt.get("title") or lt.get("project") or "")
        return (self.db_ok, self.api_ok, talk, self._pressed,
                self._bub_mode, self._ok_clicks,
                tuple((x.get("_wb_title") or x.get("title") or "",
                       x.get("credit"), x.get("total_tokens"),
                       x.get("_last_act") or x.get("last_ts"))
                      for x in a[:3]),
                lt_sig,
                self._follow_focus and (self._follow_focus["key"],
                                        self._follow_focus["kind"]),
                self._timeline_cache and self._timeline_cache[1][0])

    def tick(self):
        if self._menu_open:
            return          # 菜单模态中：不查库、不刷新气泡（动画在 _anim_tick 里照跑）
        # 高精度动画时钟看门狗：时钟线程若异常/被系统掐掉（>2s 没有 tick），
        # 立刻退回 SetTimer —— 最坏也只是回到 21fps 的旧行为，绝不让动画整个冻住。
        if (self._anim_clock_on and self._anim_last_tick
                and time.time() - self._anim_last_tick > 2.0):
            self._stop_anim_clock()
            self._anim_clock_dead = True
            if self.hwnd:
                _user32.SetTimer(self.hwnd, ID_TIMER_ANIM, ANIM_MS, None)
            self._report_event("anim_clock_stalled", ok=False,
                               detail="退回 SetTimer")
        if not self._dragging:
            now = time.time()
            _kpi, active, ok, latest = query_db(self.db_path, self.wb_db)
            if self._read_stale(ok, now) and self.active:
                # 数仓连续读不出来 → 不能一直替它"记着"上次的活跃会话，
                # 否则她会永远卡在写字态（读数失败时保留旧值是防闪烁的权宜，
                # 见 query_db 的约定——但必须有个上限）。
                self.active = []
                self._drawn_sig = None
                self._report_event("read_stale", ok=False,
                                   detail=f"读数失败 {int(now - self._read_ok_at)}s → 视为无任务")
            if active is not None:
                self.active = active
            if latest is not None:
                self.latest_turn = latest
            # latest_turn=None 也保留旧值（避免连续无数据时闪烁归零）
            self.db_ok = ok
            self._sync_data_emotion()
            self._sync_bubble_mode()          # 对话任务结束 → OK 态（见方法内迁移规则）
            sig = self._sig()
            # live 模式下"用时"按 first_ts → now 算，每秒要 +1s 实时跳动
            # 强制每 tick 重绘（开销可忽略；只在 latest_turn 存在时）
            live_tick = bool(self.latest_turn and self.active)
            if live_tick or sig != self._drawn_sig:
                self.draw()
                self._drawn_sig = sig
            _user32.SetWindowPos(self.hwnd, ctypes.c_void_p(HWND_TOPMOST),
                                 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        # 联动关闭：WorkBuddy 退出 → 桌宠一并退出（拖拽中也检测，避免"卡住时孤儿"）
        self._check_linked_close(time.time())
        x, y = self._window_xy(self.hwnd)
        report_state(os.getpid(), True, 1, [x, y], len(self.active),
                     self.db_ok, self.api_ok)
        if self.run_seconds and time.time() - self.t0 > self.run_seconds:
            _user32.PostQuitMessage(0)

    def close(self):
        self._stop_anim_clock()           # 动画时钟线程 + 定时器句柄
        if self._wb_proc_handle:
            try:
                _kernel32.CloseHandle(self._wb_proc_handle)
            except Exception:
                pass
            self._wb_proc_handle = None
        self._uninstall_follow_hotkey()   # P3：全局热键随窗口销毁注销
        self._stop_anim_sfx()             # 动作音效（循环播的那种）必须显式停
        try:
            self.surf.close()
        except Exception:
            pass
        for _bucket in getattr(self, "_anim_imgs", {}).values():
            for spr in _bucket.values():
                try:
                    if spr:
                        _DisposeImage(spr[0])
                except Exception:
                    pass
        try:
            _GdiplusShutdown(self._gp_token)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 单实例保护
# ---------------------------------------------------------------------------
MUTEX_NAME = "Local\\KomiPetWhaleSingleInstance"
ERROR_ALREADY_EXISTS = 183

# 看板 API 守护（_start_api_guard）：探测间隔 / 两次拉起之间的冷却（秒）
API_GUARD_INTERVAL_S = 60.0
API_GUARD_COOLDOWN_S = 60.0


def _acquire_single_instance():
    """抢单实例互斥量。返回 True = 我是唯一实例；False = 已有实例在跑。

    为什么必须有：桌宠**本来没有任何单实例保护**（本项目 2026-09-26 实测发现）。
    而守望进程 wb_whale_watcher.py（登录自启）会"检测到桌宠没跑就拉起"，
    加上用户手动启动/快捷方式，就很容易出现**两个桌宠窗口几乎完全重叠**——
    平时看不出（位置相同、同用一份位置存档），一旦各自随机相位（眨眼/摇摆）不同步，
    就表现为用户反馈的"点击时出现多个重叠"。
    有了它：无论谁来启动第二次，都只会安静退出，并把已有窗口拉到前台给个反馈。
    """
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = wt.HANDLE
    h = k32.CreateMutexW(None, False, MUTEX_NAME)
    if not h:
        return True                      # 创建失败就不拦（宁可多开，不可开不了）
    if k32.GetLastError() == ERROR_ALREADY_EXISTS:
        # 已有实例：把它的窗口拉到前台闪一下，让用户知道"已经开着了"
        try:
            u = ctypes.windll.user32
            hwnd = u.FindWindowW(CLASS_NAME, None)
            if hwnd:
                u.ShowWindow(hwnd, 9)    # SW_RESTORE
                u.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return False
    return True


def main():
    seconds = None
    if "--seconds" in sys.argv:
        try:
            seconds = float(sys.argv[sys.argv.index("--seconds") + 1])
        except Exception:
            seconds = None
    if os.name != "nt":
        sys.stderr.write("[wb-whale] 本脚本仅适用于 Windows，其他平台请运行 hover.py。\n")
        return 2
    # ★ 单实例：已有桌宠在跑 → 安静退出（并把已有窗口拉前台），避免"多个重叠"
    if not _acquire_single_instance():
        sys.stderr.write("[wb-whale] 已有桌宠实例在运行，本次启动退出。\n")
        return 0
    app = None
    try:
        app = WhalePet(run_seconds=seconds)
        app.run()
    except Exception:
        log_exception("[whale]")
        sys.stderr.write(f"[wb-whale] 启动失败，详见日志：{ERR_LOG}\n")
        return 1
    finally:
        if app:
            app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
