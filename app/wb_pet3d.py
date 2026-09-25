# -*- coding: utf-8 -*-
r"""wb_pet3d.py — 还原参考视频效果的 3D 立体摆件桌宠（可直接运行）

运行
----
    python app\wb_pet3d.py                       # 默认用 assets3d/pet_v2_rigged.glb
    python app\wb_pet3d.py --model <xxx.glb>
    python app\wb_pet3d.py --shot out.png        # 出一张"自画像"（验证用）
    python app\wb_pet3d.py --seconds 20          # 跑 20 秒自动退出

操作
----
    左键拖 = 移动    右键拖 = 环绕视角    滚轮 = 缩放
    左键点 = 戳一下（惊讶）    双击 = 摸头（开心）
    开关（设置文件 .pet3d_settings.json）：wander 随机走动 / follow 跟随鼠标 / bubble 气泡

视频效果 → 代码位置对照（搜索 `#[Vn]` 直接跳到）
--------------------------------------------------
    #[V1] 无边框 / 背景透明 / 始终置顶 / 不干扰其他窗口
    #[V2] 待机动画（呼吸起伏）
    #[V3] 眨眼（部分眨眼 + 全闭 + 工作中降频）
    #[V4] 拖拽移动（含"被拎起来"的反馈）
    #[V5] 点击反馈（戳=惊讶 / 双击=开心）
    #[V6] 跟随鼠标（视线朝向光标）
    #[V7] 随机走动
    #[V8] 状态/情绪切换（idle / walk / surprise / joy）
    #[V9] 气泡提示（头顶气泡，带尾巴）

架构
----
    glfw 隐藏窗建 GL 3.3 → moderngl 渲染到 FBO → glReadPixels
      → PIL 叠加气泡 → 32 位 DIB → UpdateLayeredWindow(ULW_ALPHA)

**为什么用 ULW_ALPHA**：它的**逐像素命中测试是系统白送的** —— 鼠标只在
alpha>0 的像素响应，透明区自动透传。所以"3D 旋转时剪影每帧在变"这个在别的
方案里最难的问题，在这里根本不存在（不需要凸包近似、不需要轮询光标）。
实测：整帧 3.8ms → 264fps 上限，30fps 只占 11% 单核。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import random
import sys
import time
from ctypes import wintypes

import glfw
import moderngl
import numpy as np
import pygltflib
import trimesh
from PIL import Image, ImageDraw, ImageFont

try:
    from wb_tray import Tray       # 托盘：桌宠无系统关闭入口，必须有它才能正常退出
except Exception:
    Tray = None

try:
    import wb_agent_presence as PRESENCE   # P0-① 联动关闭的 agent 探测
except Exception:
    PRESENCE = None                        # 不可用 → 联动关闭静默跳过（宁可留着）

try:
    import wb_follow as FOLLOW             # 联动判定/接续检测纯逻辑（与 2D 共用）
except Exception:
    FOLLOW = None

_HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(_HERE, ".pet3d_settings.json")
SHARED_SETTINGS = os.path.join(_HERE, ".whale_settings.json")   # 共享：pet_mode 在这里
PET_MODE = "3d"

# P0-① 联动关闭（对齐 2D 语义）：WB_PET_LINKED=1 时，agent 全部退出且持续
# LINKED_GRACE_S → 优雅退出；从未见过任何 agent → 不退（宁可留着，不误杀）。
LINKED_CHECK_S = 2.5
LINKED_GRACE_S = 3.0
MODE_CHECK_S = 2.0

# ================================================================ Win32
WS_EX_LAYERED, WS_EX_TOPMOST = 0x00080000, 0x00000008
WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x08000000, 0x00000080
WS_POPUP, SW_SHOWNOACTIVATE = 0x80000000, 4
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x00000002, 0x00, 0x01
GWL_EXSTYLE, BI_RGB, DIB_RGB_COLORS = -20, 0, 0
PM_REMOVE, HWND_TOPMOST = 0x0001, -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
WM_DESTROY = 0x0002
WM_MOUSEMOVE, WM_LDOWN, WM_LUP, WM_RDOWN, WM_RUP = 0x0200, 0x0201, 0x0202, 0x0204, 0x0205
WM_LDBLCLK, WM_MOUSEWHEEL, WM_NCHITTEST = 0x0203, 0x020A, 0x0084


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class WNDCLASSW(ctypes.Structure):
    """⚠️ ctypes.wintypes 里没有 WNDCLASSW，必须自己定义。"""
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
# ⚠️ 必须逐个声明签名：不声明时 64 位句柄按 c_int 传 → OverflowError 或访问冲突
u32.DefWindowProcW.restype = ctypes.c_longlong
u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
u32.RegisterClassW.restype = wintypes.WORD
u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
u32.ShowWindow.restype = wintypes.BOOL
u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
u32.GetDC.restype = wintypes.HDC
u32.GetDC.argtypes = [wintypes.HWND]
u32.LoadCursorW.restype = wintypes.HANDLE
u32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
u32.UpdateLayeredWindow.restype = wintypes.BOOL
u32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                    ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                    ctypes.POINTER(POINT), wintypes.DWORD,
                                    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
u32.SetWindowPos.restype = wintypes.BOOL
u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.UINT]
u32.DestroyWindow.restype = wintypes.BOOL
u32.DestroyWindow.argtypes = [wintypes.HWND]
u32.PostQuitMessage.restype = None
u32.PostQuitMessage.argtypes = [ctypes.c_int]
u32.PeekMessageW.restype = wintypes.BOOL
u32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                             wintypes.UINT, wintypes.UINT]
u32.TranslateMessage.restype = wintypes.BOOL
u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
u32.DispatchMessageW.restype = ctypes.c_longlong
u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
u32.GetCursorPos.restype = wintypes.BOOL
u32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
u32.GetSystemMetrics.restype = ctypes.c_int
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
g32.CreateCompatibleDC.restype = wintypes.HDC
g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
g32.CreateDIBSection.restype = wintypes.HBITMAP
g32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                 ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
g32.SelectObject.restype = wintypes.HANDLE
g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


def _wndproc(hwnd, msg, wparam, lparam):
    """⚠️ 必须是**模块级单例**：临时桩会被 GC，Windows 之后跳进垃圾内存 → 崩溃。"""
    try:
        if msg == WM_DESTROY:
            u32.PostQuitMessage(0)
            return 0
        # 整窗可点击；"哪些像素真能点到"由 ULW 的 alpha 命中测试决定
        if msg == WM_NCHITTEST:
            return 1
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)
    except BaseException:
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC_STUB = WNDPROC(_wndproc)

# ================================================================ 着色器
VS = """
#version 330
in vec3 in_pos; in vec3 in_nrm; in vec2 in_uv;
uniform mat4 u_mvp; uniform mat4 u_model;
out vec3 v_n; out vec2 v_uv;
void main(){ v_n = mat3(u_model) * in_nrm; v_uv = in_uv; gl_Position = u_mvp * vec4(in_pos,1.0); }
"""
FS = """
#version 330
in vec3 v_n; in vec2 v_uv;
out vec4 f_out;
uniform sampler2D u_tex;
uniform int u_has_tex;
uniform int u_unlit;       // 纯色部件（眼睑盖板）走不受光，否则被 cel 压暗成土黄
uniform vec3 u_flat;
uniform float u_bands[3];
void main(){
  if (u_unlit == 1) { f_out = vec4(u_flat, 1.0); return; }
  vec3 albedo = (u_has_tex == 1) ? texture(u_tex, v_uv).rgb : u_flat;
  vec3 L = normalize(vec3(-0.42, 0.74, 0.52));
  float l = max(dot(normalize(v_n), L), 0.0);
  float b = l > 0.58 ? u_bands[0] : (l > 0.28 ? u_bands[1] : u_bands[2]);
  f_out = vec4(albedo * b, 1.0);
}
"""


# ================================================================ 加载
def _read_vec3(gl, blob, idx):
    acc = gl.accessors[idx]
    bv = gl.bufferViews[acc.bufferView]
    off = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    comp = {"VEC3": 3, "VEC2": 2, "SCALAR": 1}[acc.type]
    fmt = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}[acc.componentType]
    out = np.frombuffer(blob, dtype=np.dtype("<" + fmt), count=acc.count * comp, offset=off)
    return out.reshape(acc.count, comp).astype(np.float32)


def load_glb(path, target_h=1.62):
    """加载 GLB：几何 + 贴图 + morph 增量 + 材质底色。

    ⚠️ 三个坑（都踩过）：
      · glTF 节点变换很多加载器会忽略 → 一律按顶层坐标，不做节点变换
      · 模型常是 **Z-up 且"头在 −Z"** → 用最长轴判 up，再 −Z→+Y
      · trimesh 取到的 baseColorFactor 是 0~255 sRGB（转回线性必失真）→ 从 GLB 读原始线性值
    """
    gl = pygltflib.GLTF2().load(path)
    blob = gl.binary_blob()
    morphs, raw_flat = {}, {}
    for m in gl.meshes:
        if not m.primitives:
            continue
        pi = m.primitives[0]
        if pi.targets:
            morphs[m.name] = [_read_vec3(gl, blob, t["POSITION"]) for t in pi.targets]
        if pi.material is not None:
            pbr = gl.materials[pi.material].pbrMetallicRoughness
            if pbr and pbr.baseColorFactor and not pbr.baseColorTexture:
                raw_flat[m.name] = tuple(float(x) for x in pbr.baseColorFactor[:3])

    sc = trimesh.load(path, force="scene")
    parts = []
    for name, g in sc.geometry.items():
        V = np.asarray(g.vertices, np.float32)
        if len(V) == 0:
            continue
        N = np.asarray(g.vertex_normals, np.float32)
        F = np.asarray(g.faces, np.uint32)
        if F.shape[1] == 4:
            F = np.vstack([F[:, [0, 1, 2]], F[:, [0, 2, 3]]])
        vis = g.visual
        uv = np.asarray(vis.uv, np.float32) if getattr(vis, "uv", None) is not None \
            else np.zeros((len(V), 2), np.float32)
        uv[:, 1] = 1.0 - uv[:, 1]                       # glTF UV 原点左上，GL 左下
        tex = None
        mat = getattr(vis, "material", None)
        img = getattr(mat, "baseColorTexture", None) if mat else None
        if isinstance(img, Image.Image):
            tex = img.convert("RGBA")
        parts.append({"name": name, "V": V, "N": N, "uv": uv, "F": F, "tex": tex,
                      "morph": morphs.get(name), "flat": raw_flat.get(name)})

    allv = np.vstack([p["V"] for p in parts])
    ext = allv.max(0) - allv.min(0)
    R = (np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], np.float32)
         if int(np.argmax(ext)) == 2 else np.eye(3, dtype=np.float32))
    allv = allv @ R.T
    lo, hi = allv.min(0), allv.max(0)
    ctr = (lo + hi) / 2.0
    sc_f = target_h / max(hi[1] - lo[1], 1e-6)
    for p in parts:
        p["V"] = (p["V"] @ R.T - ctr) * sc_f
        # 脚底落 y=0（**加**；写成减号会把模型推到原点下方 → 画面全空）
        p["V"][:, 1] += (hi[1] - lo[1]) * sc_f / 2.0
        p["N"] = p["N"] @ R.T
    return parts


def _cjk_font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ================================================================ #[V3] 眨眼
class Blink:
    """眨眼状态机，对齐 2D 版眨眼标准 ④⑤：状态调制 + 部分眨眼。

    · 部分眨眼占比 65%（`blink_half`），全闭 35%（`blink_full`）—— 与视频里
      "半闭 → 全闭"两档一致
    · 单次 ~130ms（三角波 0→1→0）
    · `busy=True` 时降频（工作中眨眼间隔拉长）
    """

    def __init__(self):
        self.t = 0.0
        self.next_at = random.uniform(2.0, 4.0)
        self.phase = 0.0
        self.dur = 0.13
        self.kind = "half"
        self.busy = False

    def update(self, dt):
        self.t += dt
        if self.phase <= 0.0 and self.t >= self.next_at:
            self.kind = "half" if random.random() < 0.65 else "full"
            self.phase = 1e-6
        if self.phase > 0.0:
            self.phase += dt / self.dur
            if self.phase >= 2.0:
                self.phase = 0.0
                base = 2.4 if self.busy else 3.4
                self.next_at = self.t + base * random.uniform(0.75, 1.5)
        return 1.0 - abs(self.phase - 1.0) if self.phase > 0.0 else 0.0


# （联动关闭判定 linked_should_quit 在 wb_follow.py——与 2D 共用同一份纯逻辑）


def read_shared_pet_mode():
    """读共享设置里的 pet_mode（缺省 '2d'；解析失败回退 '2d'）。"""
    try:
        with open(SHARED_SETTINGS, "r", encoding="utf-8") as f:
            mode = str(json.load(f).get("pet_mode", "2d")).lower()
        return mode if mode in ("2d", "3d") else "2d"
    except Exception:
        return "2d"


def write_shared_pet_mode(mode):
    """写共享设置的 pet_mode（**合并写入**，保住 2D 的其余键不被抹掉）。"""
    data = {}
    try:
        with open(SHARED_SETTINGS, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data["pet_mode"] = mode
    try:
        with open(SHARED_SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ================================================================ 主体
class Pet3D:
    def __init__(self, model, w=560, h=660, scale=1.0):
        self.model_path = model
        self.W, self.H = int(w * scale), int(h * scale)
        self.scale = scale

        st = self._load_settings()
        self.yaw = st.get("yaw", 8.0)
        self.pitch = st.get("pitch", 4.0)
        self.dist = st.get("dist", 3.85)
        self.pos = list(st.get("pos", [1400, 320]))
        self.enable_wander = st.get("wander", True)
        self.enable_follow = st.get("follow", True)
        self.enable_bubble = st.get("bubble", True)

        self.busy = False                    # 供数据层置位（工作中 → 眨眼降频）
        # P0-① 联动关闭：守望拉起时注入 WB_PET_LINKED=1
        self.linked_expected = os.environ.get("WB_PET_LINKED") == "1"
        self._agents_seen = False
        self._linked_empty_since = None
        self._linked_check_at = 0.0
        # P0-② 实时切换：轮询共享设置的 pet_mode，变了 → 优雅退出（守望 5s 内拉起新形态）
        self._mode_check_at = 0.0
        self.blink = Blink()
        self.state = "idle"                  # #[V8] idle / walk / surprise / joy
        self.state_left = 0.0
        self._t = 0.0
        self.running = True

        self._drag = None
        self._drag_start = (0, 0)
        self._win_start = (0, 0)
        self.gaze = 0.0                      # #[V6] 视线朝向（弧度偏移）
        self.gaze_target = 0.0
        self.walk_to = None                  # #[V7]
        self.walk_from = None
        self.walk_t = 0.0
        self.next_walk = time.time() + random.uniform(5.0, 9.0)
        self.bubble_text = ""
        self.bubble_left = 0.0
        self.last_frame = None

        self._init_gl()
        self._load_model()
        self._init_window()

        self.tray = None
        if Tray:
            try:
                self.tray = Tray(self, "古见同学 3D")
                print("  托盘已就绪：右键托盘=菜单（含退出）")
            except Exception as e:
                print(f"  托盘创建失败（不影响运行）: {e}")

    # ---------------- 设置 ----------------
    def _load_settings(self):
        try:
            with open(SETTINGS, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_settings(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump({"yaw": self.yaw, "pitch": self.pitch, "dist": self.dist,
                           "pos": list(self.pos), "wander": self.enable_wander,
                           "follow": self.enable_follow, "bubble": self.enable_bubble},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------------- GL ----------------
    def _init_gl(self):
        if not glfw.init():
            raise RuntimeError("glfw.init 失败")
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        self._ctx_win = glfw.create_window(64, 64, "glctx", None, None)
        if not self._ctx_win:
            raise RuntimeError("创建 GL 上下文失败")
        glfw.make_context_current(self._ctx_win)
        self.ctx = moderngl.create_context()
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.fbo = self.ctx.framebuffer(
            color_attachments=[self.ctx.texture((self.W, self.H), 4)],
            depth_attachment=self.ctx.depth_texture((self.W, self.H)))
        self.prog = self.ctx.program(vertex_shader=VS, fragment_shader=FS)
        self.prog["u_bands"].value = (1.00, 0.84, 0.68)
        self._font = _cjk_font(int(15 * self.scale))

    def _load_model(self):
        t0 = time.time()
        self.parts = load_glb(self.model_path, 1.62)
        self.draws = []
        for p in self.parts:
            vbo = self.ctx.buffer(np.hstack([p["V"], p["N"], p["uv"]]).astype(np.float32).tobytes())
            ibo = self.ctx.buffer(p["F"].astype(np.uint32).tobytes())
            t = None
            if p["tex"] is not None:
                t = self.ctx.texture(p["tex"].size, 4, p["tex"].tobytes())
                t.build_mipmaps()
            vao = self.ctx.vertex_array(self.prog, [(vbo, "3f 3f 2f", "in_pos", "in_nrm", "in_uv")], ibo)
            self.draws.append({"p": p, "vbo": vbo, "vao": vao, "tex": t})
        tris = sum(len(p["F"]) for p in self.parts)
        nm = sum(1 for p in self.parts if p["morph"])
        print(f"  模型 {len(self.parts)} 件 / {tris:,} 三角形 / {nm} 件带形态键 / {time.time()-t0:.2f}s")

    # ---------------- #[V1] 窗口：无边框 + 透明 + 置顶 + 不干扰 ----------------
    def _init_window(self):
        hinst = k32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(_WNDPROC_STUB, ctypes.c_void_p).value
        wc.hInstance = hinst
        wc.lpszClassName = "WBPet3DClass"
        if not u32.RegisterClassW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
            raise RuntimeError(f"RegisterClassW 失败 {ctypes.get_last_error()}")
        self.hwnd = u32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            "WBPet3DClass", "古见同学 3D", WS_POPUP,
            int(self.pos[0]), int(self.pos[1]), self.W, self.H, None, None, hinst, None)
        if not self.hwnd:
            raise RuntimeError(f"CreateWindowExW 失败 {ctypes.get_last_error()}")
        u32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.scr = u32.GetDC(None)
        self.mem = g32.CreateCompatibleDC(self.scr)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.W
        bmi.bmiHeader.biHeight = self.H            # >0 = bottom-up，与 glReadPixels 同向
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        hbmp = g32.CreateDIBSection(self.scr, ctypes.byref(bmi), DIB_RGB_COLORS,
                                    ctypes.byref(bits), None, 0)
        if not hbmp or not bits.value:
            raise RuntimeError("CreateDIBSection 失败")
        g32.SelectObject(self.mem, hbmp)
        self.buf = (ctypes.c_ubyte * (self.W * self.H * 4)).from_address(bits.value)
        self.blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self.size = wintypes.SIZE(self.W, self.H)
        self.pt_src = POINT(0, 0)

    # ---------------- #[V8] 状态切换 ----------------
    def set_state(self, s, secs=0.0):
        self.state = s
        self.state_left = secs

    def poke(self):
        """#[V5] 单击 = 戳一下 → 惊讶（视频 7.9–8.7s：睁大眼 + 张嘴 o）"""
        self.set_state("surprise", 1.0)
        self.say("……！")

    def pet(self):
        """#[V5] 双击 = 摸头 → 开心（视频 8.8–9.6s：wink + 指抵脸颊）"""
        self.set_state("joy", 1.6)
        self.say("嘿嘿…")

    def say(self, text, secs=2.2):
        """#[V9] 气泡提示"""
        self.bubble_text = text
        self.bubble_left = secs

    # ---------------- 输入 ----------------
    def input(self, msg, wparam, lparam):
        if msg in (WM_LDOWN, WM_RDOWN):
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            if msg == WM_LDOWN:
                self._drag = "move"
                self._drag_start = (x, y)
                self._win_start = list(self.pos)
                self.poke()
            else:
                self._drag = "orbit"
                self._drag_start = (x, y)
        elif msg in (WM_LUP, WM_RUP):
            self._drag = None
        elif msg == WM_LDBLCLK:
            self.pet()
        elif msg == WM_MOUSEMOVE:
            # ⚠️ lparam 低/高 16 位**有符号**：`& 0xFFFF` 会把负坐标变成 6 万
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            if self._drag == "move":
                dx, dy = x - self._drag_start[0], y - self._drag_start[1]
                self.pos = [self._win_start[0] + dx, self._win_start[1] + dy]
                self.walk_to = None                        # 手动拖了就别自己走
                self.next_walk = time.time() + random.uniform(4.0, 8.0)
            elif self._drag == "orbit":
                self.yaw += (x - self._drag_start[0]) * 0.45
                self.pitch = max(-24.0, min(24.0, self.pitch - (y - self._drag_start[1]) * 0.30))
                self._drag_start = (x, y)
        elif msg == WM_MOUSEWHEEL:
            d = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            self.dist = max(2.0, min(6.5, self.dist - d / 120.0 * 0.25))

    def _pump(self):
        msg = wintypes.MSG()
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            m = msg.message
            if m in (WM_MOUSEMOVE, WM_LDOWN, WM_LUP, WM_RDOWN, WM_RUP, WM_LDBLCLK, WM_MOUSEWHEEL):
                self.input(m, msg.wParam, msg.lParam)
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    # ---------------- #[V6] 跟随鼠标 ----------------
    def _update_gaze(self, dt):
        if not self.enable_follow or self._drag:
            self.gaze_target = 0.0
        else:
            p = POINT()
            if u32.GetCursorPos(ctypes.byref(p)):
                cx = self.pos[0] + self.W / 2
                cy = self.pos[1] + self.H * 0.35
                dx, dy = p.x - cx, p.y - cy
                # 只做小幅朝向（±0.35 rad），太大会像脖子拧断
                self.gaze_target = max(-0.35, min(0.35, math.atan2(dx, max(abs(dy), 220.0)) * 0.55))
        self.gaze += (self.gaze_target - self.gaze) * min(1.0, dt * 4.0)

    # ---------------- #[V7] 随机走动 ----------------
    def _update_walk(self, dt, now):
        if not self.enable_wander or self._drag or self.state in ("surprise", "joy"):
            return
        if self.walk_to:
            self.walk_t += dt / 2.2                       # 走一趟 ~2.2s
            k = min(1.0, self.walk_t)
            e = k * k * (3 - 2 * k)                       # smoothstep
            self.pos[0] = self.walk_from[0] + (self.walk_to[0] - self.walk_from[0]) * e
            self.pos[1] = self.walk_from[1] + (self.walk_to[1] - self.walk_from[1]) * e
            if k >= 1.0:
                self.walk_to = None
                self.next_walk = now + random.uniform(5.0, 10.0)
        elif now >= self.next_walk:
            sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
            dx = random.uniform(-160, 160) * self.scale
            dy = random.uniform(-70, 70) * self.scale
            tx = max(0, min(sw - self.W, self.pos[0] + dx))
            ty = max(0, min(sh - self.H, self.pos[1] + dy))
            self.walk_from = list(self.pos)
            self.walk_to = [tx, ty]
            self.walk_t = 0.0

    # ---------------- 数学 ----------------
    @staticmethod
    def _disp(c):
        """线性 → sRGB。

        ⚠️ 纯色部件（眼睑盖板）的色值来自 GLB 的 baseColorFactor（**线性**），
        但本管线贴图本身是 sRGB 编码、输出缓冲也按 sRGB 显示 → 直接用会偏暗偏绿。
        """
        out = []
        for v in c:
            v = max(0.0, min(1.0, float(v)))
            out.append(v * 12.92 if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055)
        return tuple(out)

    @staticmethod
    def _persp(fovy, asp, zn, zf):
        t = 1.0 / math.tan(math.radians(fovy) / 2)
        return np.array([[t / asp, 0, 0, 0], [0, t, 0, 0],
                         [0, 0, (zf + zn) / (zn - zf), 2 * zf * zn / (zn - zf)],
                         [0, 0, -1, 0]], np.float32)

    @staticmethod
    def _look_at(eye, ctr, up):
        f = (ctr - eye).astype(np.float64); f /= np.linalg.norm(f)
        s = np.cross(f, up); s /= np.linalg.norm(s)
        u = np.cross(s, f)
        m = np.eye(4, dtype=np.float32)
        m[0, :3], m[1, :3], m[2, :3] = s, u, -f
        m[:3, 3] = -m[:3, :3] @ eye
        return m

    # ---------------- #[V9] 气泡 ----------------
    def _draw_bubble(self, arr):
        """在 RGBA 数组上画一个带尾巴的圆角气泡（头顶偏右）。"""
        if not (self.enable_bubble and self.bubble_text and self.bubble_left > 0):
            return arr
        im = Image.fromarray(arr, "RGBA")
        d = ImageDraw.Draw(im)
        try:
            bbox = d.textbbox((0, 0), self.bubble_text, font=self._font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = 60, 16
        pad = int(10 * self.scale)
        bw, bh = tw + pad * 2, th + pad * 2
        bx = int(self.W * 0.58)
        by = int(self.H * 0.10)
        bx = max(4, min(self.W - bw - 4, bx))
        by = max(4, min(self.H - bh - 4, by))
        try:
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=int(12 * self.scale),
                                fill=(255, 255, 255, 235), outline=(58, 54, 74, 200), width=1)
        except Exception:
            d.rectangle([bx, by, bx + bw, by + bh], fill=(255, 255, 255, 235))
        d.polygon([(bx + int(bw * 0.28), by + bh), (bx + int(bw * 0.40), by + bh),
                   (bx + int(bw * 0.30), by + bh + int(9 * self.scale))],
                  fill=(255, 255, 255, 235))
        d.text((bx + pad, by + pad - 2), self.bubble_text, font=self._font, fill=(46, 42, 62, 255))
        return np.array(im, dtype=np.uint8)

    # ---------------- P0-①② 联动关闭 / 实时切换 ----------------
    def switch_to(self, mode):
        """切到另一个形态：写共享设置 pet_mode → 优雅退出 → 守望 5s 内拉起新形态。"""
        mode = str(mode).lower()
        if mode not in ("2d", "3d") or mode == PET_MODE:
            return
        write_shared_pet_mode(mode)
        self.say(f"切换到 {mode.upper()}…", 2.0)
        self.running = False

    def _linked_and_mode_tick(self, now):
        """每帧轻量入口；重活按 LINKED_CHECK_S / MODE_CHECK_S 分频。"""
        if self.linked_expected and PRESENCE is not None and FOLLOW is not None \
                and now >= self._linked_check_at:
            self._linked_check_at = now + LINKED_CHECK_S
            try:
                agents = PRESENCE.detect_once()
            except Exception:
                agents = None                          # 探测失败 → 不判定
            quit, _seen, _empty = FOLLOW.linked_should_quit(
                self._agents_seen, self._linked_empty_since, agents, now,
                LINKED_GRACE_S, linked_expected=self.linked_expected)
            self._agents_seen, self._linked_empty_since = _seen, _empty
            if quit:
                print("  [linked] agent 全部退出 → 3D 桌宠联动关闭")
                self.say("那…下次见", 1.5)
                self.running = False
                return
        if now >= self._mode_check_at:
            self._mode_check_at = now + MODE_CHECK_S
            if read_shared_pet_mode() != PET_MODE:
                print(f"  [mode] pet_mode 已切换 → 退出，守望将拉起新形态")
                self.running = False

    # ---------------- 每帧 ----------------
    def tick(self, dt):
        now = time.time()
        self._t += dt
        if self.state_left > 0:
            self.state_left -= dt
            if self.state_left <= 0:
                self.state = "idle"
        if self.bubble_left > 0:
            self.bubble_left -= dt
        self.blink.busy = self.busy
        self._linked_and_mode_tick(now)
        k = self.blink.update(dt)
        self._update_gaze(dt)
        self._update_walk(dt, now)

        # ---- 姿态（#[V2] 呼吸 / #[V4] 拖拽反馈 / #[V8] 情绪）----
        bob = math.sin(self._t * 2.85) * 0.012                       # #[V2]
        lift, tilt, scale_m = 0.0, 0.0, 1.0
        if self._drag == "move":
            lift, scale_m = 0.05, 1.04                               # #[V4] 被拎起来
        if self.walk_to:
            bob += abs(math.sin(self._t * 7.5)) * 0.010              # #[V7] 走动轻颠
        if self.state == "surprise":
            lift += -0.03 * min(1.0, self.state_left * 4)            # #[V8] 后仰
            scale_m *= 0.98
        elif self.state == "joy":
            bob += abs(math.sin(self._t * 6.0)) * 0.055              # #[V8] 小跳
            tilt = math.sin(self._t * 5.0) * 0.10                    # #[V8] 歪头

        fbo = self.fbo
        fbo.use()
        self.ctx.clear(0.0, 0.0, 0.0, 0.0)
        proj = self._persp(30.0, self.W / self.H, 0.05, 40.0)
        ry = math.radians(self.yaw) + self.gaze                      # #[V6] 叠加视线偏移
        rp = math.radians(self.pitch)
        base = np.array([0.0, 0.81 + bob + lift, 0.0], np.float32)
        eye = np.array([math.sin(ry) * math.cos(rp) * self.dist,
                        0.95 + math.sin(rp) * self.dist,
                        math.cos(ry) * math.cos(rp) * self.dist], np.float32)

        model = np.eye(4, dtype=np.float32)
        model[0, 0] = model[1, 1] = model[2, 2] = scale_m
        ct, st = math.cos(tilt), math.sin(tilt)
        rot = np.array([[ct, -st, 0, 0], [st, ct, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
        model = rot @ model
        view = self._look_at(eye, base, np.array([0, 1, 0], np.float32))
        self.prog["u_mvp"].write((proj @ view @ model).T.tobytes())
        self.prog["u_model"].write(model.T.tobytes())

        # 眼睑形态键：惊讶=睁大（盖板不动）；开心=眯眼（半闭）
        if self.state == "joy":
            ti, w = 0, 0.75
        else:
            ti, w = (0 if self.blink.kind == "half" else 1), k
        for d in self.draws:
            p = d["p"]
            if p["morph"]:
                Vd = p["V"] if not (ti is not None and ti < len(p["morph"]) and w > 0.001) \
                    else p["V"] + p["morph"][ti] * w
                d["vbo"].write(np.hstack([Vd, p["N"], p["uv"]]).astype(np.float32).tobytes())
            if d["tex"] is not None:
                d["tex"].use(0)
                self.prog["u_has_tex"].value = 1
                self.prog["u_unlit"].value = 0
            else:
                self.prog["u_has_tex"].value = 0
                self.prog["u_unlit"].value = 1
                self.prog["u_flat"].value = self._disp(p["flat"] or (0.31, 0.26, 0.35))
            d["vao"].render()

        data = fbo.read(components=4)
        arr = np.frombuffer(data, np.uint8).reshape(self.H, self.W, 4)[::-1].copy()
        arr = self._draw_bubble(arr)                                  # #[V9]
        arr = arr[::-1]                                                # 回到 bottom-up 给 DIB
        self.last_frame = arr.tobytes()
        ctypes.memmove(self.buf, self.last_frame, len(self.last_frame))
        # ⚠️ 用 memmove；`buf[:] = data` 是 ctypes 逐元素循环，慢 300 倍（实测 51ms→0.18ms）
        pt_dst = POINT(int(self.pos[0]), int(self.pos[1]))
        u32.UpdateLayeredWindow(self.hwnd, self.scr, ctypes.byref(pt_dst), ctypes.byref(self.size),
                                self.mem, ctypes.byref(self.pt_src), 0,
                                ctypes.byref(self.blend), ULW_ALPHA)
        # 每帧重申 TOPMOST（别的全屏窗会盖上来）
        u32.SetWindowPos(self.hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self._pump()
        return k

    # ---------------- 运行 ----------------
    def run(self, fps=30, seconds=None):
        print("  运行中：左键拖=移动 · 右键拖=环绕 · 滚轮=缩放 · 点一下=戳 · 双击=摸头")
        t0 = time.time()
        last = time.perf_counter()
        try:
            while self.running:
                now = time.perf_counter()
                dt = min(now - last, 0.1)
                last = now
                self.tick(dt)
                if seconds and time.time() - t0 >= seconds:
                    break
                sl = 1.0 / fps - (time.perf_counter() - now)
                if sl > 0:
                    time.sleep(sl)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self):
        self.save_settings()
        try:
            if getattr(self, "tray", None):
                self.tray.remove()
        except Exception:
            pass
        try:
            if getattr(self, "hwnd", None):
                u32.DestroyWindow(self.hwnd)
        except Exception:
            pass
        try:
            glfw.terminate()
        except Exception:
            pass
        self.running = False


def default_model_path():
    for p in (os.path.join(_HERE, "assets3d", "pet_v2_rigged.glb"),
              r"D:\workbuddy\_3dtools\model_v2_rigged.glb"):
        if os.path.isfile(p):
            return p
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--shot", default=None, help="存一帧'自画像'（验证用，不依赖 Z 序）")
    a = ap.parse_args()
    m = a.model or default_model_path()
    if not m:
        print("找不到模型：--model 指定，或放到 app/assets3d/pet_v2_rigged.glb")
        sys.exit(1)
    print(f"模型: {m}")
    pet = Pet3D(m, scale=a.scale)
    if a.shot:
        pet.say("今天也一起加油吧", 99)
        pet.run(fps=a.fps, seconds=2.0)
        from PIL import Image as _I
        arr = np.frombuffer(pet.last_frame, np.uint8).reshape(pet.H, pet.W, 4)[::-1]
        _I.fromarray(arr, "RGBA").save(a.shot)
        print(f"  → 自画像 {a.shot}")
    else:
        pet.run(fps=a.fps, seconds=a.seconds)
