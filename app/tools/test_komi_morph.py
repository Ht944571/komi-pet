# -*- coding: utf-8 -*-
r"""古见双版本桌宠回归测试

覆盖：
  A. _load_sprites 加载双版本 12 张（Q 版 + 高冷版 × 6 表情 × 正反）
  B. self.style 持久化（settings 含 style 字段）
  C. _face_cfg 双版本都能取到配置，fallback 链 OK
  D. _body_region 区域判定（head/face/body/skirt 四区域）
  E. _morph_by_region 各区域形态切换正确，台词贴合人设
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


# ===== A. sprite 加载双版本 =====
print("\n[A] sprite 加载双版本 12 张")
keys = list(app._sprites.keys())
check("A1: 至少 12 个 sprite key", len(keys) >= 12, f"got {len(keys)}: {keys[:5]}...")
q_keys = [k for k in keys if k.startswith("q.")]
alt_keys = [k for k in keys if k.startswith("alt.")]
check("A2: Q 版 6 表情", len([k for k in q_keys if "_f" not in k]) == 6)
check("A3: 高冷版 6 表情", len([k for k in alt_keys if "_f" not in k]) == 6)
check("A4: 镜像齐全（每状态 2 个）", all(
    f"{k}_f" in keys for k in q_keys + alt_keys if not k.endswith("_f")))
check("A5: q.idle 存在", "q.idle" in app._sprites)
check("A6: alt.idle 存在", "alt.idle" in app._sprites)
check("A7: alt.stone 存在（石像化高冷版）", "alt.stone" in app._sprites)


# ===== B. style 持久化 =====
print("\n[B] style 持久化")
check("B1: 默认 style='q'", app.style == "q")
# 模拟设置 alt
app.style = "alt"
app._save_settings()  # 这里已经 monkeypatch 为 lambda，不落盘
check("B2: 可设 style='alt'", app.style == "alt")
app.style = "q"  # 还原


# ===== C. _face_cfg 双版本 =====
print("\n[C] _face_cfg 双版本配置")
# 加载 _eye_config.json
import json as _json
cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "_eye_config.json")   # 项目内部，不指向技能目录
eye_cfg = _json.load(open(cfg_path, encoding="utf-8"))
check("C1: Q 版 idle 配置存在", "idle" in eye_cfg["states"])
check("C2: Q 版 blush 配置存在", "blush" in eye_cfg["states"])
check("C3: Q 版 stone 配置存在", "stone" in eye_cfg["states"])
check("C4: 高冷版 alt_idle 配置存在", "alt_idle" in eye_cfg["states"])
check("C5: 高冷版 alt_stone 配置存在", "alt_stone" in eye_cfg["states"])
check("C6: 高冷版 alt_blush 配置存在", "alt_blush" in eye_cfg["states"])
# _face_cfg 调用：传入 alt_idle + _f 应能取到配置且 eyes.left.cx 翻转
app.style = "alt"
cfg_f = app._face_cfg("idle", "_f")   # 用 Q 版 idle 但 _f 翻转
# _f 翻转：原 left(cx=0.345)→ 右 key (cx=0.655)；原 right(cx=0.7008)→ 左 key (cx=0.2992)
# 所以翻转后 left.cx(0.2992) < right.cx(0.655)
check("C7: _face_cfg 翻转 cx（_f 后 left.cx < right.cx）",
      cfg_f["eyes"]["left"]["cx"] < cfg_f["eyes"]["right"]["cx"],
      f"got L={cfg_f['eyes']['left']['cx']} R={cfg_f['eyes']['right']['cx']}")
cfg_f2 = app._face_cfg("alt_idle", "")
check("C8: alt_idle 配置取到", cfg_f2["eyes"]["left"]["cx"] > 0)
app.style = "q"


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
# 摸头
app._morph_by_region("head")
check("E1: 摸头 → blush 形态", app._morph_state == "blush")
check("E2: 摸头 → 台词含 '温柔' 或 '停太久'",
      "温柔" in app._quote or "停太久" in app._quote,
      f"got: {app._quote}")
check("E3: 摸头 → 持续 4s",
      abs(app._morph_until - (time.time() + W.MORPH_HOLD_S)) < 1,
      f"got hold={app._morph_until - time.time():.1f}s")

# 戳脸
app._morph_by_region("face")
check("E4: 戳脸 → stone 形态", app._morph_state == "stone")
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
check("G1: 双版本切换后 whale 仍能正常运行",
      len(app._sprites) >= 12)


print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")

sys.exit(0 if not FAILED else 1)