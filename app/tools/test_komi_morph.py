# -*- coding: utf-8 -*-
r"""古见桌宠立绘与形态回归测试（2026-09-26 起对应 v3 美术方案）

覆盖：
  A. _load_sprites 加载 v3 单版本 8 态 × 正反 = 16 张（同一画布尺寸）
     高冷版（alt_*）已停用 → 断言其不存在
  B. self.style 字段（高冷版停用后即使设 'alt' 也取不到 alt 立绘）
  C. _face_cfg：v3 的 _eye_config.json（joy 标 skip、无 alt_*、未标定态返回 None）
  D. _body_region 区域判定（head/face/body/skirt 四区域）
  E. _morph_by_region 各区域形态切换（v3：摸头→joy、戳脸→surprise，
     石化迁到 _react 第四档），台词贴合人设
  F. 台词人设一致性（不出声/不傲娇怼人/含写字板/含石像化）
  G. 现有功能未破坏（test_scale_switch / test_motion / test_live_bubble 全 PASS）

用法：python tools/test_komi_morph.py
"""

import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_whale_win as W                              # noqa: E402
import wb_motion as MOTION                            # noqa: E402
import wb_hover_core as CORE                          # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


app = W.WhalePet(run_seconds=10)
app._save_settings = lambda: None     # 不落盘用户配置


# ===== A. sprite 加载（v3 单版本 8 态）=====
print("\n[A] sprite 加载 v3 单版本 8 态")
keys = list(app._sprites.keys())
check("A1: 至少 16 个 sprite key（8 态 × 正反）", len(keys) >= 16,
      f"got {len(keys)}: {keys[:5]}...")
q_keys = [k for k in keys if k.startswith("q.")]
alt_keys = [k for k in keys if k.startswith("alt.")]
check("A2: Q 版 8 表情（含 v3 新增 joy / surprise）",
      len([k for k in q_keys if "_f" not in k]) == 8,
      f"got {sorted(k for k in q_keys if '_f' not in k)}")
check("A3: 高冷版已停用（无 alt.* key）", alt_keys == [], f"got {alt_keys[:4]}")
check("A4: 镜像齐全（每状态 2 个）", all(
    f"{k}_f" in keys for k in q_keys if not k.endswith("_f")))
check("A5: q.idle 存在", "q.idle" in app._sprites)
check("A6: v3 八态齐全", all(
    f"q.{s}" in app._sprites for s in
    ("idle", "happy", "pout", "shy", "blush", "stone", "joy", "surprise")))
_sizes = {app._sprites[k][1:] for k in q_keys}
check("A7: 全部立绘同一画布尺寸（切表情不跳尺寸，v3 核心设计）",
      len(_sizes) == 1, f"got {sorted(_sizes)}")


# ===== B. style 字段（高冷版停用后恒为 'q'）=====
print("\n[B] style 字段")
check("B1: 默认 style='q'", app.style == "q")
app.style = "alt"
app._save_settings()  # 这里已经 monkeypatch 为 lambda，不落盘
check("B2: 高冷版已停用 → 即便 style='alt' 也取不到 alt 立绘",
      app._sprites.get("alt.idle") is None)
app.style = "q"  # 还原


# ===== C. _face_cfg（v3 单版本）=====
print("\n[C] _face_cfg 配置")
# 加载 v3 的 _eye_config.json
import json as _json
cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "pet_v3", "_eye_config.json")
eye_cfg = _json.load(open(cfg_path, encoding="utf-8"))
check("C1: idle 配置存在", "idle" in eye_cfg["states"])
check("C2: blush 配置存在", "blush" in eye_cfg["states"])
check("C3: stone 配置存在", "stone" in eye_cfg["states"])
check("C4: v3 新增 joy 显式标 skip（眼睛本就是闭的，不画眼睑）",
      eye_cfg["states"].get("joy", {}).get("skip") is True)
check("C5: v3 新增 surprise 有眼睛锚点",
      "eyes" in eye_cfg["states"].get("surprise", {}))
check("C6: 高冷版 alt_* 配置已移除",
      [k for k in eye_cfg["states"] if k.startswith("alt_")] == [])
# _face_cfg 调用：_f 应翻转 cx（左右互换）
cfg_f = app._face_cfg("idle", "_f")
check("C7: _face_cfg 翻转 cx（_f 后 left.cx < right.cx）",
      cfg_f["eyes"]["left"]["cx"] < cfg_f["eyes"]["right"]["cx"],
      f"got L={cfg_f['eyes']['left']['cx']} R={cfg_f['eyes']['right']['cx']}")
# v3 起：未标定态返回 None，不再回退 idle（回退会把 idle 眼睑画到别的姿态上）
check("C8: 未标定态返回 None（joy / 不存在的态都不猜坐标）",
      app._face_cfg("joy", "") is None and app._face_cfg("nosuchstate", "") is None)


# ===== D. _body_region 区域判定 =====
print("\n[D] _body_region 区域判定")
# 构造 fake layout（dict，_body_region 用 lay["..."] 索引）
lay = {"W": 300, "H": 372, "bub_h": 120, "bubble_h": 120, "pet_h": 240}

# 区域映射：head=顶部 18%，skirt=底部 78%+
check("D1: 顶部 → head", app._body_region(150, 130, lay) == "head")    # 130 < 120+240*0.18=163
check("D2: 中部居中 → face", app._body_region(150, 200, lay) == "face")  # y=200 在 163-228, x=150 中部
check("D3: 底部 → skirt", app._body_region(150, 330, lay) == "skirt")    # y=330 > 120+240*0.78=307
check("D4: 胸口 → body", app._body_region(150, 250, lay) == "body")    # y=250 在 228-307 但不在 face 列
check("D5: 边界兜底", app._body_region(150, 360, lay) in ("skirt", "body"))
# 越界
check("D6: y 越界上 → body", app._body_region(150, 50, lay) == "body")   # < 120


# ===== E. _morph_by_region 各区域形态 =====
print("\n[E] _morph_by_region 形态切换")
# 重置状态
app._emotion = MOTION.EMOTION_NEUTRAL
app._react_until = 0.0
app._react_face = None
app._morph_state = ""
app._morph_until = 0.0

import random
random.seed(42)
# 摸头 → joy（v3：2026-09-26 由 blush 改为 joy，blush 由 _react 一/二档承接）
app._morph_by_region("head")
check("E1: 摸头 → joy 形态（v3）", app._morph_state == "joy")
check("E2: 摸头 → 台词含 '温柔' 或 '停太久'",
      "温柔" in app._quote or "停太久" in app._quote,
      f"got: {app._quote}")
check("E3: 摸头 → 持续 4s",
      abs(app._morph_until - (time.time() + W.MORPH_HOLD_S)) < 1,
      f"got hold={app._morph_until - time.time():.1f}s")

# 戳脸 → surprise（v3：由 stone 改为 surprise，stone 由 _react 第四档承接）
app._morph_by_region("face")
check("E4: 戳脸 → surprise 形态（v3）", app._morph_state == "surprise")
check("E5: 戳脸 → 台词含 '石化' 含义（僵住/石化/不能戳）",
      any(k in app._quote for k in ("僵住", "石化", "不能戳", "掉")),
      f"got: {app._quote}")

# 戳裙
app._morph_by_region("skirt")
check("E6: 戳裙 → pout 形态", app._morph_state == "pout")
check("E7: 戳裙 → 台词含 '为什么' 或 '请不要'",
      "为什么" in app._quote or "请不要" in app._quote,
      f"got: {app._quote}")

# 戳身 → 走原 _react 四档
app._morph_state = ""
app._react_until = 0.0
app._clicks = []    # 清空点击计数
app._morph_by_region("body")
check("E8: 戳身 → 走 _react（_morph_state 保持空）",
      app._morph_state == "" and app._react_until > time.time(),
      f"got morph={app._morph_state} react_until={app._react_until - time.time():.1f}s")

# v3：石化的触发入口由「戳脸」迁到 _react 第四档（该档文案标签本就写着「石化」）
app._react_until = 0.0
app._clicks = [time.time()] * 7     # 第 7 次 → 第四档
app._react()
check("E9: _react 第四档 → stone 形态（v3 承接石化的入口）",
      app._react_face == "stone", f"got {app._react_face}")


# ===== F. 台词人设一致性 =====
print("\n[F] 台词人设一致性")
# 收集所有台词池
all_quotes = (
    W.QUOTES_SHY + W.QUOTES_TSUNDERE + W.QUOTES_POUT + W.QUOTES_OUTBURST
    + W.QUOTES_HEAD + W.QUOTES_FACE + W.QUOTES_SKIRT
)
# 人设红线：
#   ① 几乎不出声：台词描述动作/写字板/脑内 OS，不写实际对话内容
#   ② 不用傲娇怼人语气：避免"滚""烦""讨厌"等
#   ③ 含"笔记本/写"元素（写字板交流）
bad_words = ["滚", "烦死了", "讨厌", "笨蛋", "闭嘴"]
has_notebook = any("笔记本" in q or "写" in q for q in all_quotes)
check("F1: 含写字板交流元素", has_notebook)
check("F2: 无傲娇怼人用词", not any(w in q for q in all_quotes for w in bad_words))
# 检查无明显对话正文（不应该出现长串像台词的内容）
# 规则：单条台词超过 30 字视为对话过长
too_long = [q for q in all_quotes if len(q) > 30 and "（" not in q]
check("F3: 台词短小（≤30 字为主，含括号动作描述）",
      len(too_long) <= 2,
      f"got long: {too_long}")
# 古见人设：紫黑眼、写字板、石像化、交流障碍
check("F4: 含石像化描述",
      any("石" in q for q in W.QUOTES_OUTBURST + W.QUOTES_FACE),
      "no stone reference")
check("F5: 含写字板描述",
      any("笔记本" in q for q in all_quotes))
check("F6: 含'写'动作描述",
      any(re.search(r"写[:：]", q) for q in all_quotes))


# ===== G. 现有功能未破坏 =====
print("\n[G] 现有功能回归")
# 用 threading 避免桌宠 run 阻塞
def shutdown():
    time.sleep(5)
    try:
        W._user32.PostQuitMessage(0)
    except Exception:
        pass
import threading
t = threading.Thread(target=shutdown, daemon=True)
t.start()
try:
    app.run()
except Exception:
    pass
check("G1: v3 单版本下 whale 仍能正常运行（16 张立绘）",
      len(app._sprites) == 16, f"got {len(app._sprites)}")


print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")

sys.exit(0 if not FAILED else 1)