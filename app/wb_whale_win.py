#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb_whale_win.py — WorkBuddy 用量看板 · 古见同学桌宠（Windows 原生，纯 ctypes + GDI+）
=====================================================================================
路线 3 v3：按参考视频重做美术 ——
  · 桌宠本体 = v3 立绘 PNG（`assets/pet_v3/pet_<state>.png`，Q 版 8 态 × 双朝向，
    统一画布 + 按脸宽归一 → 切表情不跳尺寸；旧的 `assets/pet_*.png` 24 张已删除）
  · 眨眼 = 分层差分贴片（`pet_v3/blink/`，真实像素，按闭合度取 6 档）
  · 状态切换 = 交叉溶解（对齐参考视频）
  · 气泡 = 深蓝描边椭圆想法框 + 双小圆点（对齐 195012 系列参考图样式）
  · 状态立绘：有活跃会话/说话 → 开心脸；空闲 → 文静脸；贴左/右边缘自动镜像朝向屏幕中心
  · 交互对齐 DeepSeek-Balance-Whale-Widget：拖拽四边吸附、按压 Q 弹、
    单击切台词、双击开看板（自愈拉起服务）、右键菜单（大小 0.6–2.5x / 气泡开关 / 退出）

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
)

# ---------- 动效设计系统（节奏 / 幅度 / 缓动 / 情绪 / 降级，全部集中管理）----------
import wb_motion as MOTION                                    # noqa: E402

# ---------- 跟随模式（聚焦信号）：纯逻辑层（去抖状态机 / 前台→agent 推断）----------
import wb_follow as FOLLOW                                    # noqa: E402

# ---------- 登记册（跟随的 window_hints / accent 来源）----------
import wb_agent_registry as REG                               # noqa: E402

# ---------- 配件层（按"哪些 agent 在用"决定古见佩戴什么）----------
# 失败必须整体降级：配件是锦上添花，绝不能因为它起不来就连桌宠本体一起挂掉
try:
    import wb_accessories as ACC                              # noqa: E402
    import wb_agent_presence as PRESENCE                      # noqa: E402
    _ACC_OK = True
except Exception as _e:                                       # pragma: no cover
    ACC = None
    PRESENCE = None
    _ACC_OK = False
    _ACC_IMPORT_ERR = _e

# ---------- 布局（基准 scale=1.0，实际尺寸 = 基准 × self.scale）----------
BASE_W = 300                   # 窗口宽
BASE_BUB_H = 128               # 气泡椭圆高
BASE_BUBBLE_H = 162            # 气泡区总高（含想法小圆）
# 立绘区高。v3 立绘是**统一画布 1191×1627**（内容只占约 60% 高，见 tools/build_pet_v3.py），
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
POS_FILE = os.path.join(_HERE, ".whale_pos.json")
SETTINGS_FILE = os.path.join(_HERE, ".whale_settings.json")
ASSETS_DIR = os.path.join(_HERE, "assets")
# v3 立绘目录（8 态、统一画布、按脸宽归一）——由 tools/build_pet_v3.py 生成。
# 高冷版（alt_*）已停用：审美线统一到 v3 的 Q 版，见 docs/眨眼重构交接-2026-09-26.md。
SPRITE_DIR = os.path.join(ASSETS_DIR, "pet_v3")
EYE_CFG_FILE = os.path.join(SPRITE_DIR, "_eye_config.json")
# 眨眼贴片（分层差分）：每个态一张「闭眼」贴片 + 在画布里的矩形。
# 由 tools/build_blink_patches.py 从 ImageGen 的闭眼变体里抠出（alpha = 与睁眼图的 diff 幅度）。
BLINK_DIR = os.path.join(SPRITE_DIR, "blink")
BLINK_MANIFEST = os.path.join(BLINK_DIR, "_patches.json")
# 立绘几何元数据（构建期产出）：内容框 content_box = 角色本体在画布里的归一化矩形。
# 点击分区要用它 —— v3 是统一画布 + 底部对齐，角色上方留空，按「立绘区高度的百分比」
# 分区会让"点头顶判成 face、点眼睛判成 body"（实测 8 态里 7 态错）。
META_FILE = os.path.join(SPRITE_DIR, "_build_meta.json")

# 反应态集合：与立绘态同名，_draw_pet 直接取用（v3 新增 joy / surprise）
REACT_FACES = ("blush", "pout", "stone", "joy", "surprise")

# 配件总开关：2026-09-26 用户要求「帽子（贝雷帽）/小猫/鲸鱼玩偶都不进桌宠」→ 置 True。
# 代码与素材都保留（不删），随时可恢复；菜单项与 persona 表也随之失效。
ACCESSORIES_OFF = True

# ---------- 配色（古见同学主题：制服蓝 / 领结红 / 深紫黑 / 灰紫，对齐古见同学展示页）----------
C_BUBBLE = 0xFFFDFBF6          # 气泡米白底（展示页 --bg #F7F4EE 的亮阶）
C_BUBBLE_LINE = 0xFF39406B     # 气泡描边制服蓝（展示页 --navy）
C_TXT_HEAD = 0xFFB4364F        # 标题领结红（展示页 --crimson 强调色）
C_TXT_MAIN = 0xFF2A2438        # 正文深紫黑（展示页 --ink）
C_TXT_DIM = 0xFF6A6284         # 次级灰紫（展示页 --ink-2）
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


def ok_spec(lay, style=None):
    """OK 态几何规格：比例 → 像素的唯一换算点。

    绘制（_draw_bubble_ok）、测试断言（A12）、预览出图三处共用同一组数字，
    避免"改了一处、另一处还是老数"的漂移。返回 dict：
      椭圆盒 bub_x/bub_y/bub_w/bub_h、图形盒 g_cx/g_cy/g_w/g_h、
      文案 cap_y/cap_font（可能为 None）、进度点 dots_cy/dot_r/dot_gap（可能为 None）
    """
    st = OK_STYLES.get(style or OK_STYLE) or OK_STYLES[OK_STYLE]
    sc, W, bh = lay["sc"], lay["W"], lay["bub_h"]
    by = bh * OK_BUB_TOP_R
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

# ---------- 互动反应：古见同学风格台词库（按点击频率分档）----------
# 古见人设：交流障碍症，几乎不说话，用笔记本/动作/微表情表达，内心戏丰富
QUOTES_SHY = [                       # 1-2 次：害羞（睫毛颤动，耳朵尖红）
    "（睫毛颤了颤）……诶？",
    "（耳朵尖悄悄红了）戳、戳我吗？",
    "（在笔记本上写：吓我一跳）",
    "（僵住三秒，然后轻轻点头）",
]
QUOTES_TSUNDERE = [                  # 3-4 次：紧张（开始石像化，移开视线）
    "（身体开始僵硬）……人、人有点多",
    "（写：请、请轻一点）",
    "（往后缩了半步，长发跟着抖了抖）",
    "（偷偷看你一眼，又飞快移开视线）",
]
QUOTES_POUT = [                      # 5-6 次：傲娇（托腮半眯眼）
    "（托腮，半眯着眼睛看你）",
    "（把笔记本举到你面前：适可而止）",
    "（写：生气了。大概。）",
    "（哼——耳朵不服气地抖了抖）",
]
QUOTES_OUTBURST = [                  # 7+ 次：石化爆发（疯狂摇头，泪目）
    "（疯狂摇头：等等等等等——）",
    "（泪目，浑身僵硬到仿佛发光）",
    "（把笔记本拍到桌上：STOP！）",
    "（抱着头蹲下去：太、太近了啦……）",
]

# ---- 部位点击触发的形态 + 台词（提案 §3：点头部/脸/身体/裙摆）----
#   head: 摸头 → 害羞低头（用 happy 表情 + 飘爱心）
#   face: 戳脸 → 紧张石像化（stone 表情 + 红晕）
#   body: 戳身体 → 普通互动（沿用 _react 四档）
#   skirt: 戳裙摆 → 委屈嘟嘴（pout 表情 + 喷水）
QUOTES_HEAD = [                      # 摸头
    "（微微低头）……这样、这样温柔可以吗",
    "（长发垂下遮住半边脸）……谢谢",
    "（耳朵红透）——请、请不要停太久",
    "（在笔记本上写：被摸头会……没办法思考）",
]
QUOTES_FACE = [                      # 戳脸
    "（僵住三秒）……………………诶？",
    "（石化）……（笔记本掉地上）",
    "（眼睛瞪大，全身僵硬到无法呼吸）",
    "（小声）——那里、不、不能戳……",
]
QUOTES_SKIRT = [                     # 戳裙摆
    "（猛地后退，脸涨通红）——！",
    "（用笔记本挡住裙摆）请、请不要这样……",
    "（眼眶微红）……为什么、要做这种事……",
    "（石像化 + 裙摆被风吹起）——啊、啊！",
]

# 点击身体部位后保持的时长（秒）：让用户看清形态 + 台词，不一闪而过
MORPH_HOLD_S = 4.0
REACT_WINDOW = 5.0                   # 点击计数的滑动窗口（秒）
ANIM_MS = 66                         # 动画帧间隔（~15fps，常驻：呼吸/粒子/漂移/调度）
IDLE_AFTER = float(os.environ.get("WB_WHALE_IDLE_AFTER", "20"))      # 无交互 N 秒后进入自主玩耍
BEHAVIOR_MIN = float(os.environ.get("WB_WHALE_BEHAVIOR_MIN", "6"))   # 自主行为间隔下限
BEHAVIOR_MAX = float(os.environ.get("WB_WHALE_BEHAVIOR_MAX", "14"))  # 上限（随机化防机械感）
GREET_DELAY = float(os.environ.get("WB_WHALE_GREET_DELAY", "1.5"))   # 启动后打招呼延迟

# ---------- Win32 常量 ----------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
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
MA_NOACTIVATE = 3
MK_LBUTTON = 0x0001
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
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
IDM_ACC = 1005                 # 配件层总开关（猫/贝雷帽/鲸鱼玩偶）
IDM_OK_AUTO = 1006             # 完成态自动收起开关（P2：不再只能改 wb_motion 重启）
IDM_LINKED = 1007              # 随 Agent 退出联动关闭开关（P2 同上）
IDM_FOLLOW = 1008              # 跟随前台切换聚焦开关（跟随模式 P1）
IDM_PIN = 1009                 # 锁定聚焦（pin）开关（跟随模式 P3）
IDM_TIMELINE0 = 1400           # 1400：今日时间线摘要项；1401+i ↔ 最近轮次[i]
IDM_FOCUS0 = 1450              # 1450+i ↔ 手动聚焦候选[i]（登记册启用 agent）
IDM_HANDOFF = 1010             # 生成接续摘要 → 剪贴板（跟随模式 P4；仅角标活跃时出现）
ID_HOTKEY_FOLLOW = 1           # 全局热键 id（RegisterHotKey 的 id 命名空间独立于定时器）
IDM_SCALE0 = 1100              # 1100+i ↔ SCALES[i]
SCALES = (0.6, 0.8, 1.0, 1.5, 2.0, 2.5)
IDM_QUALITY0 = 1200            # 1200+i ↔ QUALITY_CHOICES[i]
QUALITY_CHOICES = (
    (MOTION.QUALITY_FULL, "完整"),
    (MOTION.QUALITY_LITE, "精简"),
    (MOTION.QUALITY_OFF, "关闭"),
)
IDM_STYLE0 = 1300               # 1300+i ↔ STYLE_CHOICES[i]（双版本形态风格）
STYLE_CHOICES = (
    ("q",   "Q 版（萌系）"),
    ("alt", "高冷版（清冷）"),
)
ID_TIMER_ANIM = 5              # 反应动画定时器
CLASS_NAME = "WBWhalePetClass"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_msimg32 = ctypes.WinDLL("msimg32", use_last_error=True)
try:
    _gdiplus = ctypes.WinDLL("gdiplus", use_last_error=True)
except OSError:
    _gdiplus = None


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
_user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
_user32.DestroyMenu.argtypes = [wt.HMENU]
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
        _user32.UpdateLayeredWindow(hwnd, hdc_screen, ctypes.byref(dst), ctypes.byref(size),
                                    self.hdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        _user32.ReleaseDC(None, hdc_screen)

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
        self._flip_until = 0.0             # 自主翻身截止
        self._flip_dir = ""
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
        # 双版本立绘：'q' = 古见 Q 版（萌系）/ 'alt' = 古见高冷版（清冷疏离）
        self.style = self._settings.get("style", "q")
        if self.style not in ("q", "alt"):
            self.style = "q"
        # ---- 动效状态（提案 §1 待机 / §2 情绪 / §3 微交互 / §6 降级）----
        self._quality = self._load_quality()
        self._eye_cfg = self._load_eye_config()
        self._breath = 0.0            # 呼吸位移（px，向下为正）
        self._tail_dx = 0.0           # 尾鳍末端水平位移（px）
        self._float_dy = 0.0          # 漂浮位移（px）
        self._shadow_scale = 1.0      # 软阴影随漂浮缩放
        self._blinker = MOTION.BlinkScheduler(time.time(), self._quality)
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
        self._pose_masks = {}         # {sprite_key: 32×48 灰度缩略} 供状态过渡差异判定
        self._pose_diff_cache = {}    # {(k1,k2): 局部最大差异}
        self._pose_wh = (0, 0)
        self._squash = 1.0            # 点击压缩曲线值（1.0 = 常态）
        self._squash_t0 = 0.0
        self._drag_tilt = 0.0         # 拖拽倾斜
        self._emotion = MOTION.EMOTION_NEUTRAL
        self._emotion_until = 0.0
        self._press_at = 0.0          # 按下时刻（长按判定）
        self._frame_ms = 0.0          # 单帧绘制耗时（自动降级用）
        self._slow_frames = 0
        self._degraded_at = 0.0
        self._motion_sig = None       # 动效脏检测签名
        # ---- 互动反应状态 ----
        self._clicks = []                # 立绘点击时间戳（滑动窗口计数）
        self._react_until = 0.0          # 反应（表情）截止时间
        self._react_face = None          # None | "blush" | "pout"
        self._wobble_until = 0.0         # 甩尾摇摆截止时间
        self._wobble_amp = 0.0           # 摇摆幅度（px）
        self._particles = []             # {x,y,vx,vy,life,max,kind,size,phase}
        self._last_quote = ""

        self._sprites = self._load_sprites()
        self._spr_cbox = self._load_sprite_boxes()         # {state: 内容框(归一化)}
        self._blink_patches = self._load_blink_patches()   # {state: (img, (x,y,w,h))}
        self._blink_cache = {}           # 按当前 scale 预缩放的贴片
        self._shown_key = None           # 上一帧实际画出的立绘 key（状态切换渐变用）
        self._fade = None                # {"from":key,"to":key,"t0":ts}
        self._ok_img = self._load_ok_glyph()
        self._ok_glyph_cache = None       # (Surface, w, h) 按当前 scale 预缩放
        self._register_class()
        self.hwnd = None
        self.surf = None
        self._watch_armed = False         # WATCH 定时器是否已启用（按需挂载）
        self._visible = False             # 窗口是否已 ShowWindow
        self._recreate_window(place=True)
        self.tick()
        self._arm_timers()                # 统一挂载定时器（hwnd 变化后需重新挂载）
        # 启动时后台预热时间线：首次右键/首批气泡就不用等（查库 3.6s 全在后台线程）
        self._kick_timeline_refresh()
        self._start_api_guard()           # 看板 API 守护（后台 daemon 线程，不做网络阻塞）
        # P3 全局热键（Ctrl+Alt+F9 手动聚焦轮换）：绑定桌宠 hwnd，
        # WM_HOTKEY 走既有消息泵；组合被占用 → 静默降级（右键菜单仍是兜底）
        self._install_follow_hotkey()
        threading.Thread(target=self._health_loop, daemon=True).start()

        # ---- 配件层：状态对象 + 后台活跃探测（探测绝不放消息循环）----
        self.acc_on = bool(self._settings.get("accessories", True))
        self._acc = None
        self._presence = None
        self._spr_rect = None            # 上一帧立绘矩形（配件定位用）
        self._spr_key = ("idle", "")
        # 活跃探测器**独立于配件开关**：它同时是"联动关闭"的宿主判据
        # （见 _host_alive）。若只在 acc_on 时创建，关掉配件就会让联动关闭退回
        # "只认 WorkBuddy"的老逻辑——那正是本次要解耦的东西。
        if _ACC_OK:
            try:
                self._presence = PRESENCE.PresenceDetector().start()
            except Exception:
                log_exception("[presence] 活跃探测启动失败")
                self._presence = None
        if _ACC_OK and self.acc_on:
            try:
                self._acc = ACC.AccessoryState(scale=self.scale)
            except Exception:
                log_exception("[acc] 配件层初始化失败")
                self._acc = None

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
                         "style": self.style,
                         "accessories": self.acc_on,
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
        self._blinker.quality = q
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
                row = conn.execute(
                    """SELECT COALESCE(NULLIF(user_prompt, ''), ''),
                              COALESCE(NULLIF(title, ''), '')
                         FROM v_turn_total WHERE agent = ?
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

    def _load_eye_config(self):
        """读取立绘面部特征配置（眨眼眼睑 / 腮红贴图定位用）。

        支持两版格式：
          v2（推荐）：{"states": {"idle"/"happy"/"pout": {"eyes": ..., "cheeks": ...}}}
                      —— 按立绘状态分别实测，坐标由 tools/detect_face.py 像素分析生成
          v1（兼容）：{"eyes": {"left": ..., "right": ...}} —— 全状态共用一套
        找不到配置时返回 None → 自动禁用眨眼（不猜坐标，避免画歪）。
        重新生成配置：python tools/detect_face.py --write
        """
        try:
            with open(EYE_CFG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                return None
            states = cfg.get("states")
            if isinstance(states, dict) and states:
                # v2：至少一个状态带完整双眼坐标才算有效
                for st in states.values():
                    eyes = (st or {}).get("eyes") or {}
                    if "left" in eyes and "right" in eyes:
                        return cfg
                return None
            eyes = cfg.get("eyes")
            if eyes and "left" in eyes and "right" in eyes:
                return cfg                              # v1 原样返回
        except Exception:
            pass
        return None

    def _face_cfg(self, state, facing):
        """取当前立绘状态的眼/脸颊归一化坐标；镜像立绘（_f）时水平翻转。

        双版本支持：state 已含版本前缀（'idle' → Q 版，'alt_idle' → 高冷版）。
        返回 {"eyes": {...}, "cheeks": {...}, "eyelid_color": str}；
        无配置返回 None。翻转规则：cx → 1 - cx，左右互换（w/h/cy 不变）。
        """
        cfg = self._eye_cfg
        if not cfg:
            return None
        states = cfg.get("states")
        if isinstance(states, dict) and states:
            # state key 直接查；查不到剥离 alt_ 前缀再查
            st = states.get(state)
            if st is None:
                base = state.removeprefix("alt_") if state.startswith("alt_") else state
                st = states.get(base)
            if not st or st.get("skip"):
                # 2026-09-26 v3：**未标定的态就返回 None，不再回退到 idle**。
                # 回退会把 idle 的眼睑坐标画到别的姿态上（明显错位）；
                # 现在只给显式标定过的态画眼睑，`skip: true` 用于「眼睛本就是闭的」态（joy）。
                return None
            out = {"eyes": st.get("eyes") or {},
                   "cheeks": st.get("cheeks") or {},
                   "eyelid_color": st.get("eyelid_color") or cfg.get("eyelid_color", "#2A2438"),
                   "skin_color": st.get("skin_color") or cfg.get("skin_color", "#FFF2EA")}
        else:
            out = {"eyes": cfg.get("eyes") or {},
                   "cheeks": cfg.get("cheeks") or {},
                   "eyelid_color": cfg.get("eyelid_color", "#2A2438"),
                   "skin_color": cfg.get("skin_color", "#FFF2EA")}
        if facing == "_f":
            flipped = {"eyes": {}, "cheeks": {},
                       "eyelid_color": out["eyelid_color"],
                       "skin_color": out["skin_color"]}
            for group in ("eyes", "cheeks"):
                for key, e in out[group].items():
                    opp = "right" if key == "left" else "left"
                    flipped[group][opp] = {**e, "cx": 1.0 - e["cx"]}
            return flipped
        return out

    # ---- 立绘资源 ----
    def _load_sprites(self):
        """加载 v3 立绘：8 表情 × 正反 = 16 张（统一画布 1191×1627）。

        命名约定：{state} / {state}_f
            state ∈ idle/happy/pout/shy/blush/stone/joy/surprise
        sprite key 形如 'q.idle' / 'q.idle_f'。
        高冷版（alt_*）与配件已停用（2026-09-26 决策：审美线统一到 v3 Q 版）。
        """
        sprites = {}
        states = ("idle", "happy", "pout", "shy", "blush", "stone", "joy", "surprise")
        for state in states:
            for suffix, mirror in (("", False), ("_f", True)):
                key = f"q.{state}{suffix}"
                path = os.path.join(SPRITE_DIR, f"pet_{state}{suffix}.png")
                if not os.path.isfile(path):
                    continue
                img = P()
                if _LoadImage(path, ctypes.byref(img)) != 0 or not img:
                    log_exception(f"[sprite] 加载失败 {path}")
                    continue
                w, h = U32(0), U32(0)
                _GetImageW(img, ctypes.byref(w))
                _GetImageH(img, ctypes.byref(h))
                sprites[key] = (img, w.value, h.value)
        if "q.idle" not in sprites:
            log_exception(f"[sprite] 未找到 v3 立绘目录 {SPRITE_DIR}"
                          f"（请先跑 tools/build_pet_v3.py）")
        # 姿态掩码（构建期由 tools/build_pose_masks.py 生成）：状态过渡差异判定用
        self._pose_masks = {}
        self._pose_wh = (0, 0)
        try:
            with open(os.path.join(SPRITE_DIR, "_pose_masks.json"), encoding="utf-8") as fp:
                pm = json.load(fp)
            self._pose_masks = pm.get("states") or {}
            self._pose_wh = (int(pm.get("w") or 0), int(pm.get("h") or 0))
        except Exception:
            pass          # 缺文件不报错：_pose_local_diff 会退回"不限制"的原行为
        return sprites

    def _load_sprite_boxes(self):
        """读各态立绘的**内容框**（角色本体在画布里的归一化矩形），点击分区要用。

        为什么离线算好再读：2D 运行时**刻意零依赖**（不 import numpy / PIL），
        没法在运行时对位图求 bbox。构建期（tools/build_pet_v3.py 有 numpy）算进
        `_build_meta.json` 即可。
        """
        out = {}
        if not os.path.isfile(META_FILE):
            log_exception(f"[sprite] 未找到几何元数据 {META_FILE}"
                          f"（点击分区会退回按立绘区百分比估算）")
            return out
        try:
            with open(META_FILE, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            log_exception(f"[sprite] 几何元数据解析失败 {META_FILE}")
            return out
        for state, ent in meta.items():
            b = ent.get("content_box")
            if b and len(b) == 4:
                out[state] = tuple(float(v) for v in b)
        return out

    def _load_blink_patches(self):
        """加载分层差分的**闭眼贴片**（每态一组「半闭档」+ 它在画布里的矩形）。

        贴片由 `tools/build_blink_patches.py` 生成：拿 ImageGen 的「只闭眼」变体与睁眼立绘
        做像素差分，**diff 区域就是眼睛**（所以既不用重新标定眼位，也不用猜），
        贴片 alpha 取自 diff 幅度。

        为什么要多档而不是一张图配全局 alpha：半闭**不是两张图混合**——那样睁眼的暗瞳会
        透过半透明闭眼图显出来（实测 ratio=0.5 重影明显）。真实半闭是**上睑从上往下压住眼球**，
        所以离线按「眼睑下落位置」做出 N 档垂直遮罩，运行时按闭合度取档。

        缺贴片的态自动退回旧的椭圆眼睑盖板（见 `_draw_blink`），不会因为没素材而坏掉。
        """
        out = {}
        if not os.path.isfile(BLINK_MANIFEST):
            log_exception(f"[blink] 未找到贴片清单 {BLINK_MANIFEST}"
                          f"（可跑 tools/build_blink_patches.py；缺失时回退椭圆盖板）")
            return out
        try:
            with open(BLINK_MANIFEST, encoding="utf-8") as f:
                man = json.load(f)
        except Exception:
            log_exception(f"[blink] 贴片清单解析失败 {BLINK_MANIFEST}")
            return out

        def _load(p):
            img = P()
            if _LoadImage(p, ctypes.byref(img)) != 0 or not img:
                return None
            return img

        for state, ent in man.items():
            slots = []
            n = int(ent.get("slots") or 0)
            for i in range(1, n + 1):
                g = _load(os.path.join(BLINK_DIR, f"{state}_c{i}.png"))
                if g is not None:
                    slots.append(g)
            if not slots:                       # 只有整张贴片时退化为单档
                g = _load(os.path.join(BLINK_DIR, f"{state}.png"))
                if g is not None:
                    slots.append(g)
            if not slots:
                continue
            out[state] = {"slots": slots,
                          "rect": tuple(ent.get("rect") or (0, 0, 0, 0)),
                          "canvas": tuple(ent.get("canvas") or (0, 0))}
        return out

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
        # 配件净空：插在气泡区与立绘区之间，供贝雷帽占用头顶上方的空间（见 wb_motion）。
        # 配件停用后这块就是纯空白（气泡与角色之间一条 58px 的缝），故归零。
        hr = 0 if ACCESSORIES_OFF else int(MOTION.ACC_HEADROOM * sc)
        pet_h = int(BASE_PET_H * sc)
        return {"W": int(BASE_W * sc), "H": bubble_h + hr + pet_h,
                "bub_h": bub_h, "bubble_h": bubble_h, "hr": hr,
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
        self._build_sprite_cache()              # 按新尺寸预缩放缓存
        if place:
            self._place()
        self._drawn_sig = None
        self.draw()
        if not self._visible:
            _user32.ShowWindow(self.hwnd, SW_SHOWNA)
            self._visible = True
        self._arm_timers()

    def _arm_timers(self):
        """把定时器挂到当前 hwnd 上。SetTimer 同 ID 会原地更新，可安全重复调用。"""
        if not self.hwnd:
            return
        _user32.SetTimer(self.hwnd, ID_TIMER_TICK, TICK_MS, None)
        # 常驻动画帧：呼吸浮动 / 粒子 / 摇摆 / 漂移 / 鼠标跟随 / 自主行为调度
        _user32.SetTimer(self.hwnd, ID_TIMER_ANIM, ANIM_MS, None)
        if self._watch_armed:
            _user32.SetTimer(self.hwnd, ID_TIMER_WATCH, WATCH_MS, None)

    def _arm_watch(self):
        """台词期间用：外部点击检测（按需挂载，记住状态以便 hwnd 变化后恢复）。"""
        self._watch_armed = True
        if self.hwnd:
            _user32.SetTimer(self.hwnd, ID_TIMER_WATCH, WATCH_MS, None)

    # ---- 立绘预缩放缓存（每帧 AlphaBlend 1:1 合成，避免每帧重采样大图）----
    def _build_sprite_cache(self):
        for tmp in getattr(self, "_cache_surfs", []):
            try:
                tmp.close()
            except Exception:
                pass
        self._sprite_cache = {}
        self._cache_surfs = []
        if not getattr(self, "surf", None):
            return
        lay = self._layout()
        ph = lay["pet_h"] - 8 * lay["sc"]
        for key, (img, iw, ih) in self._sprites.items():
            pw, phh = int(ph * iw / ih), int(ph)
            if pw <= 0 or phh <= 0:
                continue
            tmp = Surface(pw, phh)
            tmp.clear()
            tmp.image(img, 0, 0, pw, phh)      # 一次性重采样到显示尺寸
            self._sprite_cache[key] = (tmp, pw, phh)
            self._cache_surfs.append(tmp)
        # OK 图形同样按「当前尺寸 + 当前方案」预缩放（分层窗口要 1:1 合成，省掉每帧重采样）
        self._ok_glyph_cache = None
        if getattr(self, "_ok_img", None):
            img, iw, ih = self._ok_img
            gh = max(1, int(round(ok_spec(lay)["g_h"])))
            gw = max(1, int(round(gh * iw / ih)))
            tmp = Surface(gw, gh)
            tmp.clear()
            tmp.image(img, 0, 0, gw, gh)
            self._ok_glyph_cache = (tmp, gw, gh)
            self._cache_surfs.append(tmp)

        # 眨眼贴片同样预缩放到显示尺寸：贴片坐标在「画布坐标系」里，而立绘按画布高 ph 绘制，
        # 所以换算系数 k = ph / 画布高。每帧只做一次 1:1 AlphaBlend（带全局 alpha）。
        self._blink_cache = {}
        for state, ent in getattr(self, "_blink_patches", {}).items():
            rect = ent["rect"]; cw, ch = ent["canvas"]
            if cw <= 0 or ch <= 0 or rect[2] <= 0 or rect[3] <= 0:
                continue
            k = ph / ch
            pw2 = max(1, round(rect[2] * k))
            ph2 = max(1, round(rect[3] * k))
            x_disp = round(rect[0] * k)
            y_disp = round(rect[1] * k)
            # 贴片矩形在画布坐标系里；镜像立绘（_f）时左右翻转 → 预算一份镜像 x
            x_mirror = round((cw - (rect[0] + rect[2])) * k)
            scaled = []
            for g in ent["slots"]:
                tmp = Surface(pw2, ph2)
                tmp.clear()
                tmp.image(g, 0, 0, pw2, ph2)
                scaled.append(tmp)
                self._cache_surfs.append(tmp)
            self._blink_cache[state] = {"slots": scaled, "x": x_disp, "y": y_disp,
                                        "w": pw2, "h": ph2, "x_mirror": x_mirror}

    def _place(self):
        lay = self._layout()
        ax, ay, aw, ah = work_area()
        saved = load_pos(POS_FILE)
        if saved:
            x, y = saved
        else:
            x, y = ax + aw - lay["W"] - 20, ay + ah - lay["H"] - 16
        x = max(ax + 4, min(x, ax + aw - lay["W"] - 4))
        y = max(ay + 4, min(y, ay + ah - lay["H"] - 4))
        self._move_window(self.hwnd, int(x), int(y))
        self._home = (int(x), int(y))

    def _move_window(self, hwnd, x, y):
        _user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), x, y, 0, 0,
                             SWP_NOSIZE | SWP_NOACTIVATE)

    def _window_xy(self, hwnd):
        r = RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top

    def _snap(self, x, y):
        """贴边吸附：松手距边缘 ≤ DRAG_EDGE 则贴边（含四角组合）。"""
        lay = self._layout()
        ax, ay, aw, ah = work_area()
        nx, ny = x, y
        if x - ax <= DRAG_EDGE:
            nx = ax
        elif ax + aw - (x + lay["W"]) <= DRAG_EDGE:
            nx = ax + aw - lay["W"]
        if y - ay <= DRAG_EDGE:
            ny = ay
        elif ay + ah - (y + lay["H"]) <= DRAG_EDGE:
            ny = ay + ah - lay["H"]
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
        - row1 标题 = "正在对话"（workbuddy.db 标记 working）或 "最近对话"（仅有 ODS 残留 turn）
        - row2 数据 = self.latest_turn（ODS 最近有数据的 turn，不依赖 working 状态）
          用时算到 now（live 模式，每 tick 重绘 +1s），保证用户能看到对话正在思考/已结束
        - row3 副标 = 活跃时间 + 双击开看板
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
            row1, row2, row3 = ("数据源读取中…", "等待 WorkBuddy 会话记录",
                                "双击打开完整看板")
            return row1, row2, row3
        else:
            row1, row2, row3 = ("当前没有活跃会话", "双击打开看板查看今日用量",
                                "数据每秒自动刷新")
            return row1, row2, row3

        # ---- row2 数据：用 live（最近有数据 turn），不用 working 等待态 ----
        if live:
            credit = live.get("credit") or 0
            tokens = fmt_tokens(live.get("total_tokens") or 0)
            # live 模式：用时算到 now（workbuddy 标记 working 时）或 last_ts（仅残留 turn 时）
            if working_turn:
                dur = fmt_duration_live(live)            # first_ts → now，每 tick 自动 +1
            else:
                dur = fmt_duration(live)                # first_ts → last_ts，最终耗时
            row2 = (f"本轮 {credit:.1f} 分 · {tokens} tok · 用时 {dur}")
        else:
            # working 但 ODS 真的没数据（新对话刚开、第一笔调用未到达）——
            # 不显示 0（之前 bug），直接给"等待下一笔"
            row2 = "本轮等待首笔调用…"

        # ---- row3 副标：P2 连续锚点 —— 跨 agent 叙述「今天：Codex 3 轮 · WorkBuddy 12 轮」
        # （有数据用叙述；无数据回退"活跃 X 分钟前"；不变的是双击提示）
        tl_summary, _tl_recent = self._today_timeline()
        tail = " · 双击开看板"
        if tl_summary:
            row3 = "今天：" + format_timeline_summary(tl_summary) + tail
        else:
            if working_turn:
                ago = fmt_ago(working_turn.get("_last_act")
                              or working_turn.get("last_ts") or 0)
            elif live:
                ago = fmt_ago(live.get("last_ts") or 0)
            else:
                ago = "-"
            proj = ((live or {}).get("project") or "").strip()
            row3 = f"活跃 {ago}" + (f" · {proj}" if proj else "") + tail
        return row1, row2, row3

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
            self._spawn_particles("spark", 6, lay["W"] / 2, lay["bub_h"] * 0.45)
        else:
            self._ok_anim_dur = MOTION.OK_POP_OUT_S
            self._ok_anim_from = 1.0
            if sound:
                self._play("pop")
        self._drawn_sig = None
        self._motion_sig = None
        self._report_event("bubble_mode", detail=mode)

    def _sync_bubble_mode(self):
        """用「运行中会话数」的迁移驱动形态切换（每 tick 一次）。

          prev > 0 → n == 0   一轮对话任务结束        → OK
          n > 0 且当前是 OK                            → 回数据态
                              （新任务在跑却挂"完成"会误导，必须让位）
        首次取样只建基线（prev is None），不据它触发——否则启动瞬间就会误判。
        """
        n = len(self.active)
        prev = self._prev_active_n
        self._prev_active_n = n
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
        ——零额外开销，且与配件层用的是**同一份判定**，不会两边口径不一。

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
        self._wb_gone_checks += 1
        if (self._wb_gone_checks >= MOTION.LINKED_CLOSE_CONFIRM_CHECKS
                and now - self._wb_gone_since >= MOTION.LINKED_CLOSE_GRACE_S):
            self._linked_quit()

    def _linked_quit(self):
        """联动关闭：关窗口 → WM_DESTROY → PostQuitMessage → 进程退出。"""
        if self._linked_closed:
            return
        self._linked_closed = True
        self._report_event("linked_close", detail="workbuddy_exit")
        try:
            _user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        except Exception:
            try:
                _user32.PostQuitMessage(0)
            except Exception:
                pass

    # ---- 互动反应（傲娇四档）----
    def _play(self, key):
        if not (self.sound_on and key in self._sounds):
            return
        try:
            winsound.PlaySound(self._sounds[key],
                               winsound.SND_MEMORY | winsound.SND_ASYNC)
        except Exception:
            pass

    def _react(self):
        """点立绘触发：按 4s 窗口内点击次数分档（害羞→傲娇→嘟嘴→爆发喷水）。"""
        now = time.time()
        self._clicks = [t for t in self._clicks if now - t < REACT_WINDOW]
        self._clicks.append(now)
        n = len(self._clicks)
        if n <= 2:
            tier, pool, face, snd = 1, QUOTES_SHY, "blush", "pop"
            hearts, spray, wamp, wdur, rdur = 3, 0, 0.0, 0.0, 3.0
        elif n <= 4:
            tier, pool, face, snd = 2, QUOTES_TSUNDERE, "blush", "chirp"
            hearts, spray, wamp, wdur, rdur = 5, 0, 4.0, 0.5, 3.2
        elif n <= 6:
            tier, pool, face, snd = 3, QUOTES_POUT, "pout", "hmph"
            hearts, spray, wamp, wdur, rdur = 0, 0, 6.0, 0.7, 3.2
        else:
            # 2026-09-26 v3：第四档 face 由 "pout" 改为 "stone"——它的文案标签本来就写着
            # 「石化」，旧值与标签自相矛盾（改后 stone 态也有了稳定触发入口）
            tier, pool, face, snd = 4, QUOTES_OUTBURST, "stone", "splash"
            hearts, spray, wamp, wdur, rdur = 0, 12, 7.0, 0.9, 3.5
        q = random.choice(pool)
        while q == self._last_quote and len(pool) > 1:
            q = random.choice(pool)
        self._last_quote = q
        self._quote = q
        self._quote_dur = f"{ {1:'害羞',2:'紧张',3:'傲娇',4:'石化'}[tier] } · 第 {n} 次"
        self._talk_until = now + rdur
        self._react_until = now + rdur
        self._react_face = face
        if wdur:
            self._wobble_until = now + wdur
            self._wobble_amp = wamp * self.scale
            self._wobble_dur = wdur
        lay = self._layout()
        cx = lay["W"] / 2
        head_y = lay["bubble_h"] + lay["pet_h"] * 0.16
        if hearts:
            self._spawn_particles("heart", hearts, cx, head_y + lay["pet_h"] * 0.2)
        if spray:
            self._spawn_particles("drop", spray, cx, head_y)
        self._play(snd)
        self._arm_watch()
        self._report_event(f"react_tier{tier}", detail=f"n={n}")
        self._drawn_sig = None

    def _spawn_particles(self, kind, count, cx, cy):
        for _ in range(count):
            if kind == "heart":
                vx, vy = random.uniform(-45, 45), random.uniform(-120, -60)
                size, life = random.uniform(4, 7) * self.scale, random.uniform(0.8, 1.2)
            elif kind == "bubble":
                vx, vy = random.uniform(-12, 12), random.uniform(-75, -45)
                size, life = random.uniform(4, 8) * self.scale, random.uniform(1.6, 2.4)
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
        now = time.time()
        dt = ANIM_MS / 1000.0
        self._tick_accessories(now, dt)
        # ① 粒子物理
        alive = []
        for p in self._particles:
            p["life"] -= dt
            if p["life"] <= 0:
                continue
            if p["kind"] == "heart":
                p["vy"] += 60 * dt               # 爱心轻飘
            elif p["kind"] == "bubble":
                p["vx"] += math.sin(now * 5 + p["phase"]) * 14 * dt   # 左右摇曳上浮
            else:
                p["vy"] += 520 * dt              # 水滴重力
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            alive.append(p)
        self._particles = alive
        if now > self._react_until:
            self._react_face = None
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
                and not self._dragging and now > self._react_until):
            self._behavior()
        # ⑤ 视线 / 悬停：由光标位置推导（提案 §3）
        self._update_pointer_state()
        # ⑤ 提案 §1 待机三件套 + §3 微交互 + §2 情绪：统一计算本帧的位移量
        self._update_motion(now)
        # ⑥ 脏检测：把全部动效量化后拼成签名，只有签名变化才重绘（省电关键）
        facing = self._current_facing(now)
        sig = self._sig()
        msig = self._motion_signature(now, facing)
        dirty = (bool(self._particles) or self._drift is not None
                 or now < self._wobble_until or now < self._react_until
                 or self._ok_animating(now)          # 气泡形态切换 / 点击脉冲 / 完成保持
                 or self._fade is not None           # ★ 状态切换过渡期间必须每帧重绘
                 or msig != self._motion_sig
                 or facing != getattr(self, "_facing_drawn", None)
                 or sig != self._drawn_sig)
        if dirty:
            self._facing_drawn = facing
            self._motion_sig = msig
            self.draw()
            self._drawn_sig = sig
            # 提案 §6：单帧绘制过慢连续 N 帧 → 自动降一档
            self._auto_degrade(now)

    # ---- 提案 §3：点击身体部位切形态 ----
    def _face_metrics(self, state, cbox):
        """把眼部锚点换算成「内容框内的相对坐标」，供点击分区用。

        返回 (ey, eh, ux0, ux1)：眼心在角色高度上的位置、眼高、脸列（左右眼外扩 35%）。
        无锚点的态（如 joy，眼睛本就是闭的）返回 None，调用方走兜底比例。
        """
        fc = self._face_cfg(state, "")          # 用未镜像锚点：调用方已把坐标统一回未镜像系
        eyes = (fc or {}).get("eyes") or {}
        if len(eyes) < 2:
            return None
        vals = list(eyes.values())
        cy = sum(v["cy"] for v in vals) / len(vals)
        h = sum(v["h"] for v in vals) / len(vals)
        left = min(v["cx"] - v["w"] / 2 for v in vals)
        right = max(v["cx"] + v["w"] / 2 for v in vals)
        x0, y0, x1, y1 = cbox
        cw, ch = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
        mid = (left + right) / 2
        half = (right - left) * 0.85            # 眼跨 + 两侧外扩
        # 脸列宽度上限 = 角色宽度的 62%：紧凑趴姿的眼跨占身体比例极大，不夹的话
        # 整条身体都会被判成 face，body 区消失（blush/stone 实测 body 只剩 1%）
        half = min(half, 0.31 * cw)
        return ((cy - y0) / ch, h / ch,
                max(0.0, (mid - half - x0) / cw), min(1.0, (mid + half - x0) / cw))

    def _body_region(self, x, y, lay):
        """将立绘点击坐标映射到 4 个部位之一：'head' / 'face' / 'body' / 'skirt'。

        2026-09-26 重写：旧实现按「立绘区高度的百分比」分区，前提是**立绘填满立绘区**。
        v3 立绘是统一画布 + 底部对齐（角色上方留空，各态内容高只占画布 50%~95%），
        那个前提不成立 —— 实测 8 态里 7 态错位：**点头顶判成 face、点眼睛判成 body**，
        导致「摸头→joy」「戳脸→surprise」两个交互根本点不出来。

        现在按**真实几何**分区：立绘实际绘制矩形（self._spr_rect，每帧记录）→
        换算到画布归一化坐标 → 与该态的内容框、眼部锚点比对。
        拿不到矩形时（首帧之前）退回旧口径，保证不会因为顺序问题崩。
        """
        rect = getattr(self, "_spr_rect", None)
        key = getattr(self, "_spr_key", None)
        if rect and key:
            state, facing = key
            cbox = getattr(self, "_spr_cbox", {}).get(state)
            if cbox:
                sx, sy, sw, sh = rect
                # 点击点 → 画布归一化坐标；镜像立绘翻回未镜像系（与内容框、锚点一致）
                cx = (x - sx) / max(1.0, sw)
                cy = (y - sy) / max(1.0, sh)
                if facing == "_f":
                    cx = 1.0 - cx
                x0, y0, x1, y1 = cbox
                # ⚠️ 边界判定必须留**亚像素容差**：点击坐标来自 int() 取整（鼠标/测试都一样），
                # 而取整会向下截断最多 1px。若某态内容框的上边缘离采样行不足 1px（实测 stone
                # 只有约 0.7px），"点头发最顶上那一两个像素"就会被判成框外 → 返回 body，
                # 表现为「点头顶没反应/反应不对」（test_hit_regions 的 stone 用例就是这么挂的）。
                eps_x = 1.5 / max(1.0, sw)
                eps_y = 1.5 / max(1.0, sh)
                if not (x0 - eps_x <= cx <= x1 + eps_x and y0 - eps_y <= cy <= y1 + eps_y):
                    return "body"           # 落在角色本体之外（画布留白，通常已被穿透）
                ux = min(1.0, max(0.0, (cx - x0) / max(1e-6, x1 - x0)))   # 钳制：容差区按边界算
                uy = min(1.0, max(0.0, (cy - y0) / max(1e-6, y1 - y0)))
                fm = self._face_metrics(state, cbox)
                if fm:
                    ey, eh, ux0, ux1 = fm
                    if ux0 <= ux <= ux1 and (ey - 0.60 * eh) < uy <= (ey + 1.40 * eh):
                        return "face"       # 眼→嘴那一段，且限定在脸的窄列里
                    if uy <= max(0.35, min(0.62, ey + 1.60 * eh)):
                        return "head"       # 眼线以上：头发/刘海/额头
                else:
                    if uy <= 0.20:
                        return "head"
                    if 0.20 <= uy <= 0.45 and 0.35 <= ux <= 0.65:
                        return "face"
                if uy >= 0.78:
                    return "skirt"          # 裙摆/膝
                return "body"
        # ---- 兜底：拿不到立绘矩形时按旧口径 ----
        ph = lay["pet_h"]
        bh = lay["bubble_h"]
        if not (bh <= y <= bh + ph):
            return "body"
        ry = (y - bh) / max(1.0, ph)
        rw = x / max(1.0, lay["W"])
        if 0.00 <= ry <= 0.18:
            return "head"
        if 0.18 <= ry <= 0.45 and 0.35 <= rw <= 0.65:
            return "face"
        if ry >= 0.78:
            return "skirt"
        return "body"

    def _morph_by_region(self, region):
        """根据点击部位切形态 + 写人设台词（摸头害羞/戳脸石化/戳身互动/戳裙委屈）。

        形态保持 MORPH_HOLD_S 秒后自动消失，期间 _draw_pet 优先用此形态。
        """
        now = time.time()
        if region in ("head", "face"):
            # 交互级 §三.2：点击头部/脸部 → 受惊连眨（快速双连眨）
            self._blinker.startle(time.time())
        if region == "head":
            # 2026-09-26 v3：摸头顶 → joy（比耶开心），替代旧的 blush；
            # blush 仍由 _react 一/二档（戳身）与打招呼触发，不会失联
            pool, face, snd, state = QUOTES_HEAD, "joy", "chirp", "joy"
            hearts = 3; spray = 0; wamp = 0.0; wdur = 0.0; rdur = 0.0
            self._react_until = now + MORPH_HOLD_S   # 抑制 _react 重入
        elif region == "face":
            # 2026-09-26 v3：戳脸 → surprise（睁大眼+张嘴，被戳一下愣住）；
            # 旧映射是 stone（石化），现由 _react 第四档承接（其标签本就叫"石化"）
            pool, face, snd, state = QUOTES_FACE, "surprise", "splash", "surprise"
            hearts = 0; spray = 0; wamp = 6.0; wdur = 0.6; rdur = MORPH_HOLD_S
            self._react_until = now + MORPH_HOLD_S
        elif region == "skirt":
            pool, face, snd, state = QUOTES_SKIRT, "pout", "hmph", "pout"
            hearts = 0; spray = 6; wamp = 5.0; wdur = 0.7; rdur = MORPH_HOLD_S
            self._react_until = now + MORPH_HOLD_S
        else:
            # 'body'：保持原 _react 四档逻辑（4s 内点击次数分档）
            self._react()
            return
        q = random.choice(pool)
        while q == self._last_quote and len(pool) > 1:
            q = random.choice(pool)
        self._last_quote = q
        self._quote = q
        # 文案标签：摸头→开心 / 戳脸→愣住 / 戳裙→委屈
        labels = {"head": "开心", "face": "愣住", "skirt": "委屈"}
        self._quote_dur = labels.get(region, region)
        self._talk_until = now + MORPH_HOLD_S
        self._react_face = face
        if wdur:
            self._wobble_until = now + wdur
            self._wobble_amp = wamp * self.scale
            self._wobble_dur = wdur
        lay = self._layout()
        cx = lay["W"] / 2
        head_y = lay["bubble_h"] + lay["pet_h"] * 0.16
        if hearts:
            self._spawn_particles("heart", hearts, cx, head_y + lay["pet_h"] * 0.2)
        if spray:
            self._spawn_particles("drop", spray, cx, head_y)
        self._play(snd)
        self._arm_watch()
        self._report_event(f"morph_{region}", detail=q)
        # 关键：设临时形态，让 _draw_pet 在 MORPH_HOLD_S 内优先用此 sprite
        self._morph_state = state
        self._morph_until = now + MORPH_HOLD_S
        self._drawn_sig = None
        self._motion_sig = None

    # ---- 提案 §1：待机动效与微交互的逐帧计算 ----
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

    def _update_pointer_state(self):
        """光标位置 → 悬停态 + 头部朝向（提案 §3）。

        放在动画帧里统一算（而不是 WM_MOUSEMOVE），分层窗口不依赖鼠标离开消息也能正确复位。
        """
        inside, dx_ratio = False, 0.0
        try:
            pt = POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            x, y = self._window_xy(self.hwnd)
            lay = self._layout()
            if x <= pt.x <= x + lay["W"] and y <= pt.y <= y + lay["H"]:
                inside = True
                dx_ratio = (pt.x - (x + lay["W"] / 2)) / max(1.0, lay["W"] / 2)
        except Exception:
            inside = False
        self._hovering = inside
        if inside:
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

        # ④ 眨眼状态机 v2（精简/完整档才有）——三层标准：
        #    注视（悬停）/工作中 → 8~12s 注视式慢眨；夜间 → 频率降 + 半眯眼；
        #    常态 3~8s 完全随机；拖拽/被端详/反应动画禁眨；久无人偶发慢眨
        if q != MOTION.QUALITY_OFF:
            bl = self._blinker
            bl.attention = self._hovering          # 鼠标注视联动（§三.1）
            bl.busy = bool(self.active)            # 工作中（会话 running）→ 专注态
            bl.night = self._night_mode()          # 夜间联动（§三.4）
            bl.suppressed = self._blink_vetoed(now)
            bl.idle = idle_long
            bl.tick(now)
            # §一.2 眨眼不是孤立运动：伴随 1~2px 头部微点（nod 包络随眨眼起伏）
            self._breath += self._blinker.nod(now) * MOTION.BLINK_NOD_PX
        else:
            self._blinker.closed_until = self._blinker.open_until = 0.0

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

    def _blink_vetoed(self, now):
        """眨眼否决（这些时刻闭眼是 bug，进行中的眨眼由调度器跳睁开段收尾）：
        - 拖拽 / 被端详（悬停）：用户在端详她时闭眼是缺陷
        - 按压及其回弹（squash 曲线全程）
        - OK 气泡弹入/弹出动画期间（眨眼永不与反应动画同期）
        - 成功/欢迎情绪：眨眼让位给眯眼笑，不叠加
        """
        if self._dragging or self._hovering:
            return True
        if self._pressed or self._squash_t0:
            return True
        if self._bub_mode == BUBBLE_OK and self._ok_anim_dur \
                and now < self._ok_anim_t0 + self._ok_anim_dur:
            return True
        if self._emotion in (MOTION.EMOTION_SUCCESS, MOTION.EMOTION_WELCOME):
            return True
        return False

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

    def _motion_signature(self, now, facing):
        """把本帧所有动效量化成可比较的元组（脏检测用）。"""
        return (
            int(round(self._breath)),
            int(round(self._float_dy)),
            int(round(self._tail_dx * 2)),
            int(round(self._gaze_dx * 2)),
            int(round(self._hover_t * 12)),
            int(round(self._squash * 100)),
            int(round(self._drag_tilt)),
            # 眨眼态：量化到 4 级（≈15fps 的 2–4 帧）——部分眨眼最低只闭到 30–60%，
            # 按 >0.5 判定会整段漏掉重绘，眼皮动了画面却不动
            int(round(self._blinker.eye_opening_ratio(now) * 3)),   # 眨眼态
            self._emotion,
            facing,
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

    def _emotion_rise(self):
        """当前情绪带来的身体上下偏置（px，正=上抬）。"""
        return MOTION.EMOTION_RISE_PX.get(self._emotion, 0.0) * self.scale

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
        self._react_until = time.time() + 2.5
        self._react_face = "blush"
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
        """空闲自主玩耍：随机吐泡泡 / 游动 / 翻身 / 换表情 / 扭扭。"""
        now = time.time()
        self._next_behavior = now + random.uniform(BEHAVIOR_MIN, BEHAVIOR_MAX)
        lay = self._layout()
        choice = random.choice(["bubble", "swim", "flip", "emote", "wiggle"])
        self._report_event("behavior", detail=choice)
        if choice == "bubble":                   # 吐泡泡（嘴边升起空心泡）
            mouth_x = lay["W"] / 2 + (22 if self._current_facing(now) else -22) * self.scale
            mouth_y = lay["bubble_h"] + lay["pet_h"] * 0.45
            self._spawn_particles("bubble", random.randint(2, 4), mouth_x, mouth_y)
            if self.bubble_on and random.random() < 0.3:
                self._quote = random.choice(["咕噜咕噜……", "噗噜噗噜~", "吐个泡泡玩玩"])
                self._quote_dur = "玩耍中"
                self._talk_until = now + 2.5
                self._arm_watch()
        elif choice == "swim":                   # 随机游动（窗口小幅漂移，稍后漂回）
            dx = random.choice([-1, 1]) * random.uniform(20, 50)
            dy = random.uniform(-24, 24)
            self._start_drift(dx, dy, dur=random.uniform(1.4, 2.0))
        elif choice == "flip":                   # 翻身（朝向翻转 3 秒）
            self._flip_dir = "_f" if not self._current_facing(now) else ""
            self._flip_until = now + 3.0
            self._wobble_until = now + 0.5
            self._wobble_amp = 3.0 * self.scale
            self._wobble_dur = 0.5
        elif choice == "emote":                  # 变换表情（脸红/开心一小会儿）
            self._react_face = "blush"
            self._react_until = now + 2.0
            if self.bubble_on and random.random() < 0.4:
                self._quote = random.choice(["盯——你在干嘛呀？", "嘿嘿，心情不错",
                                             "今天的用量也很健康哦"])
                self._quote_dur = "自言自语"
                self._talk_until = now + 2.5
                self._arm_watch()
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

    def _current_facing(self, now):
        """朝向决策：自主翻身覆盖 > 鼠标跟随(0.4s 防抖) > 保持。"""
        if now < self._flip_until:
            return self._flip_dir
        if self._dragging:
            return self._facing
        lay = self._layout()
        pt = POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        wx, _ = self._window_xy(self.hwnd)
        off = pt.x - (wx + lay["W"] / 2)
        if abs(off) > 60:                        # 鼠标明显偏向一侧 → 面朝鼠标
            d = "_f" if off > 0 else ""
            if d != self._facing:
                if self._follow_dir != d:
                    self._follow_dir = d
                    self._follow_since = now
                elif now - self._follow_since > 0.4:
                    self._facing = d
        else:
            self._follow_dir = None
            self._follow_since = 0.0
        return self._facing

    # ---- 绘制 ----
    def draw(self):
        s = self.surf
        s.clear()
        lay = self._layout()
        now = time.time()
        if self.bubble_on:
            self._draw_bubble(s, lay, now)
        self._draw_pet(s, lay)
        # 配件统一画在立绘**之上**：
        #   · 贝雷帽/玩偶必须在身体前面（穿戴与抱持）
        #   · 小黑猫贴地线走过——从她脚前经过是自然的；若画在身后，
        #     chibi 立绘太宽（占窗口 65%）会把猫整个挡住，等于消失
        self._draw_accessories(s, lay, now)
        s.present(self.hwnd)

    def _draw_thought_circles(self, s, lay, line_argb):
        """想法小圆：沿「主气泡底部 → 立绘头顶」对角线由大到小排列。

        漫画思考泡惯例：圆从大到小指向源头，给眼睛一条"从头顶冒出"的引导线。
        数据态与 OK 态共用同一套几何，只换描边色 —— 形态切换时小圆不跳位。
        """
        sc = lay["sc"]
        W = lay["W"]
        bx, by, bw, bh = 6 * sc, 4 * sc, W - 12 * sc, lay["bub_h"]
        p0x = W / 2 - 22 * sc                       # 起点：主气泡底偏左
        p0y = by + bh + 4 * sc                      # 距主气泡下沿 4px
        p1x = W / 2 - 4 * sc                        # 终点：头顶偏中
        p1y = lay["bubble_h"] + 5 * sc              # 立绘头发最上沿上方 5px
        c1r, c2r = 7.5 * sc, 4.5 * sc               # 大→小，靠近主气泡的更大
        c1x = p0x + (p1x - p0x) * 0.30              # 30% 处（近主气泡端）
        c1y = p0y + (p1y - p0y) * 0.30
        c2x = p0x + (p1x - p0x) * 0.78              # 78% 处（近头顶端，最小）
        c2y = p0y + (p1y - p0y) * 0.78
        s.ellipse(C_BUBBLE, c1x - c1r, c1y - c1r, c1r * 2, c1r * 2,
                  line_argb=line_argb, line_w=3.0 * sc)
        s.ellipse(C_BUBBLE, c2x - c2r, c2y - c2r, c2r * 2, c2r * 2,
                  line_argb=line_argb, line_w=2.4 * sc)

    def _draw_bubble(self, s, lay, now):
        """气泡分形态绘制：OK 态走 _draw_bubble_ok，其余走数据态三行文案。"""
        if self._bub_mode == BUBBLE_OK or self._ok_animating(now):
            self._draw_bubble_ok(s, lay, now)
            return
        sc = lay["sc"]
        W = lay["W"]
        bx, by, bw, bh = 6 * sc, 4 * sc, W - 12 * sc, lay["bub_h"]
        # 主椭圆（白底 + 藏蓝描边）
        s.ellipse(C_BUBBLE, bx, by, bw, bh, line_argb=C_BUBBLE_LINE, line_w=3.5 * sc)
        self._draw_thought_circles(s, lay, C_BUBBLE_LINE)
        # 文案（藏蓝粗体，垂直居中三段）
        if self._in_talk():
            lines = [self._quote_dur, self._quote, "双击开看板 · 点我继续互动"]
            sizes = (13 * sc, 15 * sc, 9.5 * sc)
            bolds = (True, True, False)
            cols = (C_TXT_HEAD, C_TXT_MAIN, C_TXT_DIM)
        else:
            lines = list(self._bubble_lines())
            sizes = (14.5 * sc, 12.5 * sc, 9.5 * sc)
            bolds = (True, True, False)
            cols = (C_TXT_HEAD, C_TXT_MAIN, C_TXT_DIM)
        total_h = sum(sz * 1.5 for sz in sizes) + 8 * sc * 2
        ty = by + (bh - total_h) / 2
        for ln, sz, bd, col in zip(lines, sizes, bolds, cols):
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
        sp = ok_spec(lay)
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
                s.image(img, x, y, w, h)
            return
        if abs(head_dx) < 0.2:
            s.blit(cached, x, y, w, h)         # 完全静止 → 1:1 整块，最快
            return
        # 整张平移：单次 blit 走 GDI 硬件 alpha 混合，自带抗锯齿，不存在接缝。
        s.blit(cached, x + head_dx, y, w, h)

    def _pose_local_diff(self, k1, k2):
        """两张立绘的"局部最大差异"（3×3 块的灰度均差最大值）。

        为什么不用全图均值：均值会被大面积同色区（头发/外套）稀释——
        实测 idle↔surprise 的全图均值只有 2.8（看不见差异），
        但它俩的脸（半眯 vs 瞪眼张嘴）恰恰是双重曝光最刺眼的地方。
        改用局部最大：能抓到"任何一小块是否差异够大"。
        """
        if not self._pose_masks:
            return 0.0            # 没有掩码数据 → 不做限制（退回原行为）
        key = (k1, k2) if k1 <= k2 else (k2, k1)
        hit = self._pose_diff_cache.get(key)
        if hit is not None:
            return hit
        a, b = self._pose_masks.get(k1), self._pose_masks.get(k2)
        w, h = self._pose_wh
        if not a or not b or w <= 0:
            self._pose_diff_cache[key] = 0.0
            return 0.0
        best = 0.0
        for y in range(0, h - 2, 2):
            row = y * w
            for x in range(0, w - 2, 2):
                s = 0
                for dy in (0, 1, 2):
                    o = row + dy * w + x
                    s += abs(a[o] - b[o]) + abs(a[o + 1] - b[o + 1]) + abs(a[o + 2] - b[o + 2])
                if s / 9.0 > best:
                    best = s / 9.0
        self._pose_diff_cache[key] = best
        return best

    def _draw_state_fade(self, s, key, x, y, w, h, now, state):
        """状态切换的**交叉溶解**：把上一张立绘以递减 alpha 叠在新立绘之上。

        为什么做：参考视频里姿态切换是**交叉溶解**而非硬切；本项目 09-25 的设计稿
        也明确要求"2 帧渐入 / 2 帧渐出"，但当时因为「GDI 的 AlphaBlend 没有全局 alpha」
        而改成"只用位移+缩放表达出现"。
        **那个结论不准确** —— BLENDFUNCTION 的第 3 字节就是 SourceConstantAlpha，
        `Surface.blit_a` 用的就是它（2026-09-26 实测确认）。

        实现前提：v3 立绘**同画布、同底边**，所以两张图直接叠即可，无需额外对齐。
        另：从反应态回到待机时先让角色眨一次眼（"回神"，设计稿 §4.3）。
        """
        prev = getattr(self, "_shown_key", None)
        if not prev or prev == key:
            self._fade = None
            self._shown_key = key
            return
        # ★ 溶解闸门：两张立绘差异够大时**直接硬切**，不做交叉溶解。
        # 为什么：交叉溶解只在两帧姿态/表情相近时才好看（参考视频就是如此）。
        # 这套 8 态 Q 版姿态与表情差异都很大（实测局部最大差异 78~218，阈值 60），
        # 硬做溶解 = 两张脸叠在一起的双重曝光 —— 用户反馈的"点击时多个重叠"就是它。
        if self._pose_local_diff(prev, key) > MOTION.POSE_FADE_MAX_DIFF:
            self._fade = None
            self._shown_key = key
            return
        fade = getattr(self, "_fade", None)
        if not fade or fade.get("from") != prev or fade.get("to") != key:
            self._fade = fade = {"from": prev, "to": key, "t0": now}
            prev_state = prev.split(".")[-1].replace("_f", "")
            if state == "idle" and prev_state in REACT_FACES:
                self._blinker.blink_now(now)   # 反应态回待机 → 先眨一次再回神
        self._shown_key = key
        p = (now - fade["t0"]) / max(1e-3, MOTION.STATE_FADE_S)
        if p >= 1.0:
            self._fade = None
            return
        src = self._sprite_cache.get(fade["from"])
        if not src:
            self._fade = None
            return
        a = int(round(255 * (1.0 - p)))        # 旧图淡出，露出新图
        if a > 2:
            s.blit_a(src[0], x, y, w, h, a)

    def _draw_blink(self, s, x, y, w, h, now, state="idle", facing=""):
        """三段式拟真眨眼绘制（依赖 BlinkScheduler 三段缓动）。

        三阶段绘制（依 blink_phase）：
          ① 闭眼段 (close)：上睑盖板从眼缝框顶下落 + 下睑微抬，闭得越多盖得越多
          ② 保持段 (hold)：上下睑在框高 80% 处接触，画睫毛阴影线 + 重睑线
          ③ 睁眼段 (open)：盖板反向收回
          + idle (ratio=1)：不绘制

        v4 眼缝锚定（_eye_config v4 / calib_face.py 标定）：框 = 上睑缘→下睑缘
        之间的**可见暗区**。盖板行程全部发生在"看得见的眼睛"上——旧版从
        "整眼框顶"下落，行程一半消耗在睫毛/头发区，部分眨眼（40-70% 深度）
        几乎不可见，且盖板越界盖掉刘海发丝。盖板色用各状态实测的
        lid_skin（眼底皮肤带均值），与发影下的脸色融合，不再是"亮胶带"。
        部分眨眼（close_depth<1）上下睑不接触 → 永远不画接触线。
        """
        fc = self._face_cfg(state, facing)
        if not fc:
            return
        ratio = self._blinker.eye_opening_ratio(now)
        droop = False
        if ratio >= 1.0:
            # 夜间慵懒态（§三.4）：非眨眼时段的常驻半眯眼（眼睑覆盖 30%）；
            # 拖拽中不画（避免和拖拽反馈叠加）。白天不画——诚实 > 氛围。
            if self._night_mode() and not self._dragging:
                ratio = 1.0 - MOTION.BLINK_NIGHT_DROOP
                droop = True
            else:
                return
        phase = self._blinker.blink_phase(now) if not droop else "droop"

        # ---- 分层差分：有闭眼贴片的态直接合成**真实像素**（不再是椭圆盖板）----
        # 按闭合度取「半闭档」：贴片是离线按"眼睑下落位置"做的垂直遮罩，
        # 睑线以上用闭眼像素、以下保留睁眼像素 —— 这才是半闭，不是两张图混合。
        # 夜间慵懒态（droop）仍走下面的椭圆路径：它是常驻半眯、不是眨眼。
        patch = None if droop else self._blink_cache.get(state)
        if patch:
            slots = patch["slots"]
            n = len(slots)
            closure = 1.0 - max(0.0, min(1.0, ratio))
            idx = int(round(closure * n))          # 0 = 完全睁眼（不画）
            if idx >= 1:
                idx = min(idx, n) - 1
                px = patch["x_mirror"] if facing == "_f" else patch["x"]
                s.blit(slots[idx], x + px, y + patch["y"], patch["w"], patch["h"])
            return

        skin = _lerp_argb(_argb(fc.get("lid_skin") or fc.get("skin_color")
                                or "#FFF2EA"), _argb(fc.get("eyelid_color", "#2A2438")),
                          MOTION.BLINK_LID_SHADE)
        lid = _argb(fc.get("eyelid_color", "#2A2438"))
        # 闭眼进度 0..1（close_p=1 全闭 → 0 不闭；ratio 是睁开度，1=全开 0=全闭）
        close_p = 1.0 - ratio
        for key in ("left", "right"):
            e = (fc.get("eyes") or {}).get(key)
            if not e:
                continue
            cx = x + e["cx"] * w
            cy = y + e["cy"] * h
            ew = e["w"] * w
            eh = e["h"] * h
            if eh < 1 or ew < 1:
                continue

            # === 眼缝几何（v4 约定）：top=上睑缘, bot=下睑缘 ===
            top = cy - eh / 2
            bot = cy + eh / 2
            pad = max(1.0, eh * 0.10)          # 盖板与睫线带的重叠量，防漏缝
            meet_y = top + (bot - top) * 0.80  # 全闭接触点：框高 80%（上睑主导）
            rest = top + (bot - top) * MOTION.BLINK_LID_REST   # 盖板静止位（睫线带下缘）
            # 闭眼越多，眼皮越往中间挤（宽度微收 6%，避免眼皮扁塌）
            width_factor = 1.0 - close_p * 0.06

            # === ①/③ 上睑盖板：从睫线带下缘钻出，全闭到 meet_y ===
            # 立绘自带的深色睫线/发影带保持可见、充当"闭眼的上睑"——
            # 盖板若从框顶出发会盖进刘海，发丝被"擦掉"（真机 4x 验证过）
            cover_h = (meet_y - rest + pad * 0.6) * close_p
            if cover_h > 0.5:
                s.ellipse(skin,
                          cx - ew * 0.52 * width_factor,
                          rest - pad * 0.6,
                          ew * 1.04 * width_factor,
                          cover_h + pad * 0.6)

            # === 下睑微抬：量取 BLINK_LOWER_LID=10%（上睑主导，下睑轻辅助）===
            low_h = (bot - top) * MOTION.BLINK_LOWER_LID * close_p
            if low_h > 0.5:
                s.ellipse(skin,
                          cx - ew * 0.52 * width_factor,
                          bot - low_h,
                          ew * 1.04 * width_factor,
                          low_h + max(1.0, eh * 0.06))

            # === ② 保持段：上下睑接触线 + 重睑线（部分眨眼不接触 → 不画）===
            if phase == "hold" or (ratio < 0.18 and phase != "idle"):
                # 睫毛阴影线：扁平深紫椭圆（眼睑接缝处的暗影，自然感）
                lid_w = max(2.0, ew * 0.85)
                lid_h = max(1.5, eh * 0.07)
                s.ellipse(lid, cx - lid_w / 2, meet_y - lid_h / 2, lid_w, lid_h)
                # 重睑线（古见是大眼，双睑效果明显）：上眼睑内沿一道细弧
                # 用更浅的暗紫色（半透明模拟重睑阴影）+ 宽度略窄
                fold_color = (int(0xC8 * (0.5 + 0.5 * ratio)) << 24) | 0x2A2438
                fold_w = max(2.0, ew * 0.70)
                fold_h = max(1.0, eh * 0.03)
                fold_y = meet_y - eh * 0.04    # 接触线上方一点点（眼窝褶痕）
                s.ellipse(fold_color, cx - fold_w / 2, fold_y - fold_h / 2,
                          fold_w, fold_h)

    def _draw_blush(self, s, x, y, w, h, state, facing, color):
        """按当前立绘状态的实测脸颊坐标画腮红（配置缺失时回退几何估算）。"""
        fc = self._face_cfg(state, facing)
        cheeks = (fc or {}).get("cheeks") or {}
        if cheeks.get("left") and cheeks.get("right"):
            for key in ("left", "right"):
                c = cheeks[key]
                cx = x + c["cx"] * w
                cy = y + c["cy"] * h
                s.ellipse(color, cx - c["w"] * w / 2, cy - c["h"] * h / 2,
                          c["w"] * w, c["h"] * h)
        else:
            # 无脸颊配置（旧 v1 配置）→ 按眼位几何估算：眼外下方
            eyes = (fc or {}).get("eyes") or {}
            for key in ("left", "right"):
                e = eyes.get(key)
                if not e:
                    continue
                sign = -1.0 if key == "left" else 1.0
                cx = x + (e["cx"] + sign * e["w"] * 0.30) * w
                cy = y + (e["cy"] + e["h"] * 0.85) * h
                s.ellipse(color, cx - e["w"] * w * 0.26, cy - e["h"] * h * 0.18,
                          e["w"] * w * 0.52, e["h"] * h * 0.36)

    # ================= 配件层（猫 / 贝雷帽 / 鲸鱼玩偶） =================

    def _init_accessories(self):
        """惰性建配件层（初始化失败自动降级，绝不影响桌宠本体）。"""
        if not _ACC_OK:
            return
        try:
            self._acc = ACC.AccessoryState(scale=self.scale)
            if self._presence is None:                 # 正常情况下 __init__ 已建好
                self._presence = PRESENCE.PresenceDetector().start()
        except Exception:
            self._acc = None
            self._presence = None
            log_exception("[acc] 初始化")

    def _release_accessories(self):
        """关闭配件显示。

        **只销毁配件层，保留活跃探测器**——后者同时供"联动关闭"判定宿主用
        （见 _host_alive）。若在此一并 stop，"关掉配件"就会连带让联动关闭失效。
        探测器是 daemon 线程，随进程退出即结束，不需要显式清理。
        """
        self._acc = None

    def _tick_accessories(self, now, dt):
        """每帧推进配件层（挂在动画帧上，猫才走得顺）。

        注意：本方法可能早于 `_acc` 建立就被调用（__init__ 里 _recreate_window
        会先 draw 一次），所以一律用 getattr 防御式取值。
        """
        if not getattr(self, "_acc", None):
            return
        try:
            if self._presence:
                act = self._presence.active()          # 读缓存，零开销
                # 必须比 **集合**：act 是 set，而 self._acc.active 是 list，
                # `set != list` 在 Python 里恒为真 → 曾导致每帧重复上报同一状态
                # （同一秒 6 条、事件日志被刷到 4 万行）。这是"比较忘了统一类型"的典型。
                if set(act) != set(self._acc.active):
                    self._acc.set_active_agents(act)
                    self._report_event("acc_active",
                                       detail=",".join(sorted(act)))
            self._acc.update(dt, self._layout(), now,
                             enter_s=MOTION.ACC_ENTER_S, exit_s=MOTION.ACC_EXIT_S)
            if self._acc.active_kinds():
                self._drawn_sig = None                 # 有配件在动 → 每帧重绘
        except Exception:
            self._acc = None                           # 异常即摘除，本体不受影响
            log_exception("[acc] tick")

    def _acc_image(self, fn):
        """按文件名缓存配件图（GDI+ 位图，进程内只加载一次）。"""
        cache = getattr(self, "_acc_imgs", None)
        if cache is None:
            cache = self._acc_imgs = {}
        got = cache.get(fn)
        if got is not None:
            return got
        path = os.path.join(ASSETS_DIR, fn)
        if not os.path.isfile(path):
            cache[fn] = None
            return None
        g = P()
        if _LoadImage(path, ctypes.byref(g)) != 0 or not g:
            cache[fn] = None
            return None
        w_, h_ = U32(0), U32(0)
        _GetImageW(g, ctypes.byref(w_))
        _GetImageH(g, ctypes.byref(h_))
        if w_.value <= 0 or h_.value <= 0:
            cache[fn] = None
            return None
        cache[fn] = (g, w_.value, h_.value)
        return cache[fn]

    def _draw_accessories(self, s, lay, now):
        """画配件（统一在立绘之上，按槽位各自定位）。

        说明：GDI 的 AlphaBlend 走的是 1:1 直拷，**没有全局 alpha**，
        所以入退场不靠淡入淡出，而是靠位移 + 缩放（drop / hug / walk_in 三种曲线）
        ——视觉上一样读得出"出现了"，且不必为透明度再引入一遍重采样。

        ⚠️ 2026-09-26 起**整体停用**：用户明确要求帽子（贝雷帽）/小猫/鲸鱼玩偶都不进桌宠，
        切到 v3 统一审美线。这里保留代码（不删）以便随时恢复，由 OFF 开关控制。
        """
        if ACCESSORIES_OFF:
            return
        acc = getattr(self, "_acc", None)
        rect = getattr(self, "_spr_rect", None)
        if not acc or not rect:                     # 早期 draw（__init__）直接跳过
            return
        acc.sprite_span = (rect[0], rect[0] + rect[2])   # 让小猫避开她身后
        state, facing = getattr(self, "_spr_key", ("idle", ""))
        style = "alt" if getattr(self, "style", "q") == "alt" else "q"
        base_state = state[4:] if state.startswith("alt_") else state
        face = ACC.anchor_for(acc.anchors, base_state, facing, style)
        if not face:
            return

        for kind in acc.active_kinds():
            spec = ACC.KINDS.get(kind)
            if not spec:
                continue
            fn = None
            if kind == "cat":
                fn = acc.cat.sprite_file()
            else:
                files = spec.get("files") or ()
                fn = files[1] if (facing == "_f" and len(files) > 1) else (
                    files[0] if files else None)
            if not fn:
                continue
            img = self._acc_image(fn)
            if not img:
                continue

            ar = img[1] / float(img[2])
            pl = ACC.compute_placement(
                kind, face, rect, ar, lay,
                cat_x=(acc.cat.x if kind == "cat" else None),
                scale=lay.get("sc", 1.0), style=style)
            if not pl:
                continue
            x, y, w, h = pl

            tr = acc.transition(kind)
            dx, dy, sca = (tr.transform(kind)[:3] if tr else (0.0, 0.0, 1.0))
            w2, h2 = w * sca, h * sca
            x2 = x + dx - (w2 - w) / 2.0
            y2 = y + dy - (h2 - h)          # 以底边为缩放基准
            x2 = max(-w2 * 0.3, min(x2, lay["W"] - w2 * 0.7))
            # 用 image() 而不是 blit()：配件是 GDI+ 位图（P()），不是 Surface；
            # blit 只接受另一个 Surface（内部取 src.hdc）。踩过一次——写错 API 又被
            # `except: pass` 吞掉，表现为"几何全对但什么都没画出来"，极难查。
            try:
                s.image(img[0], x2, y2, w2, h2)
            except Exception:
                log_exception("[acc] 绘制")

    def _draw_pet(self, s, lay):
        """立绘：状态选图（开心/文静/嘟嘴）+ 镜像 + 按压 Q 弹 + 摇摆 + 脸红 + 粒子。"""
        sc = lay["sc"]
        W, H = lay["W"], lay["H"]
        ph = lay["pet_h"] - 8 * sc
        # 提案 §3：悬停放大（ease-out-cubic 淡入）+ 点击压缩（squash 曲线）
        hover_s = 1.0 + (MOTION.HOVER_SCALE - 1.0) * MOTION.ease_out_cubic(self._hover_t)
        hover_s = min(hover_s, MOTION.SCALE_MAX)          # 提案 §5 克制上限
        sq = self._squash
        ph_draw = ph * hover_s * sq
        # 压扁时横向补偿（squash & stretch，体积感）
        pw_ratio = min(1.0 + (1.0 - sq) * 0.7, MOTION.SCALE_MAX)
        if self._pressed:
            pass                                  # 压缩改由 squash 曲线驱动（不再写死 0.92）
        now = time.time()
        # ---- 情绪优先于数据状态选图（提案 §2）----
        reacting = now < self._react_until
        # 部位点击触发的临时形态切换：self._morph_state 非空时优先
        if self._morph_state and self._morph_until > now:
            state = self._morph_state
        elif reacting and self._react_face in REACT_FACES:
            # 反应态与立绘态同名，直接取用（含 v3 新增的 joy / surprise）
            state = self._react_face
        elif self._emotion in (MOTION.EMOTION_SUCCESS, MOTION.EMOTION_WELCOME):
            state = "happy"
        elif self._emotion == MOTION.EMOTION_FAIL:
            state = "pout"                  # 失败 = 委屈（不是怒容）
        else:
            state = "happy" if (self.active or self._in_talk()) else "idle"
        facing = self._current_facing(now)  # 翻身覆盖 > 鼠标跟随 > 保持
        # 双版本 sprite key：'q.idle' / 'alt.idle' / 带 _f 后缀等
        style_prefix = getattr(self, "style", "q")
        key = f"{style_prefix}.{state}{facing}"
        spr = (self._sprites.get(key)
               or self._sprites.get(f"{style_prefix}.{state}")
               or self._sprites.get(f"{style_prefix}.idle"))
        # 提案 §1：呼吸 + 漂浮 + 情绪姿态偏置（全部量化到整数像素）
        bob = self._breath + self._float_dy + self._emotion_rise()
        if self._drift:                      # 游动漂移时加强起伏
            bob += math.sin(now * 6.0) * 2.0 * sc
        y_bottom = H - 4 * sc + bob
        # 甩尾摇摆：衰减正弦水平位移
        wob = 0.0
        if now < self._wobble_until:
            t = self._wobble_until - now
            wob = self._wobble_amp * math.sin(t * 18.0) \
                * (t / max(0.3, getattr(self, "_wobble_dur", 0.9)))
        if spr:
            img, iw, ih = spr
            pw = (ph * iw / ih) * pw_ratio
            x = (W - pw) / 2 + wob
            # 记录本帧立绘矩形与状态键：配件层据此定位（与 _draw_blush 同一套归一化锚点）
            self._spr_rect = (x, y_bottom - ph_draw, pw, ph_draw)
            self._spr_key = (state, facing)
            # 提案 §4：软阴影随漂浮高度缩放（漂浮越高阴影越小越淡）
            sw = pw * 0.72 * self._shadow_scale
            s.ellipse(C_SHADOW, W / 2 - sw / 2, y_bottom - 6 * sc, sw,
                      8 * sc * self._shadow_scale)
            # 优先用预缩放缓存（AlphaBlend 合成，GDI 负责缩放）；尺寸差异过大回退原图
            cache_key = f"{style_prefix}.{state}{facing}"
            cached = (self._sprite_cache.get(cache_key)
                      or self._sprite_cache.get(f"{style_prefix}.{state}"))
            if cached and abs(cached[2] - ph_draw) < 2:
                self._blit_pet(s, cached[0], img, x, y_bottom - ph_draw, pw, ph_draw)
            else:
                self._blit_pet(s, None, img, x, y_bottom - ph_draw, pw, ph_draw)
            # 状态切换交叉溶解（对齐参考视频；见 _draw_state_fade）
            self._draw_state_fade(s, cache_key, x, y_bottom - ph_draw,
                                  pw, ph_draw, now, state)
            # 立绘整体平移量（视线跟随 + 拖拽倾斜）——表情贴图必须同步平移，
            # 否则凝视/拖拽时眼睑、腮红会与眼睛、脸颊错位
            face_x = x + self._gaze_dx + self._drag_tilt
            # 眨眼/腮红 key 用 style-aware（高冷版眼睛更细长，配置独立）
            face_state_key = f"alt_{state}" if style_prefix == "alt" else state
            # 提案 §1 眨眼：闭眼时按当前立绘实测眼位画眼睑（配置缺失则自动跳过）
            self._draw_blink(s, face_x, y_bottom - ph_draw, pw, ph_draw, now,
                             face_state_key, facing)
            # 提案 §4 腮红：按当前立绘实测脸颊锚点定位（ shy 档更红 ）
            if reacting and self._react_face == "blush":
                blush = MOTION.COLOR_BLUSH_LOUD
            elif self._emotion == MOTION.EMOTION_SUCCESS:
                blush = MOTION.COLOR_BLUSH_SOFT
            elif state in ("shy", "blush"):
                blush = MOTION.COLOR_BLUSH_SOFT        # 害羞立绘自带
            else:
                blush = None
            if blush:
                self._draw_blush(s, face_x, y_bottom - ph_draw, pw, ph_draw,
                                 face_state_key, facing, blush)
            # 跟随模式：领结位的身份色徽章（聚焦=当前前台 agent；未知=灰半透明）
            self._draw_follow_badge(s, x, pw, ph_draw, y_bottom, now)
        else:
            s.ellipse(0xFF39406B, W / 2 - 50 * sc, y_bottom - ph_draw, 100 * sc, ph_draw,
                      line_argb=C_BUBBLE_LINE, line_w=2)
            s.ctext("古见同学缺席中", W / 2, y_bottom - ph_draw / 2 - 8 * sc, 11 * sc,
                    C_BUBBLE, bold=True)
        # 粒子（爱心 / 石化爆发樱花 / 吐泡泡，随生命值淡出）——古见主题配色
        for p in self._particles:
            a = max(0.0, min(1.0, p["life"] / p["max"]))
            if p["kind"] == "heart":
                s.heart(p["x"], p["y"], p["size"],
                        (int(0xEF * a) << 24) | 0xE89AAE)
            elif p["kind"] == "bubble":
                r2 = p["size"] * (1.0 + (1 - a) * 0.4)     # 上浮略膨胀
                s.ellipse((int(0x30 * a) << 24) | 0x7A86C9,
                          p["x"] - r2, p["y"] - r2, r2 * 2, r2 * 2,
                          line_argb=(int(0xC0 * a) << 24) | 0x39406B,
                          line_w=1.6 * sc)
            elif p["kind"] == "spark":
                # OK 弹入迸发：浅橙小点，随生命淡出
                s.ellipse((int(0xE6 * a) << 24) | (MOTION.COLOR_OK_SPARK & 0xFFFFFF),
                          p["x"] - p["size"], p["y"] - p["size"],
                          p["size"] * 2, p["size"] * 2)
            else:
                # 石化爆发樱花瓣（默认档）
                s.ellipse((int(0xDD * a) << 24) | 0xE89AAE,
                          p["x"] - p["size"], p["y"] - p["size"],
                          p["size"] * 2, p["size"] * 2)

    # ---- 消息 ----
    def on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        if msg == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_TIMER:
            return self._on_timer(hwnd, wparam)
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
                ax, ay, aw, ah = work_area()
                nx = max(ax + 4, min(self._win0[0] + dx, ax + aw - lay["W"] - 4))
                ny = max(ay + 4, min(self._win0[1] + dy, ay + ah - lay["H"] - 4))
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
            if self.bubble_on and 0 <= py <= lay["bubble_h"]:
                self._report_event("double_click", detail="bubble")
                self.open_dashboard()
            else:
                self._report_event("double_click", detail="body")
                self._react()
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
                self._blinker.blink_now(time.time())   # §三.3：回待机先眨一次
                return 0
            if was_press:                          # 单击：按区域即时分发（不等双击窗口）
                self._last_interact = time.time()
                py = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                px = ctypes.c_short(lparam & 0xFFFF).value
                if self.bubble_on and 0 <= py <= lay["bubble_h"]:
                    if self._bub_mode == BUBBLE_OK:
                        # OK 态：单击计入连击（点满 3 次才回到数据态），不切台词
                        self._bubble_ok_click()
                    else:
                        self._report_event("bubble_click")
                    self._drawn_sig = None
                elif held_ms >= MOTION.LONGPRESS_MS:
                    self._long_press_release(lay)   # 提案 §3：长按憋气后"噗"地喷水
                else:
                    # 身体点击 → 按区域切形态（提案 §3：点击头/脸/身/裙摆触发不同形态）
                    region = self._body_region(px, py, lay)
                    self._morph_by_region(region)
            return 0
        if msg == WM_HOTKEY and wparam == ID_HOTKEY_FOLLOW:
            self._hotkey_cycle_focus()      # P3 全局热键：手动聚焦轮换
            return 0
        if msg == WM_RBUTTONUP:
            self._report_event("menu_open")
            self.context_menu(hwnd)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 菜单 / 看板 ----
    def context_menu(self, hwnd):
        pt = POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        menu = _user32.CreatePopupMenu()
        _user32.AppendMenuW(menu, MF_STRING, IDM_DASHBOARD, "打开完整看板")
        _user32.AppendMenuW(
            menu, MF_STRING | (MF_CHECKED if self.bubble_on else 0),
            IDM_BUBBLE, "想法气泡")
        _user32.AppendMenuW(
            menu, MF_STRING | (MF_CHECKED if self.acc_on else 0),
            IDM_ACC, "配件（猫 / 贝雷帽 / 玩偶）")
        _user32.AppendMenuW(
            menu, MF_STRING | (MF_CHECKED if self.sound_on else 0),
            IDM_SOUND, "互动音效")
        # P2：行为开关（此前只能改 wb_motion 的 *_ON 后重启）
        _user32.AppendMenuW(
            menu, MF_STRING | (MF_CHECKED if self.ok_autodismiss_on else 0),
            IDM_OK_AUTO, "完成态自动收起")
        _user32.AppendMenuW(
            menu, MF_STRING | (MF_CHECKED if self.linked_close_on else 0),
            IDM_LINKED, "随 Agent 退出联动关闭")
        _user32.AppendMenuW(
            menu, MF_STRING | (MF_CHECKED if self.follow_on else 0),
            IDM_FOLLOW, "跟随前台切换聚焦")
        _user32.AppendMenuW(
            menu, MF_STRING | (MF_CHECKED if self.focus_pin else 0),
            IDM_PIN, "锁定聚焦（pin）")
        # （早期此处有「切换到 3D 桌宠」菜单项；3D 线已于 2026-09-26 退役移除）
        # P2 连续锚点：今日时间线（跨 agent 轮次，点条目开看板）
        _tl_summary, _tl_recent = self._today_timeline()
        subt = _user32.CreatePopupMenu()
        _user32.AppendMenuW(
            subt, MF_STRING, IDM_TIMELINE0,
            "今天：" + (format_timeline_summary(_tl_summary) or "暂无轮次"))
        _user32.AppendMenuW(subt, MF_SEPARATOR, 0, None)
        for i, (hhmm, agent, title) in enumerate(_tl_recent):
            _user32.AppendMenuW(subt, MF_STRING, IDM_TIMELINE0 + 1 + i,
                                f"{hhmm}  {agent} · {title[:20]}")
        if not _tl_recent:
            _user32.AppendMenuW(subt, MF_GRAYED, 0, "（今天还没有轮次）")
        _user32.AppendMenuW(menu, MF_POPUP, subt, "今日时间线")
        # P3 手动聚焦：锁定或跟随失效时的兜底（登记册启用 agent，accent 打点）
        subf = _user32.CreatePopupMenu()
        _focusables = [(k, v) for k, v in sorted(REG.load()["agents"].items(),
                                                 key=lambda kv: kv[1].get("order", 100))
                       if v.get("enabled")]
        for i, (k, v) in enumerate(_focusables):
            chk = MF_CHECKED if (self._follow_focus or {}).get("key") == k else 0
            _user32.AppendMenuW(subf, MF_STRING | chk, IDM_FOCUS0 + i,
                                f"{v.get('label') or k}")
        _user32.AppendMenuW(menu, MF_POPUP, subf, "手动聚焦（Ctrl+Alt+F9 轮换）")
        # P4 接续：仅当角标活跃时出现（用户触发才生成——零隐私风险、无主动打扰）
        _h = self._handoff
        if _h and time.time() - _h.get("since", 0) <= MOTION.HANDOFF_SHOW_MAX_S:
            _hlabel = ((REG.load()["agents"].get(_h["key"]) or {}).get("label")
                       or _h["key"])
            _user32.AppendMenuW(menu, MF_STRING, IDM_HANDOFF,
                                f"生成接续摘要（{_hlabel}）→ 剪贴板")
        # 大小子菜单（0.6–2.5x，对齐上游挂件）
        sub = _user32.CreatePopupMenu()
        for i, sc in enumerate(SCALES):
            chk = MF_CHECKED if abs(self.scale - sc) < 1e-6 else 0
            _user32.AppendMenuW(sub, MF_STRING | chk, IDM_SCALE0 + i,
                                f"{sc:.1f}x" + ("（默认）" if sc == 1.0 else ""))
        _user32.AppendMenuW(menu, MF_POPUP, sub, "桌宠大小")
        # 动效质量子菜单（提案 §6：完整 / 精简 / 关闭，用户可手动降级）
        subq = _user32.CreatePopupMenu()
        for i, (q, label) in enumerate(QUALITY_CHOICES):
            chk = MF_CHECKED if self._quality == q else 0
            _user32.AppendMenuW(subq, MF_STRING | chk, IDM_QUALITY0 + i, label)
        _user32.AppendMenuW(menu, MF_POPUP, subq, "动效质量")
        # 形态风格子菜单（双版本切换：Q 版 / 高冷版）
        subs = _user32.CreatePopupMenu()
        for i, (st, label) in enumerate(STYLE_CHOICES):
            chk = MF_CHECKED if self.style == st else 0
            _user32.AppendMenuW(subs, MF_STRING | chk, IDM_STYLE0 + i, label)
        _user32.AppendMenuW(menu, MF_POPUP, subs, "形态风格")
        _user32.AppendMenuW(menu, MF_STRING, IDM_QUIT, "退出古见同学")
        _user32.SetForegroundWindow(hwnd)
        cmd = _user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
            pt.x, pt.y, 0, hwnd, None)
        _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        _user32.DestroyMenu(sub)
        _user32.DestroyMenu(subq)
        _user32.DestroyMenu(subs)
        _user32.DestroyMenu(subt)
        _user32.DestroyMenu(subf)
        _user32.DestroyMenu(menu)
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
        elif cmd == IDM_ACC:
            self.acc_on = not self.acc_on
            self._report_event("menu_acc", detail=str(self.acc_on))
            self._save_settings()
            if self.acc_on and self._acc is None:
                self._init_accessories()
            if not self.acc_on:
                self._release_accessories()
            self._drawn_sig = None
        elif cmd == IDM_SOUND:
            self.sound_on = not self.sound_on
            if self.sound_on and not self._sounds:
                self._sounds = _make_sounds()
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
        elif IDM_STYLE0 <= cmd < IDM_STYLE0 + len(STYLE_CHOICES):
            new_style = STYLE_CHOICES[cmd - IDM_STYLE0][0]
            if new_style != self.style:
                self.style = new_style
                self._report_event("menu_style", detail=new_style)
                self._save_settings()
                # 形态风格切换不重建窗口（避免打断动画）；只重置 sprite 缓存 key 前缀
                self._drawn_sig = None
                self._motion_sig = None
                # 重置临时形态，让用户立即看到新风格的 idle
                self._morph_state = ""
                self._morph_until = 0.0

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
            api_py = os.path.join(_HERE, "wb_usage", "wb_api.py")
            if not os.path.isfile(api_py):
                log_exception(f"[dashboard] 找不到看板服务脚本 {api_py}")
                return
            port = self._dashboard_port()
            self._report_event("ensure_server", ok=True,
                               detail=f"启动 wb_api --port {port}")
            tmp = os.environ.get("TEMP") or "C:/Windows/Temp"
            with open(os.path.join(tmp, "wb-usage.log"), "ab") as fout, \
                 open(os.path.join(tmp, "wb-usage.err.log"), "ab") as ferr:
                subprocess.Popen(
                    [sys.executable, api_py, "--port", str(port)],
                    cwd=os.path.dirname(api_py),
                    stdin=subprocess.DEVNULL, stdout=fout, stderr=ferr,
                    creationflags=subprocess.DETACHED_PROCESS
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            log_exception("[dashboard] 拉起看板服务失败")
        finally:
            self._spawning_dash = False

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
        return (self.db_ok, self.api_ok, talk, self._pressed, self._react_face,
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
        if not self._dragging:
            _kpi, active, ok, latest = query_db(self.db_path, self.wb_db)
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
            if live_tick or self._fade is not None or sig != self._drawn_sig:
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
        if self._wb_proc_handle:
            try:
                _kernel32.CloseHandle(self._wb_proc_handle)
            except Exception:
                pass
            self._wb_proc_handle = None
        self._uninstall_follow_hotkey()   # P3：全局热键随窗口销毁注销
        try:
            self.surf.close()
        except Exception:
            pass
        for spr in self._sprites.values():
            try:
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
