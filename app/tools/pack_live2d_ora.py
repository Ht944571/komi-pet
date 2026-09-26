# -*- coding: utf-8 -*-
"""把自动拆层的 13 张 PNG 打包成**带内容的 .ora**（在空骨架基础上填充）。

产出：live2d/拆层_自动_v1.ora
用法：Krita 打开 → 在这版底稿上修（比从零画省 70% 工作量）。
"""
import os
import io as _io
import json
import zipfile
import numpy as np
from PIL import Image, ImageDraw

ROOT = r"D:\workbuddy\古见同学桌宠\live2d"
LAY = os.path.join(ROOT, "_work", "layers")
ORA = os.path.join(ROOT, "拆层_自动_v1.ora")
CANVAS = 1024

# 与骨架一致的图层顺序（上→下）；None = 留空
STACK = [
    ("01_发饰呆毛_可选 [可选]", None),
    ("02_前发 [★必做]", "02_前发.png"),
    ("03_眉 [本设计并入04，留空]", None),
    ("04_眼_上睑 [★必做]", "04_眼_上睑.png"),
    ("05_眼_瞳孔高光 [★必做]", "05_眼_瞳孔高光.png"),
    ("06_眼_眼白 [★必做]", "06_眼_眼白.png"),
    ("07_眼_下睑 [★必做·可后补]", None),
    ("08_鼻_可选 [可选]", None),
    ("09_嘴 [★必做]", "09_嘴.png"),
    ("09b_嘴_张形 [★必做·Cubism里做插值]", "09_嘴_张形.png"),
    ("10_脸_肤色底 [★必做]", "10_脸_肤色底.png"),
    ("11_腮红 [可选]", "11_腮红.png"),
    ("12_身体制服 [★必做]", "12_身体制服.png"),
    ("13_手臂左右_可选 [可选·已并入12]", None),
    ("14_裙摆 [★必做]", "14_裙摆.png"),
    ("15_腿袜 [★必做]", "15_腿袜.png"),
    ("16_后发 [★必做]", "16_后发.png"),
    ("17_补块_被遮挡区 [★必做]", "17_补块_被遮挡区.png"),
    ("90_参考_三视图 [参考]", "__REF__"),
]


def main():
    ref = Image.open(os.path.join(ROOT, "reference", "front.png")).convert("RGBA")
    comp = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 255))
    x = 20
    d = ImageDraw.Draw(comp)
    for name, p in (("FRONT", "front.png"), ("SIDE", "side.png"), ("BACK", "back.png")):
        im = Image.open(os.path.join(ROOT, "reference", p))
        s = min(300 / im.width, 620 / im.height)
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
        comp.paste(im, (x, 300 - im.height // 2), im)
        d.text((x + 4, 20), name, fill=(60, 50, 70))
        x += im.width + 30
    a = np.array(comp)
    a[..., :3] = (a[..., :3].astype(np.float32) * 0.82 + 255 * 0.18).astype(np.uint8)
    ref_layer = Image.fromarray(a, "RGBA")
    ref_layer.putalpha(ref_layer.getchannel("A").point(lambda v: int(v * 0.30)))

    stack, data = [], []
    n = 0
    for name, png in STACK:
        n += 1
        fn = f"data/layer{n:03d}.png"
        if png == "__REF__":
            img, op = ref_layer, 0.32
        elif png and os.path.isfile(os.path.join(LAY, png)):
            img = Image.open(os.path.join(LAY, png)).convert("RGBA")
            op = 1.0
        else:
            img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
            op = 1.0
        b = _io.BytesIO(); img.save(b, "PNG")
        data.append((fn, b.getvalue()))
        stack.append(f'    <layer name="{name}" src="{fn}" x="0" y="0" '
                     f'opacity="{op}" visibility="visible" composite-op="svg:src-over"/>')

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<image version="0.0.3" w="{CANVAS}" h="{CANVAS}" xres="72" yres="72">\n'
           '  <stack>\n' + "\n".join(stack) + '\n  </stack>\n</image>\n')
    thumb = Image.new("RGB", (256, 256), (245, 244, 248))
    tt = Image.open(os.path.join(ROOT, "_work", "s2_composite.png")).convert("RGBA")
    tt.thumbnail((256, 256))
    thumb.paste(tt, ((256 - tt.width) // 2, (256 - tt.height) // 2), tt)
    tb = _io.BytesIO(); thumb.save(tb, "PNG")

    with zipfile.ZipFile(ORA, "w", zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "image/openraster")
        z.writestr("stack.xml", xml)
        for fn, b in data:
            z.writestr(fn, b)
        z.writestr("Thumbnails/thumbnail.png", tb.getvalue())
    # 顺手把清单写成 json（谁生成了什么，方便核对）
    json.dump({n: p for n, p in STACK if p and p != "__REF__"},
              open(os.path.join(ROOT, "_work", "layer_map.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("→", ORA, os.path.getsize(ORA) // 1024, "KB", f"({len(STACK)} 层)")


if __name__ == "__main__":
    main()
