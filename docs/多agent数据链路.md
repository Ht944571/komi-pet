# 多 Agent 数据链路 · 设计与扩展指南

> 面向要**新增一个用量来源**或**改动聚合口径**的维护者。
> 阅读顺序建议：先看 §1 的核心契约，再看 §8 的落地清单。

---

## 1. 核心契约：规范用量形状

整套看板的所有聚合，最终都建立在 `ods_jsonl_event.raw_usage_json` 这**一个 JSON 列**上。
数仓层（`wb_dw.py`）用固定的字段名从这个 JSON 里取值：

| 规范字段 | 含义 | 缺失时视图如何处理 |
|---|---|---|
| `credit` | 积分 | `COALESCE(..., 0)` |
| `prompt_tokens` | 输入 Token | `COALESCE(..., 0)`；**= 0 的行会被 `v_call` 过滤掉** |
| `completion_tokens` | 输出 Token | `COALESCE(..., 0)` |
| `total_tokens` | 总 Token | `COALESCE(..., 0)` |
| `prompt_cache_hit_tokens` | 命中缓存的输入 | 回退读 `prompt_tokens_details.cached_tokens` |
| `prompt_cache_write_tokens` | 写入缓存的输入 | `COALESCE(..., 0)` |
| `prompt_cache_miss_tokens` | 未命中缓存的输入 | **可缺**：按 `prompt − cached − write` 回退（≥0） |
| `completion_thinking_tokens` | 思维链 / reasoning | 回退读 `completion_tokens_details.reasoning_tokens` |

**这就是扩展多 agent 的全部关键**：每个 agent 的采集器只要把自家 token 计数
**归一**成上表形状，既有 VIEW、接口、前端图表即可原样复用，**不需要为每个 agent 写一套 SQL**。

WorkBuddy 是"基准源"——它的 `providerData.rawUsage` 本身就是这个形状，所以
`agents/workbuddy.py` 只是原样透传。其它源做映射即可。

字段常量定义在 `agents/base.py` 的 `CANONICAL_USAGE_FIELDS`；
`canonical_usage(**kw)` 会校验字段名拼写（拼错直接 `KeyError`，避免静默丢数据）。

---

## 2. 数据流与分层职责

```
① 文件发现        agents/*.root() + glob_pattern()
② 行解析          agents/*.parse_lines()  →  规范行 dict
③ 落库            wb_collect.py（断点续采 / 覆盖检测 / 幂等 / 原文归档）
④ 建模            wb_dw.py（只建 VIEW，实时聚合）
⑤ 接口            wb_api.py（agent 过滤 + 元信息）
⑥ 呈现            dashboard.html（口径自适应）/ wb_hover_core.py（桌宠取数）
```

各层职责边界：

- **agents/** 只做"行 → 规范行"的翻译，**不碰数据库**（不建表、不写 meta、不知游标存在）。
- **wb_collect.py** 只做文件级增量策略与落库，**不解析任何 agent 私有格式**。
- **wb_dw.py** 只认 `raw_usage_json` 的规范字段，**不认识 agent 是谁**（`agent` 只是一个分组列）。
- **wb_api.py** 只做过滤与聚合，**不知道各 agent 的数据从哪来**。

新增一个 agent 只需要动 `agents/` 一个目录。这是刻意的隔离。

---

## 3. `AgentSource` 接口契约

`agents/base.py`：

```python
class AgentSource:
    key = ""            # 稳定标识，写进 ods_jsonl_event.agent，勿随意改
    label = ""          # 看板按钮/图例上的显示名
    has_credit = False  # 是否有「积分」概念（前端据此决定是否切 Token 口径）
    order = 100         # 看板按钮排序
```

| 方法 | 必须实现 | 说明 |
|---|---|---|
| `root()` | ✅ | 数据根目录。可返回不存在的路径（`available()` 会判假） |
| `glob_pattern()` | — | 相对 `root` 的 glob，支持 `**`。默认 `*/*.jsonl` |
| `available()` | — | 默认判 `root()` 是否为目录 |
| `parse_lines(path, new_lines, ctx)` | ✅ | `new_lines` 是 `[(line_no, raw_bytes)]`；返回规范行 dict 列表 |
| `build_context(path)` | — | 文件级上下文预扫描；默认返回 `None`（不扫） |
| `project_of(cwd)` | — | cwd → 项目名；默认取末段目录名 |

`parse_lines` 返回的每个 dict（用 `self.base_row(line_no, **kw)` 构造）：

| key | 说明 |
|---|---|
| `line_no` | 文件物理行号（与 `file_path` 组成主键，**决定幂等与断点续采正确性**） |
| `id` | 事件 id（可自造，如 `f"codex:{sid}:{line_no}"`） |
| `event_type` / `role` | 事件类型与角色。**用户消息必须是 `event_type='message'` + `role='user'`**，否则 `v_msg` 取不到、轮次的 user_prompt 为空 |
| `session_id` | 会话标识（`v_session` / 会话列表依赖它） |
| `ts_ms` | 毫秒时间戳（**不是秒**，`base.parse_ts_ms()` 可容错解析 ISO8601 / 秒 / 毫秒） |
| `cwd` | 工作目录 |
| `model` | 模型名（`v_model` / 看板模型分布依赖它） |
| `request_id` | **轮次 id**（`v_turn` 按它聚合，一轮内多次调用会被求和） |
| `raw_usage_json` | 规范用量 JSON 文本；无用量的事件给 `None` |
| `user_prompt` / `ai_title` | 用户提问 / AI 标题 |
| `_raw` | 解析后的**完整原始对象**，采集器取走写进 `ods_jsonl_raw` 后剔除（不要省略，否则原文归档为空） |

---

## 4. 内置数据源实现要点

### 4.1 WorkBuddy（`workbuddy`）

- 根目录 `~/.workbuddy/projects/`，每会话一个 `*.jsonl`，**两层不递归**。
- 每行自带 `sessionId` / `cwd` / `timestamp`，用量在 `providerData.rawUsage`，
  轮次在 `providerData.conversationRequestId`，模型在 `providerData.model`。
- **不需要 `build_context()`**（每行自带上文）。
- 文本型事件全量入库（历史上就是这样，也是库体积的主要来源之一）。

### 4.2 Codex CLI / Codex Desktop（`codex`）

- 根目录 `~/.codex/sessions/`，路径是 `<年>/<月>/<日>/rollout-<ts>-<uuid>.jsonl`，**递归**。
- 每行形如 `{timestamp, type, payload}`；关键事件：

  | 事件 | 用途 |
  |---|---|
  | `session_meta` | `payload.session_id` / `cwd` / `originator` |
  | `turn_context` / `event_msg:task_started` | `turn_id`（→ `request_id`）、`model` |
  | `world_state` / `event_msg:thread_settings_applied` | `model` |
  | `event_msg:token_count` | **用量** |
  | `event_msg:user_message` | `payload.message` → user_prompt |

- **用量映射**（`payload.info.last_token_usage`，即本次调用的增量）：

  ```
  prompt_tokens              ← input_tokens
  completion_tokens          ← output_tokens
  total_tokens               ← total_tokens
  prompt_cache_hit_tokens    ← cached_input_tokens
  prompt_cache_write_tokens  ← cache_write_input_tokens
  completion_thinking_tokens ← reasoning_output_tokens
  （credit 不写 —— Codex 按 Token 计费，没有积分概念）
  ```

  **实测校验**：`sum(last_token_usage.total_tokens)` 与最后一次
  `total_token_usage.total_tokens` 完全相等（本机三例：4480420 / 102901 / 6759351 全对），
  所以按「一个 `token_count` 事件 = 一次调用」入库、由 VIEW 求和，口径正确。

- **必须实现 `build_context()`**：Codex 的 `token_count` 行**不自带** session_id / cwd /
  model / turn_id（这些出现在文件头的 `session_meta`、`world_state`、`turn_context` 里）。
  断点续采时新行的上文可能早已消费过，所以解析前先把整文件扫一遍建立上下文
  （含 `line_no → turn_id` 映射）。rollout 文件很小（本机合计 1.5 MB），这个代价可接受。
- **只入库两类行**（`token_count` / `user_message`），不搬全部原始事件——避免重演
  WorkBuddy 侧"ODS 只增不减、库涨到 GB 级"的问题。本机 3 个会话只产生 185 行。
- `project_of()`：Codex Desktop 的会话目录形如 `…\Codex\<日期>\<工作区名|sk-密钥>`，
  密钥目录名对用户无意义，回退到上一层（日期）。

---

## 4.3 DeepSeek Harness（`deepseek-harness`）

- 位置：`~/.dsh/sessions/<项目槽>/session-<uuid>/session.jsonl.zstd`
  （**zstd 压缩**，且是**追加写**的多帧流）
- 走"自管源"通道（覆盖 `collect()`），因为压缩流没法按行号 seek，增量游标无从谈起。

事件映射：

| 事件 | 用途 |
|---|---|
| `session` | 会话头：`id` / `cwd`（**必须是文件第一行**，否则后续行无法归属） |
| `assistant/message` | **用量**在 `data.usage`，`data.turn` + `data.step` 是轮次身份 |
| `user/message` | 用户提问（**必须按 `data.source.kind` 过滤**，插件/系统注入的也是 `role=user`） |
| `session/title` | `data.title` → ai-title |

### ⚠️ 三个必须知道的坑（都实测踩过）

**① 多帧 zstd 的静默截断。** 会话文件是追加写 → 多帧拼接。
`zstandard` 的 `stream_reader()` **默认 `read_across_frames=False`**，
也就是**读完第一帧就停且不报错**：实测 3.96 MB 的文件只解出 203 字节（恰好是会话头那一帧），
表现为"解压成功但内容为空"。
→ 解法：`decompressobj()` + `unused_data` 手动逐帧；
  并在 `zstdio.read_zstd()` 里对结果做**完整性校验**（JSONL 必须以 `}`/`]` 收尾），
  不闭合就继续试下一层——因为**截断不报错，只看"拿到非空 bytes"会被骗**。
> 对照：Python 3.14 的 stdlib `compression.zstd` 的 `open()` / `decompress()`
> **都会跨帧**读完整（同一文件实测 12,189,309 字节）。两者行为相反，别想当然。

**② 同一会话存在两种格式的文件。** 实测会话目录里同时有
`session.jsonl.zstd` 与 `session.v3.jsonl.zstd`，内容高度重叠，直接扫会**重复计数**。
→ 解法不是猜哪个权威，而是**让重复自然折叠**：`line_no` 用**内容派生的稳定身份**
（`hash("usage:<sid>:<turn>:<step>")`），两种格式里同一事件算出同一个键，
`INSERT OR IGNORE` 自动去重。实测：产出 2835 行 → 入库 2703 条，差额就是折叠掉的重复。

**③ 同一个 usage 对象里混着两种口径**（本数据源最大的坑）：

| 字段 | 口径 |
|---|---|
| `inputTokens` / `outputTokens` / `reasoningTokens` | **单步增量** |
| `cacheReadTokens` | **会话级累计**（跨轮持续递增，**从不重置**） |
| `totalTokens` | 累计，且与 in+out 对不上 → **不用** |

而且 `inputTokens` **只含新增（未命中缓存）部分** → 单次调用的真实输入 = `inputTokens + 缓存增量`。

两版错误都写过，别重犯：把 `cacheReadTokens` 当增量 → 算出"缓存 3.9 亿 > 输入 294 万"；
改成"轮内累计差分" → 仍错，**它在轮边界不重置**，每轮第一步都把整个会话的累计当成增量。

**自洽性必须验**（这两条不成立就说明口径错了）：
`cached ≤ prompt`、`total == prompt + completion`。当前实测：prompt 743.6 万 / cached 448.9 万
（命中率 60.4%）——合理。

### 另外两个设计点

- **未变化文件不重解**：用 `meta` 存 `size|mtime_ns` 快跳（解压是开销大头）。
  但**只在确实产出时才记状态**：否则一旦某轮解析异常，状态被写死，
  后续每轮都"跳过未变化"，变成**永久静默失败**（这个反例真发生过）。
- **标题身份带 seq**：会话标题会多次更新，全保留让 `v_session` 取最新那条；
  只按 session 折叠会留下**最早**的标题。

---

## 5. 数仓层

### 5.1 ODS 的 `agent` 列

- 新库由 `CREATE TABLE` 直接带上；**旧库由 `wb_collect._migrate()` 补列**
  （`ALTER TABLE ADD COLUMN`，元数据操作，毫秒级）。
- **不回填历史行**：已有的 26 万行如果 UPDATE 会重写整张表（2 GB+ 写放大）。
  历史行 `agent` 保持 `NULL`，视图统一用 `COALESCE(agent,'workbuddy')` 解释——
  语义等价（该列出现之前唯一的数据源就是 WorkBuddy）且零成本。
- `ALTER TABLE` 之后才建 `idx_ods_agent` 索引（顺序很重要：旧库上先建索引会
  `no such column`，这个坑踩过一次）。

### 5.2 视图清单

| 视图 | 分组维度 | 备注 |
|---|---|---|
| `v_call` | 单次调用明细 | 新增 `agent` 列 |
| `v_turn` | 轮次 | 新增 `agent`（进入 `GROUP BY turn_id, agent`） |
| `v_daily` / `v_model` / `v_project` | 天 / 模型 / 项目 | **保持"全部 agent 合计"语义不变**，避免破坏既有接口口径 |
| `v_turn_total` | 统一合并视图 | 末尾追加 `agent` 列（jsonl 侧来自各源；official 侧固定 `codebuddy`） |
| `v_agent` | **agent** | 各 agent 合计 + 会话数 + 首末日 + 缓存命中率 |
| `v_agent_daily` | agent × 天 | |
| `v_agent_model` | agent × 模型 | |
| `v_agent_project` | agent × 项目 | |

设计取舍：**既有 `v_daily`/`v_model`/`v_project` 不动**（它们的语义是"全部 agent 合计"），
按 agent 拆分的需求由新的 `v_agent_*` 承担。这样老接口的输出结构与数值都不会变。

---

## 6. 接口层

- 所有聚合接口都接受 `agent=<key>`，缺省 `all`：
  `/api/kpi`、`/api/daily`、`/api/models`、`/api/projects`、`/api/clients`、`/api/tops`
- 新增 `/api/agents`：返回各 agent 合计 + 元信息（`label` / `has_credit` / `order`）。
- `agent` 取值由 `agents` 注册表校验，**未知取值返回空集而不是 500**
  （`_agent_cond()` 返回恒假条件 `1 = 0`）——前端传脏参数时表现为"没有数据"。
- 多表 JOIN 场景需给列加别名消歧：`v_call c JOIN v_turn t` 里两边都有 `agent`/`model`，
  所以 `_agent_cond(agent, col="t.agent")`、`_day_cond(days, col="t.day")`。
- `_models` 的占比分母用 **`WITH agg AS (…)` 先物化一次**，两个占比（`credit_pct` /
  `token_pct`）各引用它。若把子查询内联写多遍，SQLite 会重复物化整个 `v_call`
  （26 万行 × `json_extract`）——实测内联三遍时 3.9s → 10.3s，CTE 版 1.1s。

---

## 7. 前端口径自适应

`dashboard.html` 里 `hasCredit()` 判断当前 agent 是否有积分口径（来自 `/api/agents`），
据此调整（避免出现误导性的 0）：

| 位置 | 有积分 | 无积分 |
|---|---|---|
| KPI 积分卡 | 正常数值 | 显示「—」+ 说明「XX 无积分口径（按 Token 计费）」 |
| 日趋势图 | 积分柱 + Token 线 + 两条日均虚线 | 只画 Token 线（不画恒为 0 的假积分线，左侧积分轴隐藏） |
| 模型分布饼图 | 按 `credit_pct` | 按 `token_pct`，标题改「Token 占比」 |
| 项目分布条形 | 按积分 | 按 Token |
| 多 Agent 对比 | ← 恒定口径：**一律用 Token + 对数 x 轴**（见下） |

「多 Agent 用量对比」卡片的两个刻意选择：

1. **统一用 Token 比较**——积分只有部分 agent 有，拿积分跨 agent 对比是拿苹果比橘子。
   积分/轮次/调用数放在 tooltip 里。
2. **x 轴取对数**——不同 agent 用量常差 2–3 个数量级（本机 WorkBuddy 213 亿 vs
   Codex 1134 万），线性轴下小的一方会被压成零宽、完全读不出。
   刻度用 `maxTicksLimit: 6` 限制密度，否则对数轴会挤出十几个重叠标签。

---

## 8. 新增一个 Agent：完整清单

- [ ] `agents/xxx.py` 写 `class XxxSource(AgentSource)`，填 `key` / `label` / `has_credit` / `order`
- [ ] 实现 `root()` / `glob_pattern()`（递归要 `**`）/ `parse_lines()`
- [ ] 上下文在文件头的（如日志式格式）实现 `build_context()`，并在 `parse_lines` 里用 `ctx`
- [ ] 用量归一：只填 `CANONICAL_USAGE_FIELDS` 里的字段名（拼错会 `KeyError`）
- [ ] 用户消息必须 `event_type='message'` + `role='user'`
- [ ] 每行带上 `_raw`（原文归档用）
- [ ] 在 `agents/__init__.py` 的 `SOURCES` 注册
- [ ] `python wb_collect.py --list-agents` 确认显示「可用」
- [ ] `python wb_collect.py --agent xxx` 确认能采到行
- [ ] 查库验证：`SELECT agent, event_type, COUNT(*) FROM ods_jsonl_event GROUP BY 1,2`
- [ ] 验证用量正确性：把入库的 `total_tokens` 合计与源文件里的累计值对一遍
      （Codex 就是这么核的——这一步不要省）
- [ ] `/api/agents` 出现新条目、看板顶栏多出按钮、切过去各卡片口径正确
- [ ] 若该 agent 无积分概念：`has_credit = False`，前端会自动切 Token 口径

---

## 9. 已知边界与坑

1. **`prompt_tokens = 0` 的行会被 `v_call` 丢弃**（视图里有 `> 0` 过滤）。
   如果某个 agent 会出现 0 输入的调用，这些行不会进统计——这是刻意保留的既有口径。
2. **`request_id` 决定轮次聚合粒度**。若某 agent 拿不到轮次概念，退化成用 `session_id`
   会把整个会话当一轮（Codex 在拿不到 `turn_id` 时就是这么退化的）。
3. **`ts_ms` 必须是毫秒**。写成秒会让日期归属整体错到 1970 年，且 `v_turn.day` 全为空。
4. **游标与 `line_no` 强耦合**：`parse_lines` 必须如实回传物理行号，不要过滤后重编号。
5. **`build_context()` 只在源覆盖了它时才会被调用**（采集器用 `type(src).build_context is not
   AgentSource.build_context` 判断），所以默认实现要保持"不扫文件"。
6. **同一文件不要被两个源同时认领**：meta 游标以绝对路径为键，重叠会导致互相干扰。
7. 改任何 `raw_usage_json` 的字段名 = 改口径，会同时影响看板 / 桌宠 / macOS 悬浮球三处。
