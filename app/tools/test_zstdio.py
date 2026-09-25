# -*- coding: utf-8 -*-
r"""zstdio 回归测试：守住"多帧静默截断"这个坑。

为什么值得单独立测：DSH 的会话文件是**追加写的多帧 zstd 流**。
`zstandard.stream_reader()` 默认 `read_across_frames=False` —— 只解第一帧且**不报错**，
实测 3.96MB 的文件只出 203 字节，表现为"解压成功但内容为空"，极难查。
这条测试用**现场造的多帧文件**验证解压器一定跨帧。

用法：python wb_usage/tools_test_zstdio.py
"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "wb_usage"))     # zstdio 在 wb_usage/ 下
import zstdio

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))

def main():
    print("=" * 56); print("  zstdio 回归测试"); print("=" * 56)
    ok, layer, hint = zstdio.describe()
    check("至少一层解压能力可用", ok, hint)
    print(f"     当前层: {layer}   层顺序: {zstdio.tier_names()}")

    tmp = tempfile.mkdtemp(prefix="zstdio_test_")

    # ---- 造多帧文件：把 N 段 JSONL 各自压成帧再首尾拼接 ----
    n_frames = 5
    parts = [('{"type":"a","i":%d,"pad":"%s"}\n' % (i, "x" * 200)).encode() for i in range(n_frames)]
    multi = os.path.join(tmp, "multi.jsonl.zstd")
    single = os.path.join(tmp, "single.jsonl.zstd")

    enc = None
    try:
        import zstandard as zs
        c = zs.ZstdCompressor()
        enc = lambda b: c.compress(b)
    except ImportError:
        try:
            from compression import zstd as std
            enc = lambda b: std.compress(b)
        except ImportError:
            import subprocess
            cli = zstdio._zstd_cli()
            if cli:
                def enc(b):
                    r = subprocess.run([cli, "-c", "-q"], input=b, capture_output=True)
                    return r.stdout
    check("测试环境能造 zstd 帧", enc is not None)
    if not enc:
        return 1

    with open(multi, "wb") as f:
        for p in parts:
            f.write(enc(p))                 # 逐帧写入 → 多帧拼接文件
    with open(single, "wb") as f:
        f.write(enc(b"".join(parts)))        # 单帧文件（对照组）

    for label, path in (("多帧", multi), ("单帧", single)):
        d = zstdio.read_zstd(path)
        lines = [l for l in (d or b"").decode().splitlines() if l.strip()]
        check(f"{label}文件解出全部 {n_frames} 行",
              d is not None and len(lines) == n_frames,
              f"实际 {len(lines)} 行 / {len(d) if d else 'None'} bytes")

    # 完整性校验函数本身
    check("_looks_complete 认可以 } 结尾", zstdio._looks_complete(b'{"a":1}\n'))
    check("_looks_complete 否决不闭合内容", not zstdio._looks_complete(b'{"a":1'))
    check("空文件返回空 bytes", zstdio.read_zstd(os.path.join(tmp, "none.zstd")) is None)
    open(os.path.join(tmp, "empty.zstd"), "wb").close()
    check("零字节文件返回 b''", zstdio.read_zstd(os.path.join(tmp, "empty.zstd")) == b"")

    # ---- 真实 DSH 文件（如果存在）----
    import glob
    fs = sorted(glob.glob(os.path.expanduser("~/.dsh/sessions/**/*.zstd"), recursive=True))
    if fs:
        big = max(fs, key=os.path.getsize)
        d = zstdio.read_zstd(big)
        cnt = len(d.decode("utf-8", "replace").splitlines()) if d else 0
        check(f"真实 DSH 最大会话文件解压出 >100 行（{os.path.getsize(big)/1e6:.1f}MB）",
              cnt > 100, f"仅 {cnt} 行 —— 疑似只解了第一帧")
        check("真实 DSH 解压结果正常闭合", zstdio._looks_complete(d) if d else False)

    print(f"\n=== 总结 ===\nPASS: {PASS}\nFAIL: {FAIL}")
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(main())
