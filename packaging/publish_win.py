#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_win.py — 生成自更新清单 `update.json`（发布用）
================================================================================
发新版就三步：

    1. 改 app/wb_version.py 里的 VERSION
    2. <venv>/python.exe packaging/build_win.py          # 出 zip
    3. <venv>/python.exe packaging/publish_win.py        # 出 update.json（含 sha256）

然后把 **zip + update.json 两个文件**传到任意静态位置（GitHub Release 资产、
对象存储、内网共享、甚至一个本地文件夹），再让客户端指过去：

    古见同学桌宠.exe --set-update-source <那个地址>

> 地址可以是「目录」也可以是「update.json 本身」：给目录时程序自己拼 `/update.json`。
> 清单里的包地址默认写**相对路径** —— 两个文件放一起就行，不用改。

清单格式（`win` 段是平台专属；顶层也接受同名字段，方便手工写）：
```json
{
  "version": "1.1.0",
  "notes": "修了……",
  "published": "2026-09-28T10:00:00",
  "win": {"url": "古见同学桌宠-Windows-1.1.0.zip",
          "sha256": "…", "size": 46344250}
}
```
"""

import argparse
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, os.path.join(ROOT, "app"))
import wb_version as V          # noqa: E402


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_zip(version=None):
    ver = version or V.VERSION
    exact = os.path.join(DIST, f"{V.APP_NAME}-Windows-{ver}.zip")
    if os.path.isfile(exact):
        return exact
    cands = sorted((os.path.join(DIST, f) for f in os.listdir(DIST)
                    if f.startswith(V.APP_NAME + "-Windows-") and f.endswith(".zip")
                    and "1.0.0" not in f or ver in f), reverse=True) if os.path.isdir(DIST) else []
    for c in cands:
        if ver in os.path.basename(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser(description="生成自更新清单 update.json")
    ap.add_argument("--notes", default="", help="更新说明（显示给用户）")
    ap.add_argument("--url", default=None,
                    help="包地址（默认写相对文件名；也可给绝对 URL）")
    ap.add_argument("--url-base", default=None,
                    help="把相对文件名拼到这个基址上（例：https://example.com/komi/）")
    ap.add_argument("--zip", default=None, help="指定要发布的 zip（默认按版本号找 dist/ 里的）")
    ap.add_argument("--extra", default=None, help='额外字段（JSON 字符串，比如 {"min_version":"1.0.0"}）')
    args = ap.parse_args()

    zip_path = args.zip or find_zip()
    if not zip_path or not os.path.isfile(zip_path):
        print(f"❌ 找不到要发布的 zip。先跑 packaging/build_win.py，"
              f"或用 --zip 指定。当前版本 v{V.VERSION}")
        return 1

    name = os.path.basename(zip_path)
    url = args.url or name
    if args.url_base and not url.startswith(("http://", "https://", "file://")):
        url = args.url_base.rstrip("/") + "/" + url

    size = os.path.getsize(zip_path)
    sha = sha256_file(zip_path)
    man = {
        "version": V.VERSION,
        "notes": args.notes or f"古见同学桌宠 v{V.VERSION}",
        "published": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "win": {"url": url, "sha256": sha, "size": size},
    }
    if args.extra:
        try:
            man.update(json.loads(args.extra))
        except Exception as e:
            print(f"⚠️ --extra 不是合法 JSON，已忽略：{e}")

    out = os.path.join(DIST, V.MANIFEST_NAME)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("=" * 62)
    print(f"  发布清单已生成 · {V.APP_NAME} v{V.VERSION}")
    print("=" * 62)
    print(f"  包    : {zip_path}  ({size / 1e6:.1f} MB)")
    print(f"  sha256: {sha}")
    print(f"  清单  : {out}")
    print()
    print("  接下来：把这两个文件传到同一个位置，然后让客户端指过去：")
    print(f"    {name}")
    print(f"    {V.MANIFEST_NAME}")
    print(f"    {V.EXE_NAME} --set-update-source <目录地址或 update.json 地址>")
    print()
    print("  客户端会：检查版本 → 下载 → 校验 sha256 → 用新版本替换自己 → 自动重启")
    return 0


if __name__ == "__main__":
    sys.exit(main())
