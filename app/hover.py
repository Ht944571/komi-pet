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

import wb_runtime as RT                                        # noqa: E402


def _cleanup_async():
    """启动时在后台清掉上一轮自更新留下的 staged 目录。

    为什么不在更新进程里删：那时 staged 里的 exe 正在跑（自己），删不掉。
    所以由**更新后新起的实例**顺手清 —— 放在后台线程里，绝不拖慢启动。
    """
    def job():
        try:
            import wb_update
            wb_update.cleanup_pending()
        except Exception:
            pass
    import threading
    threading.Thread(target=job, daemon=True).start()


def _update_dispatch(argv):
    """自更新相关参数。返回 None = "不是更新命令，继续走正常流程"。

    这些命令**源码运行也能用**（`--check-update` / `--set-update-source` 与打包无关），
    但 `--update` / `--apply-update` 只有在打包版里才有意义（没有 exe 可替换）。
    """
    import wb_version as VER

    def val(flag):
        i = argv.index(flag)
        return argv[i + 1] if i + 1 < len(argv) else None

    if "--set-update-source" in argv:
        import wb_update
        p = wb_update.set_source(val("--set-update-source") or "")
        print(f"更新源已写入：{p}")
        print("（留空则清空 → 恢复「默认不联网」）")
        return 0

    if "--check-update" in argv:
        import wb_update
        info = wb_update.check(val("--source") if "--source" in argv else None)
        print(f"当前版本：{VER.display()}")
        print(f"更新源　：{info['src'] or '(未配置)'}")
        print(info["msg"])
        m = info.get("manifest")
        if m and info.get("available"):
            if m.get("notes"):
                print(f"更新说明：{m['notes']}")
            print(f"UPDATE_AVAILABLE={m['version']}")
        elif m:
            print("UPDATE_NONE")
        else:
            print("UPDATE_UNKNOWN")
        return 0

    if "--update" in argv:
        import wb_update
        if not RT.FROZEN:
            print("源码运行不支持自更新（没有可替换的 exe）。")
            print("  想看有没有新版：--check-update；想测完整流程：用打包版。")
            return 2
        try:
            m = wb_update.run_apply_new(val("--source") if "--source" in argv else None,
                                        progress=lambda got, total: None)
        except wb_update.UpdateError as e:
            print(f"❌ 更新失败：{e}")
            return 1
        print(f"✅ v{m['version']} 已下载并解压。")
        print("   接下来由**新版本自己**替换并重启（本程序现在要退出，好让文件解锁）。")
        print("   几秒后桌宠会自动回来；出问题看 <数据目录>/update/update.log")
        return 0

    if "--apply-update" in argv:
        import wb_update
        a = val("--from") if "--from" in argv else None
        b = val("--to") if "--to" in argv else None
        if not a or not b:
            print("--apply-update 需要 --from <staged> --to <install>")
            return 2
        return 0 if wb_update.apply_update(a, b) else 1

    return None


def _frozen_dispatch(argv):
    """**打包成 exe 后**的入口分发：一个 exe + 子命令。

    命令由 `wb_runtime.self_cmd()` / `hidden_launcher()` 生成：
      --pet（默认） 桌宠本体     · --watcher 守望     · --api [--port N] 看板服务
      --install / --uninstall / --status / --check   安装维护（见 wb_setup）
    源码运行时不会走到这里（保持原有平台分发不变）。
    """
    def has(f):
        return f in argv

    if has("--install") or has("--uninstall") or has("--status") or has("--check"):
        import wb_setup
        act = ("uninstall" if has("--uninstall") else
               "status" if has("--status") else
               "check" if has("--check") else "install")
        port = 8801
        if "--port" in argv:
            try:
                port = int(argv[argv.index("--port") + 1])
            except Exception:
                pass
        return wb_setup.main([act, "--port", str(port)])

    if has("--watcher"):
        RT.free_console()
        _cleanup_async()
        import wb_whale_watcher
        return wb_whale_watcher.main([])

    if has("--api"):
        # API 的 argparse 很严格 → 必须把 --api 摘掉再把余下参数原样传下去
        # （控制台不摘：从 cmd 手动起时日志要看得见；被桌宠守护拉起时本来就没有控制台）
        sys.path.insert(0, os.path.join(RT.bundle_dir(), "wb_usage"))
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--api"]
        import wb_api
        return wb_api.main()

    RT.free_console()          # 桌宠：双击 exe 不留黑窗
    _cleanup_async()
    import wb_whale_win
    return wb_whale_win.main()


def main():
    argv = sys.argv[1:]
    RT.init_console()                          # 中文 Windows 控制台默认 936 → 会乱码/报错
    r = _update_dispatch(argv)                 # 自更新相关（两种运行形态都支持检查）
    if r is not None:
        return r
    if RT.FROZEN:
        return _frozen_dispatch(argv)
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
