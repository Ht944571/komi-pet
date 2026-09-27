# -*- coding: utf-8 -*-
"""
agents.ccusage — ccusage 覆盖面补充源（T2）
================================================================

定位（任务文档 §5.3，已实测）：**覆盖面补充，不是替代**。
  · 补**广度**：ccusage 支持 18+ agent 源、自带定价（costUSD，我们没有定价库）
  · 补**成本维度**：cost_usd 列（本次为 ODS 新增）
  · 保留自研解析器的**深度**（每次调用/每轮/每项目）与**零依赖**
  · 与自研解析器重叠的源（codex / zcode）**跳过**——避免同用量两个数
    （Codex 已逐 token 对账吻合，正因吻合才必须去重）

形态：**自管源**（覆盖 collect()）——数据来自外部 CLI 的 JSON 输出，
没有"文件 + 行号 + 游标"结构。

工程红线（本项目血泪教训）：探测/解压/网络一律放后台线程，绝不进采集循环。
`npx ccusage` 首跑 62s、热跑 ~20s，而采集循环 1s 一轮——所以：
  · collect() **只读缓存**（毫秒级），绝不同步等 npx
  · 刷新由**后台轮转线程**做：每轮挑一个最陈旧的源跑一次 npx（源间至少隔
    REFRESH_GAP_S），缓存按源落盘（原子写）
  · 用户没装 Node → available()=False，整源跳过，主链路零影响（T2 验收 ②）

字段映射（任务文档 §5.3，已验证 input+output+cacheRead+cacheCreation==totalTokens）：
  prompt_tokens              ← inputTokens + cacheReadTokens + cacheCreationTokens
  completion_tokens          ← outputTokens
  completion_thinking_tokens ← reasoningOutputTokens
  total_tokens               ← totalTokens
  cost_usd                   ← costUSD（源命令输出；全量输出叫 totalCost，兼容解析）

幂等：line_no 用 52 位内容派生哈希（同 deepseek/zcode 的规矩），重采不重。
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time

from .base import AgentSource, canonical_usage, usage_json

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 这是**可写**缓存（npx ccusage 拉回来的 json）→ 必须落在可写目录：
#    源码运行 = wb_usage/agents/data/ccusage（与改造前一致）；
#    打包后 = %LOCALAPPDATA%\KomiPet\agents\ccusage（包目录只读，写不进去）
try:
    import wb_runtime as RT
except ImportError:                                     # 源码直接跑时补 sys.path（app/）
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    import wb_runtime as RT
CACHE_DIR = (os.path.join(HERE, "data", "ccusage") if not RT.FROZEN
             else os.path.join(RT.data_dir(), "agents", "ccusage"))

REFRESH_MIN_S = 300          # 同一源两次刷新的最小间隔（任务文档：≥5 分钟）
REFRESH_GAP_S = 60           # 相邻两源刷新的间隔（轮转节流）
NPX_TIMEOUT_S = 180          # 单次 npx 超时（首跑拉包 62s + 余量）

# 与自研解析器重叠的源：跳过，避免重复计数（Codex 已对账吻合）
COVERED = ("codex", "zcode")

# ccusage 源 → 登记册 agent key（有登记的走登记 key，受面板开关管控）
MAPPED = {
    "claude": "claude-code",
    "kimi": "kimi",
    "qwen": "qwen-code",
    "gemini": "gemini-cli",
    "opencode": "opencode",
}
# 登记册暂无条目的源：以 ccusage-<src> 为 agent key 入库（可在登记册补条目后映射）
EXTRA = ("amp", "droid", "codebuff", "hermes", "pi", "goose",
         "kilo", "copilot", "antigravity", "openclaw", "grok")


def _node_available():
    """本机是否有 Node（npx 随 Node 发行）。没有 → 整源跳过，主链路零影响。"""
    try:
        import shutil
        return bool(shutil.which("node"))
    except Exception:
        return False


def _npx_cmd():
    import shutil
    for name in ("npx.cmd", "npx.cmd.exe", "npx", "npx.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _source_enabled(agent_key):
    """登记册里该 agent key 是否启用（无登记条目 → 视为启用）。"""
    try:
        import sys
        app = os.path.dirname(HERE)
        if app not in sys.path:
            sys.path.insert(0, app)
        import wb_agent_registry as REG
        spec = REG.load()["agents"].get(agent_key)
        if spec is None:
            return True
        return bool(spec.get("enabled", True))
    except Exception:
        return True


def _stable_line_no(key):
    """52 位内容派生哈希（自管源幂等规矩：重采不得错位产生重复行）。"""
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:13], 16) & ((1 << 52) - 1)


def _day_ts_ms(date_str):
    """'2026-09-25' → 当日本地 12:00 的毫秒（落在该日内，避免时区把日切走）。"""
    try:
        y, m, d = (int(x) for x in date_str.split("-")[:3])
        return int(time.mktime((y, m, d, 12, 0, 0, 0, 0, -1)) * 1000)
    except Exception:
        return 0


def _sources():
    """要采集的 (ccusage源名, agent key) 列表（登记册开关已过滤）。"""
    out = []
    for src, key in MAPPED.items():
        if src in COVERED:
            continue
        if _source_enabled(key):
            out.append((src, key))
    for src in EXTRA:
        if src not in COVERED:
            out.append((src, f"ccusage-{src}"))
    return out


def _cache_path(src):
    return os.path.join(CACHE_DIR, f"{src}.json")


def _read_cache(src):
    try:
        with open(_cache_path(src), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("daily") else None
    except Exception:
        return None


def _write_cache(src, daily):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _cache_path(src) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.time(), "daily": daily}, f,
                  ensure_ascii=False)
    os.replace(tmp, _cache_path(src))


def _row_of(src, agent_key, entry):
    """一条 ccusage 日聚合 → 规范 ODS 行（映射见模块头）。"""
    date = str(entry.get("date") or entry.get("period") or "")[:10]
    if not date:
        return None
    inp = int(entry.get("inputTokens") or 0)
    cread = int(entry.get("cacheReadTokens") or 0)
    cwrite = int(entry.get("cacheCreationTokens") or 0)
    out = int(entry.get("outputTokens") or 0)
    total = int(entry.get("totalTokens") or 0) or (inp + cread + cwrite + out)
    usage = canonical_usage(
        prompt_tokens=inp + cread + cwrite,
        completion_tokens=out,
        total_tokens=total,
        prompt_cache_hit_tokens=cread or None,
        prompt_cache_write_tokens=cwrite or None,
        completion_thinking_tokens=int(entry.get("reasoningOutputTokens") or 0) or None,
    )
    models = entry.get("models") or [m.get("modelName")
                                     for m in (entry.get("modelBreakdowns") or [])]
    models = [m for m in models if m]
    cost = entry.get("costUSD")
    if cost is None:
        cost = entry.get("totalCost")
    row = AgentSource.base_row(
        _stable_line_no(f"ccusage:{src}:{date}"),
        id=f"ccusage-{src}-{date}",
        event_type="daily", role="agent", session_id=date,
        ts_ms=_day_ts_ms(date), model=models[0] if len(models) == 1 else "",
        raw_usage_json=usage_json(usage))
    row["agent"] = agent_key     # 行级覆盖：一个源承载多个 agent key（登记 key 优先）
    row["cost_usd"] = float(cost) if cost is not None else None
    return row


def _fetch_one(src):
    """跑一次 npx ccusage <src> daily --json，成功则写缓存。返回是否成功。"""
    npx = _npx_cmd()
    if not npx:
        return False
    flags = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW
    try:
        r = subprocess.run(
            [npx, "-y", "ccusage@latest", src, "daily", "--json"],
            capture_output=True, timeout=NPX_TIMEOUT_S,
            creationflags=flags, shell=False)
    except Exception:
        return False
    if r.returncode != 0:
        return False
    try:
        d = json.loads(r.stdout.decode("utf-8", errors="replace"))
        daily = d.get("daily") if isinstance(d, dict) else None
        if not isinstance(daily, list):
            return False
        _write_cache(src, daily)
        return True
    except Exception:
        return False


class _Refresher:
    """后台轮转刷新线程：每轮挑最陈旧的源跑一次 npx，绝不阻塞采集循环。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None

    def ensure_started(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop,
                                            name="ccusage-refresh", daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            now = time.time()
            candidates = []
            for src, _key in _sources():
                c = _read_cache(src)
                at = (c or {}).get("fetched_at") or 0
                candidates.append((at, src))
            candidates.sort()
            oldest_at, oldest = candidates[0] if candidates else (time.time(), None)
            if oldest is None or now - oldest_at < REFRESH_MIN_S:
                time.sleep(60)
                continue
            _fetch_one(oldest)
            time.sleep(REFRESH_GAP_S)


_refresher = _Refresher()


class CcusageSource(AgentSource):
    """ccusage 覆盖面补充源（自管源）。key=ccusage；各源行以 agent 列区分。"""

    key = "ccusage"
    label = "ccusage（覆盖面补充）"
    has_credit = False
    order = 900

    def root(self):
        return CACHE_DIR

    def available(self):
        """有 Node 才可用（npx 随 Node）。没有 → 整源跳过，主链路零影响。"""
        return _node_available()

    def project_of(self, cwd):
        return ""                       # ccusage 日聚合无项目维度

    def collect(self, conn, db_path, insert_rows):
        """只读缓存落库（毫秒级）；刷新由后台轮转线程负责。"""
        if not _node_available():
            return 0
        _refresher.ensure_started()
        inserted = 0
        for src, agent_key in _sources():
            cache = _read_cache(src)
            if not cache:
                continue                # 尚未抓到过 → 本轮静默（后台线程会补）
            rows = []
            for entry in cache["daily"]:
                if not isinstance(entry, dict):
                    continue
                row = _row_of(src, agent_key, entry)
                if row:
                    rows.append(row)
            if rows:
                inserted += insert_rows(f"ccusage://{src}/daily", self, rows)
        return inserted
