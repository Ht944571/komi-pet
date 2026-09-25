# -*- coding: utf-8 -*-
r"""视觉交互改进回归测试（自动化验证，不依赖人工点击）

验证点（对应提案）：
  1. 待机动效：呼吸 / 尾鳍 / 漂浮 位移非零（§1）
  2. 眨眼状态机可触发（§1）
  3. 情绪切换：成功 / 失败 / 空数据 / 待机 状态正确（§2）
  4. 数据情绪同步：服务异常→fail，空数据→empty，正常→neutral（§2）
  5. 按压压缩曲线压扁（§3）
  6. 闭眼眼睑 + 分层位移绘制不抛异常（§1/§4）
  7. 自动降级 full→lite（§6）

用法：python tools/test_motion.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_whale_win as W          # noqa: E402
import wb_motion as MOTION        # noqa: E402


def main():
    app = W.WhalePet(run_seconds=22)
    app._save_settings = lambda: None          # 不写用户 settings
    app.open_dashboard = lambda *a, **k: None  # 不开真实浏览器

    report = {}

    def scenario():
        time.sleep(2.5)
        now = time.time()

        # ① 待机动效：多帧采样取最大值（正弦曲线会过零）
        breath_vals, tail_vals, float_vals = [], [], []
        for dt in (0.0, 0.2, 0.4, 0.6):
            app._update_motion(now + dt)
            breath_vals.append(app._breath)
            tail_vals.append(abs(app._tail_dx))
            float_vals.append(abs(app._float_dy))
        report["breath_max"] = round(max(breath_vals), 2)
        report["tail_max"] = round(max(tail_vals), 2)
        report["float_max"] = round(max(float_vals), 2)
        report["eye_cfg"] = bool(app._eye_cfg)

        # ② 眨眼：直接构造眨眼态（v3 BlinkScheduler 用 blink_t0/blink_total）
        app._blinker.blink_t0 = now
        app._blinker.blink_total = MOTION.BLINK_TOTAL_S
        report["blink_closing"] = app._blinker.eye_opening_ratio(now + 0.03) < 1.0

        # ③ 情绪切换
        app.set_emotion(MOTION.EMOTION_SUCCESS)
        report["emotion_success"] = app._emotion
        report["success_particles"] = len(app._particles)
        app.set_emotion(MOTION.EMOTION_FAIL)
        report["emotion_fail"] = app._emotion

        # ④ 数据情绪同步（从 neutral 出发验证转换逻辑）
        app._emotion = MOTION.EMOTION_NEUTRAL
        app.db_ok = False
        app.api_ok = True        # api_ok=None 会被 _sync_data_emotion 当作坏，先设 True
        app._sync_data_emotion()
        report["sync_fail"] = app._emotion
        app.db_ok = True
        app.active = []
        app._sync_data_emotion()
        report["sync_empty"] = app._emotion
        app.active = [{"x": 1}]
        app._sync_data_emotion()
        report["sync_neutral"] = app._emotion

        # ⑤ 按压压缩曲线
        app._squash_t0 = time.time()
        report["squash_min"] = round(app._squash_curve(time.time() + 0.05), 3)

        # ⑥ 闭眼眼睑 + 分层位移绘制不抛异常
        app._emotion = MOTION.EMOTION_NEUTRAL
        app._squash_t0 = 0.0
        app._blinker.blink_t0 = time.time() + 0.5
        app._blinker.blink_total = MOTION.BLINK_TOTAL_S
        app._gaze_dx = 1.0
        app._tail_dx = 1.5
        try:
            app.draw()
            report["draw_with_blink_tilt"] = True
        except Exception as e:                    # noqa: BLE001
            report["draw_with_blink_tilt"] = repr(e)

        # ⑦ 自动降级（monkeypatch 了 _save_settings，不会落盘）
        app._quality = MOTION.QUALITY_FULL
        app._frame_ms = 20.0                      # > 8ms 阈值
        for _ in range(3):
            app._auto_degrade(time.time())
        report["auto_degrade"] = app._quality

        W._user32.PostQuitMessage(0)

    threading.Thread(target=scenario, daemon=True).start()
    app.run()

    print("==== 视觉交互改进回归测试 ====")
    for k, v in report.items():
        print(f"{k:22s}: {v}")

    ok = True
    checks = [
        (report.get("breath_max", 0) > 0, "呼吸位移无值"),
        (report.get("tail_max", 0) > 0, "尾鳍位移无值"),
        (report.get("float_max", 0) > 0, "漂浮位移无值"),
        (report.get("eye_cfg") is True, "眼部配置未加载（眨眼禁用）"),
        (report.get("blink_closing") is True, "眨眼未触发"),
        (report.get("emotion_success") == MOTION.EMOTION_SUCCESS, "成功情绪状态错误"),
        ((report.get("success_particles") or 0) > 0, "成功情绪无粒子"),
        (report.get("emotion_fail") == MOTION.EMOTION_FAIL, "失败情绪状态错误"),
        (report.get("sync_fail") == MOTION.EMOTION_FAIL, "数据同步：服务异常未映射到 fail"),
        (report.get("sync_empty") == MOTION.EMOTION_EMPTY, "数据同步：空数据未映射到 empty"),
        (report.get("sync_neutral") == MOTION.EMOTION_NEUTRAL, "数据同步：正常未回落 neutral"),
        (0 < report.get("squash_min", 1.0) < 1.0, "按压压缩曲线未压扁"),
        (report.get("draw_with_blink_tilt") is True, "闭眼+分层绘制抛异常"),
        (report.get("auto_degrade") == MOTION.QUALITY_LITE, "自动降级未触发 full→lite"),
    ]
    for passed, msg in checks:
        if not passed:
            print("FAIL:", msg)
            ok = False
    print("结论:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
