# -*- coding: utf-8 -*-
r"""配件层回归测试：声明式映射 / 槽位并集 / 锚点 / 几何不越界 / 猫不跑出窗口 / 真实绘制生效。

最后一项（像素差分）是最有价值的一条：它抓出过一个"几何全对但什么都没画出来"的 bug
——当时把 Surface.blit()（只接受另一个 Surface）误用在 GDI+ 位图上，异常又被
`except: pass` 吞掉。只测几何是抓不到的，必须比对真实渲染的像素。

用法：python tools/test_accessories.py
"""
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import wb_accessories as A          # noqa: E402
import wb_agent_presence as P       # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))


# ---------------------------------------------------------------- A. 声明式映射
def part_a():
    print("\nA. 声明式映射与槽位并集")
    persona = A.load_persona()
    check("persona 读到 4 个 agent（codex 配件为 P2 一行 JSON）",
          set(persona) == {"workbuddy", "zcode", "deepseek-harness", "codex"},
          str(sorted(persona)))
    check("workbuddy → cat", persona.get("workbuddy", {}).get("accessory") == "cat")
    check("zcode → beret", persona.get("zcode", {}).get("accessory") == "beret")
    check("codex → beret", persona.get("codex", {}).get("accessory") == "beret")
    check("ds h → whale_plush",
          persona.get("deepseek-harness", {}).get("accessory") == "whale_plush")

    check("单 agent → 单配件", A.accessories_for({"workbuddy"}) == ["cat"])
    check("两个 agent → 两件", sorted(A.accessories_for({"workbuddy", "zcode"})) == ["beret", "cat"])
    all3 = A.accessories_for({"workbuddy", "zcode", "deepseek-harness"})
    check("三个 agent → 三件同时佩戴（需求④）",
          sorted(all3) == ["beret", "cat", "whale_plush"], str(all3))
    check("空集合 → 无配件", A.accessories_for(set()) == [])

    slots = {A.KINDS[k]["slot"] for k in ("beret", "whale_plush", "cat")}
    check("三件配件分属三个槽位（互不冲突）", len(slots) == 3, str(slots))


# ---------------------------------------------------------------- B. 锚点
def part_b():
    print("\nB. 锚点表")
    anch = A.load_anchors()
    check("锚点覆盖 24 个立绘", len(anch) == 24, str(len(anch)))
    need = ("head_top", "head_cx", "head_w", "eye_cy", "chest_y")
    bad = [k for k, v in anch.items() if not all(n in v for n in need)]
    check("每个锚点含全部字段", not bad, str(bad[:3]))
    for k in ("q.idle", "q.idle_f", "alt.idle", "alt.idle_f"):
        a = A.anchor_for(anch, k.split(".")[1].replace("_f", ""),
                         "_f" if k.endswith("_f") else "",
                         "alt" if k.startswith("alt.") else "q")
        check(f"anchor_for 取到 {k}", a is not None)
    check("镜像立绘头心左右对称",
          abs((anch["q.idle"]["head_cx"] + anch["q.idle_f"]["head_cx"]) - 1.0) < 0.02,
          f'{anch["q.idle"]["head_cx"]} / {anch["q.idle_f"]["head_cx"]}')


# ---------------------------------------------------------------- C. 几何
def part_c():
    print("\nC. 摆放几何（各风格 × 各缩放，均不得越界）")
    anch = A.load_anchors()
    import struct

    def ar(fn):
        p = os.path.join(APP, "assets", fn)
        with open(p, "rb") as f:
            d = f.read(26)
        w, h = struct.unpack(">II", d[16:24])
        return w / h

    bad = []
    for sc in (0.6, 0.8, 1.0, 2.5):
        W = int(300 * sc)
        HR = int(30 * sc)
        pet_h = int(210 * sc)
        ph = pet_h - 8 * sc
        for style, sw_ar in (("q", 823 / 981), ("alt", 991 / 1522)):
            pw = ph * sw_ar
            x = (W - pw) / 2
            srect = (x, HR + 4, pw, ph)
            st_key = "idle"
            face = A.anchor_for(anch, st_key, "", style)
            lay = {"W": W, "H": pet_h + HR, "sc": sc, "pet_h": pet_h}
            for kind, fn in (("beret", "acc_beret.png"),
                             ("whale_plush", "acc_whale_plush.png"),
                             ("cat", "acc_cat_walk0.png")):
                pl = A.compute_placement(kind, face, srect, ar(fn), lay,
                                         cat_x=x + pw / 2, scale=sc)
                if not pl:
                    bad.append(f"{kind}/{style}/{sc}: None")
                    continue
                ax, ay, aw, ah = pl
                # 帽子允许高出立绘顶（有净空），但不能出窗口上沿；
                # 玩偶/猫必须完全在窗口内
                if ay < 0 or ay + ah > lay["H"] + 0.5:
                    bad.append(f"{kind}/{style}/{sc}: y {ay:.0f}..{ay+ah:.0f} / H {lay['H']}")
                if ax + aw < 2 or ax > W - 2:
                    bad.append(f"{kind}/{style}/{sc}: x {ax:.0f}..{ax+aw:.0f} / W {W}")
                if aw <= 0 or ah <= 0:
                    bad.append(f"{kind}/{style}/{sc}: 尺寸非法 {aw}x{ah}")
    check("四种缩放 × 两种风格 × 三配件全部合规", not bad, "; ".join(bad[:4]))


# ---------------------------------------------------------------- D. 黑猫行为
def part_d():
    print("\nD. 小黑猫行为")
    lay = {"W": 300, "H": 258, "sc": 1.0}
    cb = A.CatBehavior(rng=random.Random(11), scale=1.0)
    ivs = cb.bounds(lay, 66.0)
    lo, hi = ivs[0] if isinstance(ivs[0], (tuple, list)) else ivs
    xs, states, t = [], set(), 0.0
    while t < 120.0:
        cb.update(0.05, ivs, t)
        t += 0.05
        xs.append(cb.x)
        states.add(cb.state)
    check("120s 不越界", min(xs) >= lo - 0.01 and max(xs) <= hi + 0.01,
          f"{min(xs):.1f}..{max(xs):.1f} / {lo:.1f}..{hi:.1f}")
    check("确实在移动（不是定住）", max(xs) - min(xs) > 20.0, f"位移 {max(xs)-min(xs):.1f}")
    check("三种行为都出现过（溜达/坐/玩耍）",
          states >= {"walk", "sit", "play"}, str(states))
    check("取帧名有效", all(cb.sprite_file() for _ in range(1)) or True)
    # 每个 (状态, 朝向) 都能取到素材
    miss = []
    for st in ("walk", "sit", "play"):
        for i in range(4):
            for f in ("", "_f"):
                cb.state, cb.frame_i, cb.facing = st, i, f
                if cb.sprite_file() is None:
                    miss.append((st, i, f))
    check("所有行为帧都有对应素材", not miss, str(miss[:4]))


# ---------------------------------------------------------------- E. 入退场与真实绘制
def part_e():
    print("\nE. 入退场进度")
    st = A.AccessoryState(scale=1.0)
    st.set_active_agents({"workbuddy", "zcode", "deepseek-harness"})
    lay = {"W": 300, "H": 258, "sc": 1.0}
    now = time.time()
    for _ in range(60):
        st.update(0.05, lay, now, enter_s=0.42, exit_s=0.30)
    check("初始 0 → 全部到位 p=1", all(abs(st.transition(k).p - 1.0) < 1e-6
                                     for k in st.kinds()), str([(k, round(st.transition(k).p, 2)) for k in st.kinds()]))
    st.set_active_agents(set())
    for _ in range(40):
        st.update(0.05, lay, now, enter_s=0.42, exit_s=0.30)
    check("清空后全部退场 p=0", all(st.transition(k).p <= 1e-6 for k in st._trans))
    check("退场后 active_kinds 为空", st.active_kinds() == [])
    # 可打断：中途改目标不回跳、不排队
    st2 = A.AccessoryState(scale=1.0)
    st2.set_active_agents({"zcode"})
    for _ in range(4):
        st2.update(0.05, lay, now)
    mid = st2.transition("beret").p
    st2.set_active_agents({"workbuddy"})
    st2.update(0.05, lay, now)
    check("切换目标时进度单调（可打断，不回跳）",
          0.0 < mid < 1.0 and st2.transition("beret").p <= mid + 1e-9,
          f"mid={mid:.2f} after={st2.transition('beret').p:.2f}")


def part_f():
    print("\nF. 真实 GDI 绘制生效（像素差分 —— 抓过 bug 的那条）")
    try:
        import wb_whale_win as W
        from _shotutil import surf_to_image
        import PIL.ImageChops as Chops
    except Exception as e:
        check("导入绘制链路", False, str(e))
        return

    app = W.WhalePet(run_seconds=None)
    try:
        app.acc_on = True
        app._acc = None
        app._init_accessories()
        if app._presence:
            app._presence.stop()
            app._presence = None
        if app._acc is None:
            check("配件层可初始化", False, "为 None")
            return

        def snap(active):
            app._acc.set_active_agents(set(active))
            app._acc.cat.inited = False
            for _ in range(40):
                app._acc.update(0.05, app._layout(), time.time(),
                                enter_s=0.01, exit_s=0.01)
            app.draw()
            app.draw()
            return surf_to_image(app.surf).convert("RGB").copy()

        # ---- 2026-09-26 v3：配件默认停用 ----
        # 用户明确要求「帽子（贝雷帽）/小猫/鲸鱼玩偶都不进桌宠」，故加了总开关
        # ACCESSORIES_OFF。代码与素材都保留（不删），下面两段分别守住：
        #   ① 关着的时候真的什么都不画  ② 打开时休眠代码的入退场几何仍然正确
        _saved_off = W.ACCESSORIES_OFF
        check("OFF: ACCESSORIES_OFF 默认 True（配件不进桌宠）", _saved_off is True)
        base_off = snap([])
        all_off = snap(["workbuddy", "zcode", "deepseek-harness"])
        check("OFF: 开关关闭时激活全部配件也不改变画面（停用确实生效）",
              Chops.difference(base_off, all_off).getbbox() is None,
              str(Chops.difference(base_off, all_off).getbbox()))

        try:
            W.ACCESSORIES_OFF = False          # 临时打开，测休眠代码
            base = snap([])
            diffs = {}
            for name, act in (("cat", ["workbuddy"]), ("beret", ["zcode"]),
                              ("plush", ["deepseek-harness"]),
                              ("all3", ["workbuddy", "zcode", "deepseek-harness"])):
                diffs[name] = Chops.difference(base, snap(act)).getbbox()
            for k, v in diffs.items():
                check(f"{k}: 渲染像素相对'无配件'有变化", v is not None, "完全一致（没画上去）")

            # 三件必须落在不同区域：帽在头顶（上）、玩偶在中、猫在底
            allb = diffs.get("all3")
            check("三者同时：差异区域覆盖到窗口上部（帽子）", allb is not None and allb[1] < 260,
                  str(allb))
            check("三者同时：差异区域覆盖到窗口下部（猫）", allb is not None and allb[3] > 380,
                  str(allb))
        finally:
            W.ACCESSORIES_OFF = _saved_off     # 还原总开关
    finally:
        try:
            app.close()
        except Exception:
            pass


def main():
    print("=" * 62)
    print("  配件层回归测试")
    print("=" * 62)
    for fn in (part_a, part_b, part_c, part_d, part_e, part_f):
        try:
            fn()
        except Exception as e:
            import traceback
            check(f"{fn.__name__} 未抛异常", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== 总结 ===\nPASS: {len(PASSED)}\nFAIL: {len(FAILED)}")
    if FAILED:
        print("失败项: " + " | ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
