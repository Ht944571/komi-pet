# -*- coding: utf-8 -*-
"""在 Krita 内执行（krita.exe --script 本文件）：
打开自动拆层的 .ora → 校验图层结构 → 导出 Cubism 可导入的 PSD + PNG 预览 → 退出。
结果写到 json（Krita 的 stdout 拿不到，用文件核对）。
"""
import json
import os

RESULT = r"D:\workbuddy\古见同学桌宠\live2d\_work\krita_export_result.json"
ORA = r"D:\workbuddy\古见同学桌宠\live2d\拆层_自动_v1.ora"
PSD = r"D:\workbuddy\古见同学桌宠\live2d\拆层_古见同学_v1.psd"
PNG = r"D:\workbuddy\古见同学桌宠\live2d\preview.png"

res = {"ok": False, "steps": []}


def log(m):
    res["steps"].append(m)


try:
    from krita import Krita, InfoObject

    ki = Krita.instance()
    log("krita: " + str(ki.applicationName()) + " / " + str(ki.version()))

    doc = ki.openDocument(ORA)
    if doc is None:
        raise RuntimeError("openDocument 返回 None（.ora 打不开？）")
    log("opened {}x{}".format(doc.width(), doc.height()))

    nodes = doc.topLevelNodes()
    names = [n.name() for n in nodes]
    res["layer_names"] = names
    res["layer_types"] = [n.type() for n in nodes]
    res["visible"] = [bool(n.visible()) for n in nodes]
    log("layers({}): {}".format(len(nodes), " | ".join(names)))

    # 导出 PSD（Cubism Editor 只认 PSD）
    doc.setBatchmode(True)                       # 抑制所有对话框
    doc.saveAs(PSD)
    res["psd_size"] = os.path.getsize(PSD) if os.path.isfile(PSD) else 0
    log("psd: {} bytes".format(res["psd_size"]))

    # PNG 预览（合成效果）
    io = InfoObject()
    io.alpha = True
    doc.exportImage(PNG, io)
    res["png_size"] = os.path.getsize(PNG) if os.path.isfile(PNG) else 0
    log("png: {} bytes".format(res["png_size"]))

    doc.close()
    log("doc closed")
except Exception as e:
    log("ERROR: " + repr(e))
finally:
    res["ok"] = res.get("psd_size", 0) > 1000
    try:
        os.makedirs(os.path.dirname(RESULT), exist_ok=True)
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

# 跑完退出 Krita（这是我自己拉起的实例，不该留在用户桌面上）
try:
    from PySide6.QtWidgets import QApplication
    QApplication.quit()
except Exception:
    try:
        from PyQt5.QtWidgets import QApplication  # noqa: F401
        QApplication.quit()
    except Exception:
        pass
