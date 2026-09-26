# -*- coding: utf-8 -*-
"""Cubism 导出产物自检：核对参数名 / moc3 版本 / 文件完整性。

用法：
    python tools/check_cubism_export.py "D:\\...\\古见同学\\古见同学.model3.json"
或指定目录（自动找 .model3.json）：
    python tools/check_cubism_export.py "D:\\workbuddy\\古见同学桌宠\\live2d\\model\\古见同学"

为什么需要它：参数名对不上时，运行时**驱动不了却不会报错**（表现是"角色不动"），
排查很痛苦。这个脚本在导入前就把它扫出来。
moc3 里以明文存着参数字符串，可以直接搜。
"""
import json
import os
import sys
from collections import Counter

# 运行时会用到的参数（缺哪个=[x]，运行时对应功能就是死的）
REQUIRED = ["ParamBreath", "ParamEyeLOpen", "ParamEyeROpen", "ParamAngleZ"]
LATER = ["ParamAngleX", "ParamAngleY", "ParamEyeBallX", "ParamEyeBallY",
         "ParamMouthOpenY", "ParamBrowLY", "ParamBrowRY",
         "ParamBodyAngleX", "ParamBodyAngleZ", "ParamHairFront", "ParamSkirt"]
VERSION = {3: "Cubism 3", 4: "Cubism 4", 5: "Cubism 5"}


def find_model3(path):
    if os.path.isfile(path) and path.endswith(".model3.json"):
        return path
    if os.path.isdir(path):
        for f in os.listdir(path):
            if f.endswith(".model3.json"):
                return os.path.join(path, f)
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    m3 = find_model3(sys.argv[1])
    if not m3:
        print("❌ 没找到 .model3.json。Cubism 导出时请勾选「嵌入用文件(moc3)」")
        return
    d = os.path.dirname(m3)
    print(f"模型配置: {m3}\n")

    data = json.load(open(m3, encoding="utf-8"))
    ref = data.get("FileReferences", {})
    ok = True

    # 1) 文件完整性
    print("── 文件清单 ──")
    files = []
    if ref.get("Moc"):
        files.append(("moc3", ref["Moc"]))
    for t in ref.get("Textures", []):
        files.append(("贴图", t))
    for k in ("Physics", "Pose", "DisplayInfo", "UserData"):
        if ref.get(k):
            files.append((k, ref[k]))
    for kind, rel in files:
        p = os.path.join(d, rel)
        exists = os.path.isfile(p)
        size = (os.path.getsize(p) // 1024) if exists else 0
        print(f"  {'✅' if exists else '❌'} {kind:10} {rel}  {size}KB")
        ok &= exists

    # 2) moc3 版本
    moc = os.path.join(d, ref.get("Moc", ""))
    if os.path.isfile(moc):
        b = open(moc, "rb").read(8)
        ver = b[4] if b[:4] == b"MOC3" else None
        print(f"\n── moc3 ──\n  版本字节 {ver} → {VERSION.get(ver, '未知')}"
              f"   （l2d 内置 Cubism 6 Core，兼容 v3/4/5）")

    # 3) 参数名（在 moc3 里以明文出现）
    print("\n── 参数名核对 ──")
    if os.path.isfile(moc):
        raw = open(moc, "rb").read()
        def has(name):
            return name.encode("utf-8") in raw or name.encode("utf-16-le") in raw
        miss_req = [p for p in REQUIRED if not has(p)]
        for p in REQUIRED:
            print(f"  {'✅' if has(p) else '❌'} {p}   ← 最小模型必需")
        if miss_req:
            ok = False
            print(f"\n  ⚠️ 缺必需参数: {miss_req}")
            print("     → 回 Cubism 的「参数」面板逐个核对拼写（大小写也要一致）")
        else:
            print("\n  ✅ 最小模型所需参数齐全")
        present_later = [p for p in LATER if has(p)]
        if present_later:
            print(f"  （已额外建好: {', '.join(present_later)}）")
    else:
        print("  ⚠️ 没有 moc3，无法核对参数名")
        ok = False

    # 4) 命中区（可选，运行时做分区点击用）
    areas = ref.get("HitAreas") or []
    print(f"\n── 命中区（可选，用于分区点击）──\n  "
          + (", ".join(a.get("Name", "?") for a in areas) if areas else "未定义——建议在 Cubism 里加 Head/Body/Skirt 三个"))

    print("\n" + ("✅ 自检通过，可以接运行时了 —— 把导出目录发我" if ok else "❌ 有问题，按上面提示回去改"))


if __name__ == "__main__":
    main()
