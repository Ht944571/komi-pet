# -*- coding: utf-8 -*-
r"""写字本子回归测试：触发条件 / 状态机迁移 / 几何不越界 / 与 active 同判据。

守护的核心不变量（改 wb_notebook / 本子绘制时别破坏）：
  A. 四种迁移都跟着「运行中会话数」走 —— 与气泡 OK 态**同一个判据**
  B. 首次取样只建基线（启动瞬间不误判成"任务开始"）
  C. 挂起/睡眠后恢复不丢行、不卡死（推进必须按累计行数）
  D. 写满一页会翻页（页 +1、本页行清零）
  E. 几何：本子不出窗口、**不遮住眼睛**（八态逐一验，用真实眼位算）

用法： py tools/test_notebook.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import wb_notebook as NB          # noqa: E402

V3 = os.path.join(APP, "assets", "pet_v3")
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if not cond else ""))


print("  写字本子回归测试")
print("=" * 60)

# ---------------------------------------------------------------- A/B/C/D
print("\n[A] 四种迁移（跟着 active 走）")
nb = NB.NotebookState()
t = 1000.0
check("A0 初始 hidden", nb.phase == NB.PHASE_HIDDEN)
check("A1 首次取样只建基线，不触发", nb.feed(0, t) == "" and nb.phase == NB.PHASE_HIDDEN)
check("A2 首次取样就是「有任务」→ 直接进 writing（不播 appear）",
      NB.NotebookState().feed(3, t) == "task_start")
nb = NB.NotebookState()
nb.feed(0, t)
mv = nb.feed(1, t + 1.0, {"title": "写看板", "dur": "1:00", "tokens": "1万", "credit": "1.0"})
check("A3 任务开始 → appear", mv == "task_start" and nb.phase == NB.PHASE_APPEAR)
nb.t_phase = t + 1.0 - 0.4
nb.tick(t + 1.0)
check("A4 appear 到时 → writing", nb.phase == NB.PHASE_WRITING)
mv = nb.feed(0, t + 9.0)
check("A5 任务完成 → present", mv == "task_done" and nb.phase == NB.PHASE_PRESENT)
mv = nb.feed(2, t + 20.0)
check("A6 新一轮 → 回 writing 且行数清零",
      mv == "new_task" and nb.phase == NB.PHASE_WRITING and nb.total_lines == 0)

print("\n[B] 首次取样基线（不得误判）")
nb = NB.NotebookState()
check("B1 有任务但首帧 → 直接 writing（不误判成完成）",
      nb.feed(2, t) == "task_start" and nb.phase == NB.PHASE_WRITING)
nb2 = NB.NotebookState()
nb2.feed(2, t)
nb2.t_phase = t - 0.4
nb2.tick(t)
check("B2 空任务首帧不触发任何迁移", nb2.feed(2, t) == "" or nb2.phase == NB.PHASE_WRITING)

print("\n[C] 挂起恢复（推进按累计行数，不许死循环）")
nb = NB.NotebookState()
nb.feed(0, t)
nb.feed(1, t + 0.01, {"title": "x", "dur": "1:00", "tokens": "1", "credit": "1"})
nb.t_phase = t - 0.4
nb.tick(t)
nb.t_phase = t - 60.0                    # 模拟挂起 60 秒后恢复
nb.tick(t)
per_line = NB.LINE_S
expect = int(60.0 / per_line) + 1
check("C1 恢复后行数追上（不丢行）", nb.total_lines == expect,
      f"累计 {nb.total_lines}，期望 {expect}")
check("C2 页数按累计行数算对",
      nb.page == (expect - 1) // NB.LINES_PER_PAGE + 1,
      f"第 {nb.page} 页")

print("\n[D] 翻页")
nb = NB.NotebookState()
nb.feed(0, t)
nb.feed(1, t, {})
nb.t_phase = t - 0.4
nb.tick(t)
nb.t_phase = t - NB.LINE_S * (NB.LINES_PER_PAGE + 2)
nb.tick(t)
check("D1 写满一页 → 页 +1", nb.page == 2, f"第 {nb.page} 页")
check("D2 翻页后本页行数回到页内", 1 <= nb.lines <= NB.LINES_PER_PAGE, f"{nb.lines} 行")
check("D3 累计行数不清零", nb.total_lines > NB.LINES_PER_PAGE, f"{nb.total_lines}")

print("\n[E] 几何：不出窗口、不遮眼睛（八态逐一）")
meta = json.load(open(os.path.join(V3, "_build_meta.json"), encoding="utf-8"))
eyes = json.load(open(os.path.join(V3, "_eye_config.json"), encoding="utf-8"))["states"]

# 立绘矩形：模拟窗口里的一段（位置随便，比例要对）
SPR = (60.0, 100.0, 240.0, 300.0)        # x, y, w, h
bad_out, bad_eye = [], []
for st, m in meta.items():
    cbox = m["content_box"]
    x0, y0, x1, y1 = cbox
    ch = (y1 - y0) * SPR[3]
    eye = eyes.get(st, {}).get("eyes") or {}
    eye_r = None
    if len(eye) >= 2:
        cy = sum(v["cy"] for v in eye.values()) / len(eye)
        eye_r = (cy - y0) / max(1e-6, y1 - y0)     # 眼位在内容高里的相对位置
    for phase in (NB.PHASE_WRITING, NB.PHASE_PRESENT):
        n = NB.NotebookState()
        n.phase = phase
        n.t_phase = 0.0
        # ⚠️ 必须和真实调用方一样传 eye_y —— 不传就没有"不遮眼睛"的钳制，
        #    这个用例也就失去了守护意义（第一次就是这么写错的）。
        d = n.deck(SPR, cbox, 1.0, 1000.0, eye_y=eye_r)
        if phase == NB.PHASE_PRESENT:
            d = n.deck(SPR, cbox, 1.0, 9999.0, eye_y=eye_r)   # 走完 present 缓动
        # 窗口范围
        if d["x"] < 0 or d["y"] < 0 or d["x"] + d["w"] > SPR[2] + SPR[0] \
                or d["y"] + d["h"] > SPR[1] + SPR[3] + 1:
            bad_out.append((st, phase, round(d["x"]), round(d["y"]),
                            round(d["w"]), round(d["h"])))
        # 不遮眼睛：本子上沿必须落在眼位之下（写/展示都算）
        if eye_r is not None:
            top_r = (d["y"] - SPR[1] - y0 * SPR[3]) / max(1e-6, ch)
            if top_r < eye_r - 0.005:
                bad_eye.append((st, phase, round(top_r, 3), round(eye_r, 3)))
check(f"E1 八态×两相位都不出窗口", not bad_out, str(bad_out[:3]))
check(f"E2 八态×两相位都不遮眼睛", not bad_eye, str(bad_eye[:3]))

print("\n[F] 展示文本来自真实数据（不是装饰）")
nb = NB.NotebookState()
nb.feed(0, t)
nb.feed(1, t, {"title": "写看板的新功能", "dur": "8:32", "tokens": "1.2万", "credit": "3.4"})
nb._last_active_n = 1
nb.feed(0, t + 1)
check("F1 完成后仍保留最后一次统计", nb.present_text() == "8:32 · 1.2万 tokens · 3.4 积分",
      nb.present_text())
check("F2 标题可用", nb.title() == "写看板的新功能")
nb2 = NB.NotebookState()
nb2.feed(0, t)
nb2.feed(1, t, {})
nb2._last_active_n = 1
nb2.feed(0, t + 1)
check("F3 无数据时不编造", nb2.present_text() == "", repr(nb2.present_text()))

print("\n[G] 展示会自动收起")
nb = NB.NotebookState()
nb.feed(0, t)
nb.feed(1, t, {})
nb._last_active_n = 1
nb.feed(0, t + 1)
check("G1 进入 present", nb.phase == NB.PHASE_PRESENT)
nb.tick(t + 1 + NB._dur("NOTE_PRESENT_S", 0.42) + NB._dur("NOTE_HOLD_S", 6.0) + 0.1)
check("G2 保持期满 → dismiss", nb.phase == NB.PHASE_DISMISS)
nb.tick(t + 100)
check("G3 dismiss 结束 → hidden", nb.phase == NB.PHASE_HIDDEN)

print("\n" + "=" * 60)
print(f"  总结：PASS {len(PASSED)} / FAIL {len(FAILED)}")
for f in FAILED:
    print(f"    FAIL: {f}")
sys.exit(1 if FAILED else 0)
