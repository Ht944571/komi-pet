# 古见同学桌宠 · 多 Agent 用量看板

> ### 🧭 **先看这篇**：[`docs/项目最终目标与工作交接.md`](docs/项目最终目标与工作交接.md)
> 回答「最终要做什么 / 现在到哪了 / 接手后怎么上手」，是文档的唯一入口。

> ### 📋 新接手？先读 [`docs/工作交接说明.md`](docs/工作交接说明.md)
> 它讲清了这是什么、五分钟怎么跑起来、目录各自负责什么、分层与关键契约、以及待办与红线。
>
> ### 🎯 要接着干？看 [`docs/目标任务-跨平台与多Agent接入.md`](docs/目标任务-跨平台与多Agent接入.md)
> 跨平台直装化 + 用户自选 agent 接入的**任务分解**（每个任务带交付物/验收标准/依赖/规模）、
> 依赖顺序与关键路径、风险与决策点、进度追踪表。
>
> ### 🧊 想做 3D 立体摆件？看 [`docs/桌宠3D立体摆件实现方案.md`](docs/桌宠3D立体摆件实现方案.md)
> 材质定性（NPR 平涂）→ 技术选型对比 → 难点排序 → 分阶段落地。
>
> ### 📜 想追「当初为什么这么设计」？看 [`docs/archive/`](docs/archive/README.md)
> 项目演进史料（鲸鱼娘 → 路线3 → 古见同学的血脉、上游调研、早期视觉提案）。**不再维护**。



> 一个独立的 Windows 桌面项目：**古见同学桌宠** + **多 AI Agent 用量看板**。
> 纯 Python 标准库（ctypes + GDI+），零第三方运行时依赖。
>
> 从 WorkBuddy 技能 `workbuddy-usage-dashboard` 中**独立出来**，可脱离 WorkBuddy 生态单独运行。

---

## 它是什么

两件东西，一套代码：

1. **桌宠**：桌面上一只古见同学 Q 版立绘，头顶想法气泡实时显示"正在对话 / 本轮积分·Token·用时"。
   有呼吸浮动、三段式拟真眨眼、鼠标跟随、空闲自主玩耍、OK 完成态、随 WorkBuddy 退出而关闭。
   立绘 24 张（Q 版 / 高冷版 × 6 表情 × 双朝向），右键热切换。
2. **多 Agent 用量看板**：把本机各个 AI Agent 的用量记录解析、汇总成一个本地网页看板
   （`http://127.0.0.1:8801`），支持**按 agent 切换与对比**。

**全部本地计算，不联网、不上传、不需要任何 API Key。**

---

## 配件层：按「哪些 agent 在用」佩戴

桌宠会检测本机有哪些 agent **正在运行**，并让古见同学相应地佩戴配件——
这就是"一只桌宠承载多个 agent"的落地形态。

| agent 在运行 | 古见同学的表现 |
|---|---|
| WorkBuddy | 身边出现一只**小黑猫**，会溜达、坐下、玩耍 |
| ZCode | 自己戴上**复古小纽扣贝雷帽**（制服蓝 + 领结红纽扣） |
| DeepSeek Harness | 抱着**鲸鱼玩偶** |
| **多个同时** | **上述配件同时佩戴**（猫 + 帽子 + 玩偶） |

### 三条设计原则

1. **声明式**：哪个 agent 配什么、怎么探测、有哪些能力、**用户开不开它**——
   全写在 `app/assets/_agents.json`（**agent 登记册，唯一来源**）；
   `wb_agent_registry.py` 是读取层，配件层与探测层都从它读（避免两处漂移）。
   旧的 `_acc_persona.json` 保留为**回退**（登记册缺失时才用）。
   **新增一个 agent 只加一行 JSON，不改代码**，彻底避免 `if agent == "zcode"` 这类硬编码。
2. **槽位化**：三件配件分属 `head` / `arms` / `companion` 三个槽位，互不冲突，
   于是"多 agent 同时运行 → 配件并集"是自然结果，没有任何特判。
3. **几何与绘图分离**：`wb_accessories.compute_placement()` 只算坐标、不依赖绘图后端，
   桌宠（GDI+）与预览工具（PIL）**共用同一份定位逻辑**，杜绝两处漂移。

### 判定口径：「在用」与「聚焦」是两件事

- **活跃（决定配件）** = 该 agent **进程在运行**。语义是"我的生活里有它"，必须**稳定**——
  开着一整天就一整天都在。配件体积大、动静大，忽隐忽现会非常廉价。
- **聚焦（决定徽章/领结）** = 前台窗口属于谁。可以频繁变化，因为那是轻量元素。

配套约束：探测跑在**后台 daemon 线程**（进程快照 + 端口探测都有系统开销，
放进 Win32 消息循环会拖慢拖动/点击——本项目吃过这个亏）；桌宠只读缓存，读取零开销。

### 关掉它

· **关掉配件**：桌宠右键菜单 →「配件（猫 / 贝雷帽 / 玩偶）」，设置持久化到 `.whale_settings.json`
· **关掉某个 agent**：看板底部「**Agent 接入**」面板的开关
  （写入 `app/assets/_agents.json`，桌宠下一轮即生效）。
  面板同时显示该 agent 的能力（可读日志 / 可探进程 / 有 hooks）与「有没有用量数据」——
  **「已启用」与「有用量数据」是两件事**：前者是「你允许它参与」，
  后者取决于你是否真的用过它。

### 素材与美术规范

配件素材由 `tools/make_accessories.py` + `tools/artkit.py` + `tools/acc_art.py` 生成。
**改美术前请先读 [`docs/配件美术规范.md`](docs/配件美术规范.md)** ——
它定义了描边/明暗/亮边/接地影四条渲染规则、色板、各配件的设计意图，
以及"改完要过三层校验（孤立图 / 语境图 / 真机图）"的工作流。

`artkit` 提供的是**与立绘同一套视觉语言**的渲染工具（描边压在填充下、cel 明暗、
左上亮边、接地影），不是随手画图元——第一版配件就是因为缺了这套语言才像剪贴画。
用户给的参考是「罗小黑」与「DeepSeek 图标」——这两个分别受**版权**与**商标**保护，
所以素材是**取风格气质做的原创绘制**（圆头大眼黑猫 / 圆润蓝色鲸鱼布偶），
**不是**对上述角色的复制。要换成手绘素材，直接替换 `app/assets/acc_*.png` 即可
（锚点表 `_acc_config.json` 由工具从立绘实测生成，与素材解耦）。

---

## 合并记录：本项目是**唯一**的桌宠（2026-09-24）

在此之前桌面上会有**两只古见同学**：本项目这一只，以及 ZCode 里的
`komi-pet` 插件（`D:\zcode\桌宠\plugins\komi-pet`，只读 ZCode 用量）。

现已合并为一只：**本项目同时承载 WorkBuddy / ZCode / DeepSeek Harness**。

为了让"退役不丢功能"，先做了两件事再退役：

1. **把 ZCode 接成数据源**（`app/wb_usage/agents/zcode.py`，只读同一个
   `~/.zcode/cli/db/db.sqlite`）→ ZCode 的用量、会话标题、模型分布仍在看板里，
   顶栏可切到 `ZCode`；
2. **把 ZCode 加进配件 persona**（`zcode → beret`）→ 用 ZCode 时古见会戴贝雷帽。

然后停用插件（**代码全部保留**，随时可回退）：

| 动作 | 位置 |
|---|---|
| 清空 SessionStart 钩子（原来在这自动拉起桌宠） | `plugins/komi-pet/hooks/hooks.json` |
| 在 ZCode 里停用插件 | `~/.zcode/cli/config.json` → `enabledPlugins["komi-pet@..."] = false` |
| 退役说明 | `plugins/komi-pet/README.md`（原文见 `README.md.retired`） |

一句话回退：把那两处改回去（`hooks` 恢复原始内容、`enabledPlugins` 置 `true`）。
**但别和本项目同时开**，否则又会出现两只。

---

## 目录结构

```
古见同学桌宠/
├── README.md                 # 本文档
├── install.py                # ★ 项目级安装器（写配置 + 注册登录自启，纯 winreg 不用 schtasks）
├── start-pet.cmd             # 双击启动桌宠（经守望进程）
├── start-dashboard.cmd       # 双击启动看板服务（带控制台，可看日志）
├── docs/                     # 文档 + 看板截图
│   ├── shot-agent-*.png      #   多 agent 视图截图
│   └── 多agent数据链路.md    #   多 agent 架构与"如何新增一个 agent"
└── app/                      # 全部运行时代码
    ├── hover.py              #   平台分发入口（macOS → b_hover.py；Windows → 桌宠）
    ├── wb_whale_win.py       #   ★ 桌宠主体（Win32 ctypes + GDI+，约 2950 行）
    ├── wb_motion.py          #   ★ 动效设计系统（所有时长/幅度/颜色数字的唯一出处）
    ├── wb_hover_core.py      #   数据公共层（路径解析 / SQLite 取数 / 格式化 / 位置记忆 / 日志）
    ├── wb_whale_watcher.py   #   守望进程（拉起桌宠 + 退出兜底关闭；--install/--status/--uninstall）
    ├── b_hover.py            #   macOS 圆形悬浮球（需 pyobjc，未做古见版）
    ├── b_hover_win.py        #   旧版 88px 圆球（hover.py --classic 回退用）
    ├── wb_paths.json         #   本机路径配置（install.py 生成）
    ├── .whale_settings.json  #   运行时：大小/风格/气泡/音效/画质
    ├── assets/               #   立绘 24 张 + ok_glyph.png + _eye_config.json（面部锚点）
    ├── tools/                #   素材生成 + 面部检测 + 测试
    └── wb_usage/             #   ★ 多 agent 看板（采集 / 建模 / 接口 / 前端）
        ├── agents/           #   ★ 多 agent 数据源适配器（base / workbuddy / codex）
        ├── wb_collect.py     #   采集：遍历各数据源，断点续采写 ODS
        ├── wb_dw.py          #   数仓：全部实时 VIEW，无物化表
        ├── wb_api.py         #   接口服务（127.0.0.1:8801）
        ├── wb_common.py      #   数据口径规则
        ├── dashboard.html    #   前端（纯静态，Chart.js 本地副本 + CDN 兜底）
        └── data/wb_usage_dw.db  # SQLite 数仓（约 2.3 GB）
```

---

## 前置条件

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10/11（桌宠为 Win32 实现） |
| Python | ≥ 3.8（本项目零第三方依赖） |
| 数据源 | 至少一个被支持的 agent 在本机有会话记录（见下） |
| 看板 | 全平台可用；macOS 悬浮球另需 `pyobjc-framework-Cocoa` |

---

## 快速开始

```bash
# 1) 环境自检（不改动任何东西）
python install.py --check

# 2) 安装：写路径配置 + 注册登录自启（用户级，无需管理员）
python install.py

# 3) 手动启动（可选，安装时已自动启动）
start-pet.cmd          # 桌宠
start-dashboard.cmd    # 看板服务（浏览器打开 http://127.0.0.1:8801）
```

常用命令：

```bash
python install.py --no-autostart   # 只写配置，不注册自启
python install.py --no-start       # 注册自启但不立即启动
python install.py --status         # 查看当前状态
python install.py --uninstall      # 移除本项目注册的自启项
python install.py --port 8802      # 换看板端口
```

桌宠自身的命令：

```bash
cd app
python hover.py --seconds 20   # 冒烟测试：桌宠出现 20 秒后自动退出
python hover.py --classic      # 回退到旧版 88px 圆球
python wb_whale_watcher.py --status   # 查 WorkBuddy / 桌宠 / 自启项状态
```

---

## 多 Agent 用量看板

### 支持的 Agent

| key | 显示名 | 数据来源 | 有「积分」口径 |
|---|---|---|---|
| `workbuddy` | WorkBuddy | `~/.workbuddy/projects/*/*.jsonl` | ✅（`providerData.rawUsage`） |
| `zcode` | ZCode | `~/.zcode/cli/db/db.sqlite`（`model_usage` 表，**只读**） | ❌（按 Token 计费） |
| `deepseek-harness` | DeepSeek Harness | `~/.dsh/sessions/**/session*.jsonl.zstd`（**zstd 压缩**） | ❌（按 Token 计费） |
| `codex` | Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl` | ❌（按 Token 计费） |
| `codebuddy` | CodeBuddy | `ods_official_request`（官方账单，可选） | ✅ |

数据源有两种形态，`AgentSource` 都支持：

- **文件源**（workbuddy / codex）：实现 `root()` + `glob_pattern()` + `parse_lines()`，
  由采集器负责文件枚举、断点续采、覆盖检测。
- **自管源**（zcode / deepseek-harness）：**覆盖 `collect()`** 即接管采集——自己读数据、
  构造规范行、调 `insert_rows()` 落库。它们的存储结构（SQLite 表 / 压缩流）根本没有
  "文件 + 行号 + 游标"这套结构，硬套按行契约只会把接口拧歪。
  自管源必须**幂等**：`line_no` 要用**内容派生的稳定整数**（本项目用 52 位哈希），
  用递增序号会在重采时错位、产生重复行。

### zstd 依赖（只影响 DeepSeek Harness）

`session.jsonl.zstd` 需要 zstd 解压，而 **Python 3.13 没有 stdlib zstd**（3.14 才有）。
`app/wb_usage/zstdio.py` 做了**四层降级**，任一层可用即可工作：

| 层 | 来源 | 备注 |
|---|---|---|
| 1 | `compression.zstd` | Python 3.14+ 标准库；实测 `open()`/`decompress()` 都跨帧 |
| 2 | `zstandard` | 第三方包（**当前生效层**） |
| 3 | `zstd` 命令行 | 系统装了即可 |
| 4 | — | 全不可用则**明确报告并跳过该源**，不影响其它 agent |

安装（当前已装）：

```bash
pip install -i https://mirrors.aliyun.com/pypi/simple/ zstandard
```

> 默认 pip 源在本机极慢，**务必带国内镜像**（实测 matplotlib 挂了 12 分钟，换镜像 10 秒）。

本机没装某个 agent → 该数据源自动跳过（`wb_collect.py --list-agents` 可查看可用性）。

### 看板怎么用

- 顶栏 **agent 筛选**（全部 / WorkBuddy / Codex CLI …）与**时间范围**（今天 / 近 7 天 / 近 30 天 / 全部）独立组合。
- **多 Agent 用量对比**卡片：跨数据源横向条形。**统一用 Token 比较、x 轴取对数**——
  不同 agent 用量常差 2–3 个数量级（本机 WorkBuddy 213 亿 vs Codex 1134 万），
  线性轴下小的一方会被压成零宽；积分只有部分 agent 有，拿来对比是拿苹果比橘子。
- 选中无积分口径的 agent 时，**积分相关卡片/图表自动切换为 Token 口径**
  （积分卡显示「—」并说明原因，不显示误导性的 0）。

### 核心设计：规范用量形状

所有聚合都建立在 `ods_jsonl_event.raw_usage_json` 上，而 VIEW 层按**固定字段名**提取：

```
credit / prompt_tokens / completion_tokens / total_tokens /
prompt_cache_hit_tokens / prompt_cache_write_tokens /
prompt_cache_miss_tokens / completion_thinking_tokens
```

**每个 agent 的采集器只需把自家用量归一到这个形状**，全部既有视图、接口、前端图表即可
原样复用——不需要为每个 agent 各写一套 SQL。这是本项目扩展多 agent 的关键契约。

### 新增一个 Agent（三步）

1. 在 `app/wb_usage/agents/` 下新建 `xxx.py`，继承 `AgentSource`，
   实现 `root()` / `glob_pattern()` / `parse_lines()`（上下文在文件头的格式再实现 `build_context()`）：

   ```python
   from .base import AgentSource, canonical_usage, usage_json, parse_ts_ms

   class XxxSource(AgentSource):
       key = "xxx"; label = "Xxx CLI"; has_credit = False; order = 30
       def root(self): return os.path.expanduser("~/.xxx/sessions")
       def glob_pattern(self): return os.path.join("**", "*.jsonl")
       def parse_lines(self, path, new_lines, ctx=None):
           rows = []
           for line_no, raw in new_lines:
               d = self._load(raw)
               if not d: continue
               usage = canonical_usage(prompt_tokens=..., completion_tokens=..., total_tokens=...)
               rows.append(self.base_row(line_no, event_type="usage", session_id=..., ts_ms=...,
                                         raw_usage_json=usage_json(usage), _raw=d))
           return rows
   ```

2. 在 `agents/__init__.py` 的 `SOURCES` 里注册。
3. `python wb_collect.py --agent xxx` 验证能采到行；看板顶栏会自动多出一个 agent 按钮。

> 详细契约（含 `build_context` 的用途、只入库哪些行、项目名映射规则）见
> [`docs/多agent数据链路.md`](docs/多agent数据链路.md)。

---

## 数据链路

```
~/.workbuddy/projects/*/*.jsonl          ~/.codex/sessions/**/*.jsonl
                 │                                    │
                 └──────────┬─────────────────────────┘
                            ▼
        app/wb_usage/agents/（每个源归一到规范用量形状）
                            │
                    wb_collect.py（断点续采 / 覆盖检测 / 幂等）
                            ▼
        ods_jsonl_event（+ agent 列）  ods_jsonl_raw（原文归档）
                            │
                    wb_dw.py（全部实时 VIEW，无物化表）
                            ▼
        v_call → v_turn → v_turn_total ──→ v_daily / v_model / v_project
                                          v_agent / v_agent_daily / v_agent_model / v_agent_project
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
        wb_api.py（JSON 接口）       wb_hover_core.py（桌宠取数）
              ▼                           ▼
        dashboard.html              古见同学桌宠气泡
```

**口径（勿改）**：轮次 = `conversationRequestId`（一轮内多次调用需求和）；缓存命中 =
`prompt_cache_hit_tokens`；用户提问 = `<user_query>` 标签内内容（其余是系统注入）；日期归属 =
轮次首次调用的本地时区日期。改动会同时影响看板、桌宠、macOS 悬浮球三处。

---

## 测试

```bash
cd app

# 桌宠侧（用桩对象，不需要真窗口）
python tools/test_bubble_ok.py       # 期望 PASS: 119
python tools/test_linked_close.py    # 期望 PASS: 23
python tools/test_blink_smooth.py    # 期望 PASS: 36
python tools/test_komi_morph.py      # 期望 PASS: 38
python tools/test_live_bubble.py     # 期望 PASS: 20
python tools/test_motion.py          # 期望 结论: PASS
python tools/test_scale_switch.py    # 期望 结论: PASS

# 看板前端（需要本机跑着看板服务；PORT 必填，避免撞上别的服务）
cd wb_usage && python wb_api.py --port 8899 &
cd ../tools && NODE_PATH=<node_modules> PORT=8899 node test_dashboard_render.js
```

`test_dashboard_render.js` 用 jsdom 跑完整前端流程（含多 agent 切换），依赖 `jsdom`。
**注意**：不要用 8801 跑这个测试——如果本机已有看板服务占用 8801，请求会打到旧服务上，
表现为莫名的 `unknown api` 错误。

---

## 已知问题与待办

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0** | 数仓 `wb_usage_dw.db` 膨胀到约 2.3 GB | `ods_jsonl_raw`（原文归档）单表约 **1.9 GB**，占全库 84%——WorkBuddy 每行都写入完整消息正文（含每轮注入的整个 system prompt），平均约 7.5 KB/行。ODS 只增不减、无保留策略、`auto_vacuum=0`。建议：给 `ods_jsonl_raw` 设 TTL 或归档裁剪 + 定期 `VACUUM`。排查提示：大库上 `COUNT(*)` 很慢，用 `MAX(rowid)` 或直接查 `health` 接口 |
| P1 | 首次打开看板较慢（约 5–10 秒） | 冷查询需对 26 万行做 `json_extract`；服务端有 30s TTL 缓存，之后都是毫秒级。属既有行为，未做优化 |
| P1 | macOS 无桌宠版本 | macOS 走 `b_hover.py`（旧版圆球），数据口径一致但视觉差距大 |
| P1 | OK 自动消失 / 联动关闭没有 UI 开关 | 只能改 `app/wb_motion.py` 的 `OK_AUTODISMISS_ON` / `LINKED_CLOSE_ON` 后重启 |
| P2 | 模块名仍带 `wb_` / `whale` 前缀 | `wb_whale_win.py`、`wb_usage/` 等是历史命名（早期为鲸鱼娘形象），改名会牵动大量引用，暂留 |
| P2 | 文件命名 | `hover.py` 现在默认起的是桌宠，名字沿革自"悬浮球" |

---

## 从 WorkBuddy 技能迁移过来的说明

本项目原先住在 WorkBuddy 技能目录 `~/.workbuddy/skills/workbuddy-usage-dashboard__skillhub/scripts/`，
**代码已完整搬到这里并双向解耦**（不再读取技能目录的任何路径）。

迁移做了什么：

1. 复制全部代码 + 立绘 + 工具 + 数仓（2.27 GB）；
2. 新增项目级 `install.py`：自启方式从 **schtasks 计划任务**改为 **HKCU Run**
   （用户级、无需管理员，且不再依赖被安全策略拦截的 `schtasks.exe`）；
3. 桌宠与看板不再依赖 skill 路径：`app/wb_paths.json` 全部指向本项目内部；
4. 自启项改名：`WorkBuddyWhaleWatcher` → `KomiPetWhaleWatcher`，
   并在注册时**自动清理**旧键（避免两个守望各拉起一只桌宠）；
5. 顺手清掉代码里遗留的"鲸鱼娘"旧品牌文案（21 处 → 统一为"桌宠"）。

**旧技能目录未做任何改动**，仍可独立运行；确认新项目稳定后可以自行删除。

---

## 隐私

- 所有数据停留在本机 SQLite（`app/wb_usage/data/wb_usage_dw.db`），**无任何外发**
- 看板展示层默认脱敏：提问仅显示前 40 字，邮箱/手机号/绝对路径/长串密钥自动打码
- 服务只监听 `127.0.0.1`，局域网内其他机器访问不到

## 素材与角色说明

- 立绘与配件为本项目用 AI 图像生成工具产出的**原创素材**，风格致敬
  《古见同学有交流障碍》（古見さんは、コミュニケーションが上手い）中的
  古见同学形象；本项目与原作版权方无关联。
- 仓库不含任何原始生成图与个人数据；`app/tools/make_sprites*.py` 等
  再生成工具的素材源路径通过环境变量指定（见各文件头部说明）。

## 许可证

[MIT](LICENSE) —— 任何人可自由使用、修改、分发；愿这只古见同学在你的桌面上
也能安静地陪着你们。
