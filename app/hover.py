#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hover.py — WorkBuddy 用量桌宠统一入口（跨平台自动分发）
=======================================================
取代直接调用 b_hover.py / b_hover_win.py / wb_whale_win.py，安装脚本与开机自启都用它：

  pythonw hover.py                 启动（默认形态见下）
  pythonw hover.py --classic       回退：经典 88px 圆形悬浮球（Windows）
  python  hover.py --seconds 20    冒烟测试：20 秒后自动退出

分发规则：
  macOS    → b_hover.py      （原生 Cocoa NSPanel，需 pyobjc）
  Windows  → wb_whale_win.py （古见同学桌宠：想法气泡三行用量 + Q 版双版本立绘，
                              纯 ctypes 零依赖；含 OK 完成态与 WorkBuddy 联动关闭）
             （加 --classic 则用 b_hover_win.py 经典圆球，仍保留可回退）
  其他      → 无原生实现，提示改用 Web 看板
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main():
    if sys.platform == "darwin":
        import b_hover            # noqa: E402  需要 pyobjc（install.py 会自动准备）
        b_hover.main()
        return 0
    if os.name == "nt":
        if "--classic" in sys.argv:
            import b_hover_win    # noqa: E402  经典圆球（保留回退）
            sys.argv.remove("--classic")
            return b_hover_win.main()
        import wb_whale_win       # noqa: E402  古见同学桌宠（默认）
        return wb_whale_win.main()
    sys.stderr.write(
        "[wb-hover] 当前平台（{}）没有原生桌宠实现，Web 看板仍可正常使用：\n"
        "    python3 {}\n".format(sys.platform,
                                  os.path.join(HERE, "wb_usage", "wb_api.py")))
    return 2


if __name__ == "__main__":
    sys.exit(main())
