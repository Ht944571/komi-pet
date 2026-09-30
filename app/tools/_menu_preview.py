# -*- coding: utf-8 -*-
r"""右键菜单美术离屏预览（开发脚本，非回归测试）

用**真实的绘制代码**（MenuSession._render + Surface 的 GDI+ 原语）在内存位图上
画一张完整菜单（常态/悬停/勾选/禁用/危险/分隔线/子菜单箭头全变体），存 PNG 供
目视验收。present（ULW 上屏）在离屏模式下被替换为 no-op——像素留在 DIB 里直接读。

用法：py tools/_menu_preview.py [输出.png]
"""
import ctypes
import os
import sys
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
import wb_whale_win as W                          # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_menu_preview.png")

# ---- GDI+ 初始化（离屏渲染必需；桌宠进程内由 WhalePet.__init__ 做）----
si = W.GdiplusStartupInput()
si.GdiplusVersion = 1
_tok = W.P()
if W._GdiplusStartup(ctypes.byref(_tok), ctypes.byref(si), None) != 0:
    raise SystemExit("GdiplusStartup 失败")

# 条目：与 context_menu 同构，含全部状态变体
items = [
    {"label": "打开完整看板", "cmd": 0},
    {"label": "想法气泡", "cmd": 0, "checked": True},
    {"label": "音效", "cmd": 0, "checked": True},
    {"label": "完成态自动收起", "cmd": 0, "checked": False},
    {"label": "随 Agent 退出联动关闭", "cmd": 0, "checked": True},
    {"label": "跟随前台切换聚焦", "cmd": 0, "checked": False},
    {"label": "锁定聚焦（pin）", "cmd": 0, "checked": True},
    {"sep": True, "label": ""},
    {"label": "今日时间线", "cmd": 0, "sub": [{"label": "12:01 zcode · 测试轮次", "cmd": 0}]},
    {"label": "手动聚焦（Ctrl+Alt+F9 轮换）", "cmd": 0, "sub": []},
    {"label": "桌宠大小", "cmd": 0, "sub": []},
    {"label": "修复卡死会话（2 个 · 停更 30 分）", "cmd": 0, "danger": True},
    {"label": "检查更新", "cmd": 0},
    {"label": "（今天还没有轮次）", "cmd": 0, "disabled": True},
    {"label": "退出古见同学", "cmd": 0, "danger": True},
    {"label": "长条目截断验证：手动聚焦（Ctrl+Alt+F9 轮换）超长", "cmd": 0},
]
HOVER = 2          # 第 3 行（音效）画成悬停态

sess = W.MenuSession(items, dpi=96)
W_ = sess._win_size(sess._content_h)[0]
H_ = sess._win_size(sess._content_h)[1]
sess.hwnd = 1
sess.surf = W.Surface(W_, H_)
sess._hover = HOVER
sess.surf.present = lambda hwnd: True            # 只画不上屏
sess._render()
print(f"rendered {W_}x{H_}")

# ---- 从 Surface 的 DIB 读回像素 → PNG（alpha 合成到白底；透明区铺浅灰）----
g = W._gdi32


class BMPH(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in ("biSize", "biWidth", "biHeight")] + \
               [(n, ctypes.c_uint16) for n in ("biPlanes", "biBitCount")] + \
               [(n, ctypes.c_uint32) for n in ("biCompression", "biSizeImage",
                                               "biXPels", "biYPels", "biClrUsed",
                                               "biClrImportant")]


hdr = BMPH()
hdr.biSize = ctypes.sizeof(BMPH)
hdr.biWidth, hdr.biHeight = W_, -H_
hdr.biPlanes, hdr.biBitCount = 1, 32
buf = (ctypes.c_ubyte * (W_ * H_ * 4))()
g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
g.GetDIBits(sess.surf.hdc, sess.surf.hbm, 0, H_, buf, ctypes.byref(hdr), 0)

rows = []
for yy in range(H_):
    row = bytearray()
    for xx in range(W_):
        o = (yy * W_ + xx) * 4
        a = buf[o + 3]
        if a == 0:
            row += b"\xDC\xDC\xDC"
        else:
            b_, g_, r_ = buf[o], buf[o + 1], buf[o + 2]
            row += bytes((r_ * a // 255 + 255 * (255 - a) // 255,
                          g_ * a // 255 + 255 * (255 - a) // 255,
                          b_ * a // 255 + 255 * (255 - a) // 255))
    rows.append(bytes(row))
raw = b"".join(b"\x00" + r for r in rows)


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + \
        struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


png = (b"\x89PNG\r\n\x1a\n"
       + _chunk(b"IHDR", struct.pack(">IIBBBBB", W_, H_, 8, 2, 0, 0, 0))
       + _chunk(b"IDAT", zlib.compress(raw, 9))
       + _chunk(b"IEND", b""))
with open(OUT, "wb") as f:
    f.write(png)
print(f"saved: {OUT}  ({W_}x{H_})")
