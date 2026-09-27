#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_anim.py — 帧序列动画（AI 视频 → 透明帧）的相位与取帧逻辑（纯逻辑，可单测）。

素材来自 `tools/video_to_pet_frames.py`：把一段 AI 视频（白底）逐帧抠图、
裁掉水印、按 v3 立绘的高度对齐缩放后，输出一串真透明 PNG + `_anim_meta.json`。

**为什么是帧序列而不是运行时播 WebM**：播放效果**一帧不差**（所有播放器内部
都是逐帧位图），但不需要在运行时带任何视频解码器 —— 项目零第三方依赖的红线保住了，
打包也照旧。

相位约定（与 `_sync_bubble_mode` / 写字本子**同一套任务判据**）：
    writing  有对话任务在跑   → 循环播放「写字段」（源片前 80 帧）
    present  任务完成          → 播一遍「翻页 + 举本子展示」段（源片 80~96 帧）
    其他                       → 不播（回退 v3 立绘）
"""

import json
import os

# 写字段 / 展示段的分界（源片 97 帧里翻页发生在 ~80 帧；抽帧后 40/49 ≈ 0.816）
SPLIT_RATIO = 0.816


class AnimClip:
    """一段帧序列的元数据 + 相位→帧号。"""

    def __init__(self, meta, frames_dir):
        self.name = meta["name"]
        self.frames_dir = frames_dir
        self.count = int(meta["count"])
        self.fps = float(meta.get("fps") or 12.0)
        self.canvas = tuple(meta.get("canvas") or (420, 565))
        self.content_box = tuple(meta.get("content_box") or (0, 0, 1, 1))
        self.split = int(self.count * SPLIT_RATIO)
        self.prefix = self.name

    # ---- 相位 → 帧号 ----

    def index(self, phase, t0, now):
        """当前该显示第几帧。writing 循环；present 播一次后停在最后一帧。"""
        if phase not in ("writing", "present"):
            return 0                    # 未知相位不播（调用方本就不该问）
        dt = max(0.0, now - t0) * self.fps
        if phase == "present":
            return min(self.count - 1, self.split + int(dt))
        return int(dt) % max(1, self.split)          # writing：循环写字段

    def finished(self, phase, t0, now):
        """present 段是否已经播完（之后保持最后一帧，直到状态变化）。"""
        return phase == "present" and (now - t0) * self.fps >= (self.count - self.split)

    def frame_path(self, i):
        return os.path.join(self.frames_dir, f"{self.prefix}_{int(i):03d}.png")


def load(name, assets_dir=None):
    """读 `assets/anim/<name>/_anim_meta.json`。没有素材返回 None（调用方回退立绘）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    assets = assets_dir or os.path.join(here, "assets")
    frames_dir = os.path.join(assets, "anim", name)
    mp = os.path.join(frames_dir, "_anim_meta.json")
    if not os.path.isfile(mp):
        return None
    try:
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
        clip = AnimClip(meta, frames_dir)
        # 首帧必须存在，否则视为素材不完整
        if not os.path.isfile(clip.frame_path(0)):
            return None
        return clip
    except Exception:
        return None
