# -*- coding: utf-8 -*-
"""
zstdio.py — zstd 解压的四层降级
================================================================

背景：DeepSeek Harness 的会话是 `session.jsonl.zstd`，而 **Python 3.13 没有 stdlib zstd**
（`compression.zstd` 是 3.14 才进标准库的）。所以这里做**四层降级**，
任何一层可用即可工作，全不可用则**明确报告不可用**（而不是猜或崩）：

  1. `compression.zstd`（Python 3.14+ 标准库）
  2. `zstandard`（第三方包，`pip install zstandard`）
  3. `zstd` 命令行（若系统装了）
  4. 都没有 → 返回 None，调用方跳过该数据源并提示怎么装

两个必须知道的细节
------------------
**① 必须流式解压，不能一锤子 `decompress()`.**
DSH 的会话文件是**追加写**的，于是是**多帧拼接**的 zstd 流。
实测：一个 23 KB 的文件用 `ZstdDecompressor().decompress()` 只解出 203 字节
（只有第一帧）；改用 `stream_reader()` 才拿到全部内容。单帧解压**不会报错**，
只会静默给出截断的结果 —— 这种"看起来成功"的失败最难查。

**② 这里跑在采集线程里，不在消息循环。**
命令行降级要用 subprocess，属于阻塞调用。本项目有明确教训：**绝不要把阻塞调用
放进 Win32 消息循环**（曾把带超时的网络请求放进 timer tick，消息循环每秒被卡死半秒）。
采集器本身就在后台线程，所以这里安全。
"""

import os
import subprocess
import sys

# 命令行 zstd 的常见位置（PATH 里没有时兜底；~ 展开到当前用户家目录）
_ZSTD_CLI_HINTS = tuple(filter(None, (
    os.path.expanduser(r"~/anaconda3/Library/bin/zstd.exe"),
    os.path.expanduser(r"~/miniconda3/Library/bin/zstd.exe"),
    r"C:\Program Files\zstd\zstd.exe",
    "/usr/bin/zstd", "/usr/local/bin/zstd", "/opt/homebrew/bin/zstd",
)))

MAX_OUTPUT = 512 * 1024 * 1024          # 解压上限（防炸弹/异常文件）


def _tier():
    """返回当前可用的最高层：(名称, 解压函数) 或 (None, None)。"""
    # 1) 标准库（Python 3.14+）
    try:
        from compression import zstd as _std                    # noqa: F401
        return "stdlib:compression.zstd", None                   # 用路径形式，见 read_zstd
    except Exception:
        pass
    # 2) 第三方包
    try:
        import zstandard                                        # noqa: F401
        return "zstandard", None
    except Exception:
        pass
    # 3) 命令行
    if _zstd_cli():
        return "cli:zstd", None
    return None, None


def _zstd_cli():
    from shutil import which
    w = which("zstd")
    if w:
        return w
    for p in _ZSTD_CLI_HINTS:
        if os.path.isfile(p):
            return p
    return None


def describe():
    """返回 (是否可用, 层名, 安装提示)。"""
    name, _ = _tier()
    if name:
        return True, name, ""
    return (False, "",
            "无 zstd 解压能力。三选一：\n"
            "  ① 用 Python 3.14+（自带 compression.zstd）\n"
            "  ② pip install -i https://mirrors.aliyun.com/pypi/simple/ zstandard\n"
            "  ③ 安装 zstd 命令行并加入 PATH")


def _looks_complete(b):
    """粗判解压结果是否完整：JSONL 流的结尾必然是一个闭合的 } 或 ]。

    为什么需要：截断是**静默**的（不报错），只靠"返回了非空 bytes"判断会被骗。
    这条校验专门用来发现"只解出第一帧"这类截断。
    """
    if not b:
        return False
    t = b.rstrip()
    return t.endswith(b"}") or t.endswith(b"]")


def _tier1_stdlib(path):
    try:
        from compression import zstd as std
    except ImportError:
        return None
    # 实测（Python 3.14）：open() 与 decompress() **都会跨帧**读完整，
    # 这点与第三方 zstandard 的 stream_reader 正好相反（见 _tier2_zstandard）。
    try:
        with std.open(path, "rb") as f:
            return f.read(MAX_OUTPUT)
    except Exception:
        pass
    try:
        with open(path, "rb") as f:
            return std.decompress(f.read())
    except Exception:
        return None


def _tier2_zstandard(path):
    """第三方 zstandard：**逐帧循环**。

    踩过的坑（2026-09-24）：`ZstdDecompressor().stream_reader(f).read(n)` 默认
    `read_across_frames=False` —— **读完第一帧就停，且不报错**。实测一个 3.96 MB
    的多帧会话文件只解出 203 字节（恰好是会话头那一帧），表现为"解压成功但内容为空"。
    用 decompressobj() + unused_data 手动续帧，单帧/多帧都对。
    """
    try:
        import zstandard as zs
    except ImportError:
        return None
    try:
        with open(path, "rb") as f:
            buf = f.read()
        d = zs.ZstdDecompressor()
        out, total = [], 0
        while buf:
            obj = d.decompressobj()
            try:
                chunk = obj.decompress(buf)
            except Exception:
                break
            if chunk:
                out.append(chunk)
                total += len(chunk)
                if total > MAX_OUTPUT:
                    break
            nxt = obj.unused_data
            if not nxt or len(nxt) >= len(buf):
                break                      # 无剩余 / 未推进 → 结束，防死循环
            buf = nxt
        return b"".join(out)
    except Exception:
        return None


def _tier3_cli(path):
    cli = _zstd_cli()
    if not cli:
        return None
    try:
        r = subprocess.run([cli, "-d", "-c", "-q", path],
                           capture_output=True, timeout=60)
        return r.stdout[:MAX_OUTPUT] if r.returncode == 0 else None
    except Exception:
        return None


_TIERS = (
    ("stdlib:compression.zstd", _tier1_stdlib),
    ("zstandard", _tier2_zstandard),
    ("cli:zstd", _tier3_cli),
)


def tier_names():
    return [n for n, _ in _TIERS]


def read_zstd(path):
    """解压 zstd 文件并返回 bytes；无可用层或全部失败返回 None。

    逐层尝试，并对结果做**完整性校验**：某一层"成功返回但内容不闭合"（典型是被截断）
    时，继续试下一层。校验的意义在于——截断不报错，只看"拿到非空 bytes"会被骗，
    这正是本项目踩过的坑。
    """
    if not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size == 0:
        return b""

    best = None
    for name, fn in _TIERS:
        try:
            out = fn(path)
        except Exception:
            out = None
        if out is None:
            continue
        if len(out) == 0 or _looks_complete(out):
            return out
        # 可疑结果：保留下来当兜底，但继续试更可靠的层
        if best is None or len(out) > len(best):
            best = out
    if best is not None:
        print(f"[zstdio] 警告：{os.path.basename(path)} 解压结果未正常闭合"
              f"（{len(best)} bytes），可能被截断")
    return best


if __name__ == "__main__":                                  # 自检
    ok, name, hint = describe()
    print(f"zstd 解压: {'可用' if ok else '不可用'}  层={name or '-'}")
    print(f"可用层顺序: {tier_names()}")
    if not ok:
        print(hint)
    else:
        import glob
        fs = sorted(glob.glob(os.path.expanduser("~/.dsh/sessions/**/*.zstd"),
                              recursive=True))
        print(f"DSH 会话文件: {len(fs)}")
        if fs:
            d = read_zstd(fs[0])
            print(f"样本解压: {len(d) if d else 'None'} bytes")
    sys.exit(0 if ok else 1)
