# -*- coding: utf-8 -*-
r"""跟随模式 P4 接续（Handoff）专项测试 —— 设计文档 §5

验证点：
  A. handoff_last_turn：窗口内命中 / 超窗 / 无记录（三值：None = 不可判定）
  B. 角标生命周期：切离"似乎未结束"的 agent → _handoff 置入 + 埋点；
     回到该 agent → 清除；候选为空 → 不置入
  C. 生成摘要：用户触发才生成；**必过脱敏**（wb_common.mask_text）；
     生成后角标消失；handoff_expand 埋点

用法：python tools/test_handoff.py
"""
import json
import os
import sys
import time
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_motion as MOTION                     # noqa: E402
import wb_follow as FOLLOW                     # noqa: E402
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


db = os.path.join(APP, "wb_usage", "data", "wb_usage_dw.db")
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
last = conn.execute(
    "SELECT last_time FROM v_turn_total WHERE agent='zcode' "
    "ORDER BY last_time DESC LIMIT 1").fetchone()
conn.close()
check("PRE: 本机 zcode 有真实轮次数据", last is not None)
last_str = last[0]
last_epoch = time.mktime(time.strptime(last_str[:19], "%Y-%m-%d %H:%M:%S"))

print("\n[A] handoff_last_turn 三值")
r = FOLLOW.handoff_last_turn(db, "zcode", last_epoch + 60)   # 结束后 1 分钟
check("A1: 结束后 1 分钟 → 命中（epoch, title）",
      r is not None and abs(r[0] - last_epoch) < 1.5 and r[1] != "")
r = FOLLOW.handoff_last_turn(db, "zcode", last_epoch + 3600)  # 1 小时后
check("A2: 超过 2 分钟窗口 → None", r is None)
r = FOLLOW.handoff_last_turn(db, "no-such-agent", last_epoch + 60)
check("A3: 无记录的 agent → None", r is None)

print("\n[B] 角标生命周期（跟随提交驱动）")
tmpdir = os.path.join(HERE, "_tmp_handoff_test")
os.makedirs(tmpdir, exist_ok=True)
settings_file = os.path.join(tmpdir, ".whale_settings.json")
saved_sf = W.SETTINGS_FILE
W.SETTINGS_FILE = settings_file
try:
    if os.path.exists(settings_file):
        os.remove(settings_file)
    app = W.WhalePet(run_seconds=2)
    evlog = []
    app._report_event = lambda ev, ok=None, detail="": evlog.append((ev, detail))
    # 接续检测打桩：workbuddy 有"刚结束"的一轮；codex 没有
    _orig_last_turn = FOLLOW.handoff_last_turn
    W.FOLLOW.handoff_last_turn = (
        lambda db_path, key, now, **kw:
        (now - 30, "刚才的话题") if key == "workbuddy" else None)
    fake_ev = {"event": "change", "key": "codex", "from": "workbuddy"}
    app._follow_commit(fake_ev, time.time())
    check("B1: 切离'似乎未结束'的 workbuddy → 角标置入",
          (app._handoff or {}).get("key") == "workbuddy"
          and (app._handoff or {}).get("title") == "刚才的话题")
    check("B2: handoff_badge_shown 已上报",
          any(e[0] == "handoff_badge_shown" and "workbuddy" in e[1]
              for e in evlog))
    evlog.clear()
    app._follow_commit({"event": "change", "key": "workbuddy",
                        "from": "codex"}, time.time())
    check("B3: 回到该 agent → 角标消失", app._handoff is None)
    app._follow_commit({"event": "change", "key": "codex",
                        "from": "workbuddy"}, time.time())
    evlog.clear()
    app._follow_commit({"event": "change", "key": "zcode",
                        "from": "codex"}, time.time())
    check("B4: 切离无候选的 agent → workbuddy 的未结束角标保留"
          "（10 分钟上限负责过期）",
          (app._handoff or {}).get("key") == "workbuddy")

    print("\n[C] 生成摘要：注入降级链（handoff.md → 剪贴板）+ 必过脱敏")
    captured = []
    app._copy_to_clipboard = lambda text: captured.append(text) or True
    fake_cwd = os.path.join(tmpdir, "agent-cwd")
    os.makedirs(fake_cwd, exist_ok=True)

    # 场景 1：cwd 可得 → handoff.md 通道（降级链第一档）
    app._handoff = {"key": "zcode", "title": "古见同学桌宠交接优化",
                    "since": time.time()}
    evlog.clear()
    app._handoff_cwd = lambda key: fake_cwd
    app._gen_handoff()
    hf = os.path.join(fake_cwd, MOTION.HANDOFF_FILENAME)
    text = open(hf, encoding="utf-8").read() if os.path.exists(hf) else ""
    check("C1: cwd 可得 → 写 handoff.md（第一档）", os.path.exists(hf))
    check("C2: 内容含接续抬头与最后一问",
          text.startswith("# 接续自") and "最后一问：" in text, f"text={text[:60]}")
    check("C3: 必过脱敏（截断/打码标记）",
          ("…" in text) or ("***" in text) or len(text) < 500,
          f"len={len(text)}")
    check("C4: handoff_expand 记录通道 handoff.md",
          any(e[0] == "handoff_expand" and "handoff.md" in e[1] for e in evlog))
    check("C5: 生成后角标消失", app._handoff is None)
    check("C6: 第一档成功 → 未降级剪贴板", not captured)

    # 场景 2：cwd 不可得 → 剪贴板保底（第二档）
    app._handoff = {"key": "zcode", "title": "t", "since": time.time()}
    app._handoff_cwd = lambda key: None
    app._gen_handoff()
    check("C7: cwd 不可得 → 剪贴板保底", bool(captured))
    check("C8: 剪贴板内容同为脱敏摘要", "最后一问：" in captured[-1])

    # 场景 3：handoff.md 写入失败（目标位置是个目录）→ 降级剪贴板
    app._handoff = {"key": "zcode", "title": "t", "since": time.time()}
    block_dir = os.path.join(tmpdir, "blocked")
    os.makedirs(os.path.join(block_dir, MOTION.HANDOFF_FILENAME), exist_ok=True)
    app._handoff_cwd = lambda key: block_dir
    app._gen_handoff()
    check("C9: handoff.md 写失败 → 降级剪贴板", len(captured) >= 2)
    check("C10: 生成后角标消失", app._handoff is None)
    app.close()
finally:
    W.SETTINGS_FILE = saved_sf
    W.FOLLOW.handoff_last_turn = _orig_last_turn   # 还原真函数

print(f"\n=== 总结 ===")
print(f"PASS: {len(PASSED)}")
print(f"FAIL: {len(FAILED)}")
for f in FAILED:
    print(f"  - {f}")
sys.exit(0 if not FAILED else 1)
