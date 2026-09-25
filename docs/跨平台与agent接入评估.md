# 跨平台化 与 多 Agent 接入 —— 可行性评估

> 评估日期：2026-09-25
> 评估对象：`D:\workbuddy\古见同学桌宠\`
> 结论先行：**代码已独立，产品定义没独立；跨平台不是打包问题而是重写问题；
> agent 接入不该自己做，应复用已有生态。**

---

## 0. 三句话结论

1. **WorkBuddy 耦合**：代码层面已基本解耦（无 import、无技能路径依赖），
   但有 **3 处"产品定义级"硬耦合** —— 不修，装到别人机器上**根本跑不起来**。
2. **跨平台**：桌宠表现层是 **Win32 ctypes + GDI+（约 2950 行）**，**不可能打包解决**，
   只能**重写表现层**。这是整个评估里最大的一块工程量。
3. **Agent 接入**：不要再自己写解析器了。`ccusage` 已支持 **18+ 个 agent**、
   `Clawd on Desk` 已支持 **20+ 个**（且都覆盖你的 WorkBuddy/ZCode/DSH）。
   正确路线是**复用它们做数据后端**，自己只保留"规范用量形状"这一层。

---

## 1. 现状确认：与 WorkBuddy 的耦合到底有多深

> 📌 **2026-09-25 更新**：本节列出的三处硬耦合**已修复**，详见文末「附：P0 解耦已完成」。 下面保留原始审计记录以便对照。

### 1.1 已经解耦的部分（代码层面）

| 检查项 | 结果 |
|---|---|
| import WorkBuddy 的任何模块 | ❌ 无 |
| 运行期读取技能目录 | ❌ 无（`wb_paths.json` 已全部指向项目内部） |
| 硬编码技能路径 | ⚠️ **仅 4 处，全在 dev 工具/测试里**（见 §1.3） |
| 依赖 WorkBuddy 进程才能启动 | ❌ 手动 `hover.py` 可独立运行 |

**结论**：**没有编译期/导入期耦合**。这一层是干净的。

### 1.2 三处"产品定义级"硬耦合（**P0 已修**，见文末附录）

#### ① `install.py` 预检是**硬门槛** —— 没装 WorkBuddy 直接**中止安装**

```python
# install.py: preflight()
projects_root = os.path.join(HOME, ".workbuddy", "projects")
if not os.path.isdir(projects_root):
    issues.append("未找到 WorkBuddy 数据源目录……请确认本机已安装并使用过 WorkBuddy")
...
if issues:
    print("存在阻塞项，已中止安装。")
    return 1        # ← 硬中止
```

一个"多 agent 桌宠"，却因为**没装 WorkBuddy**而装不上。这是最不合理的一处。

#### ② 守望进程**只在 WorkBuddy 运行时才拉起桌宠**

```python
# wb_whale_watcher.py: check_once()
if _process_pid(WORKBUDDY_EXE):
    ... spawn_whale()
else:
    print("WorkBuddy 未运行，不拉起桌宠")     # ← 永远不启动
```

在只有 ZCode 或只有 Codex 的机器上，桌宠**永远不会出现**，而且**静默无提示**。

#### ③ 联动关闭以 WorkBuddy 退出为信号

```python
# wb_whale_win.py: _check_linked_close()
self._linked_expected = os.environ.get("WB_PET_LINKED") == "1"   # 守望注入
# 见到 WorkBuddy 存活或由守望拉起 → 它消失即关闭桌宠
```

这是个**合理的设计**（WorkBuddy 专用挂件就该跟着它关闭），
但作为通用产品，它需要变成：**"跟着最后一个活跃 agent 关闭"** 或者**可选**。

### 1.3 残留的技能路径引用（4 处，都是 dev 侧）

| 文件 | 性质 | 影响 |
|---|---|---|
| `tools/make_sprites.py` | 素材生成脚本 | 换机器会写错目录 |
| `tools/make_sprites_komi.py` | 素材生成脚本 | 同上 |
| `tools/test_komi_morph.py` | **测试** | 换机器**测试直接失败** |

> 运行时代码（`wb_whale_win.py` / `wb_api.py` / `wb_collect.py`）**没有**这个问题。
> 但这 3 个文件该修 —— 一个测试引用已废弃的绝对路径，等于埋了一颗换机必炸的雷。

### 1.4 一个概念上的澄清（很重要）

**"读取 WorkBuddy 的数据"不是耦合，是设计。**
项目通过 `agents/workbuddy.py` 把 WorkBuddy 当作**四个数据源之一**——
这是"多 agent"的应有形态。真正的耦合是**把 WorkBuddy 当成产品存在的前提**。

一句话：**现在是"WorkBuddy 的专用挂件"，还不是"通用的多 agent 桌宠"。**
差别就在 §1.2 那三处。

---

## 2. 跨平台可行性

### 2.1 硬事实：表现层是 Windows 专有实现

| 模块 | 行数 | 平台依赖 |
|---|---|---|
| `wb_whale_win.py`（桌宠主体） | ~2950 | **Win32 ctypes + GDI+**（`user32`/`gdiplus`/`msimg32`） |
| `wb_whale_watcher.py` | ~380 | `winreg`、`CreateToolhelp32Snapshot`、无名互斥体 |
| `wb_agent_presence.py` | ~180 | `CreateToolhelp32Snapshot` + TCP 端口探测 |
| `wb_motion.py` | ~200 | **纯数据（无平台依赖）** ✅ |
| `wb_accessories.py` | ~330 | **纯几何 + 状态机（无平台依赖）** ✅ |
| `wb_usage/*`（数据层） | ~3000 | **纯 Python 标准库** ✅ |
| `b_hover.py` | ~500 | macOS `pyobjc` —— **但这是旧的"圆球"版本，不是古见同学** |

**关键结论**：数据层（~3000 行）与设计层（~530 行）**天然跨平台**；
真正的 Windows 专有代码集中在**桌宠表现层约 3500 行**。

### 2.2 macOS 现状是个陷阱

项目里有 `b_hover.py`（macOS），**容易让人误以为"已经有跨平台了"**。
实际它是**早期的 88px 圆形悬浮球**，与古见同学桌宠（立绘、表情、配件）
是**两个不同产品**。所以：

> **跨平台 ≠ 补一个 macOS 分支。跨平台 = 把古见同学桌宠的表现层重写一遍。**

### 2.3 三条技术路线

| 路线 | 做法 | 优点 | 缺点 | 评价 |
|---|---|---|---|---|
| **A. 双原生** | Windows 保持现有 ctypes；macOS 用 Swift/AppKit 重写；共享数据层与设计令牌 | 性能最好、`WM_NCHITTEST` 级别的点击穿透最精确 | **两份表现层要同步维护**，一个人做等于双倍工期 | ❌ 单人项目不划算 |
| **B. Tauri v2** | Rust 后端（文件解析/轮询/进程探测）+ 系统 WebView 渲染前端；Canvas/SVG 做动画 | 体积 **5–15 MB**、内存 **30–80 MB**；Rust 后端天然适合我现在的探测逻辑；内置自动更新与签名 | **没有原生 per-region 点击穿透**，需 Rust 以 ~60fps 轮询光标切换 `setIgnoreCursorEvents`；**macOS 透明有已知 issue**；`macOSPrivateApi: true` 会永久失去 Mac App Store 资格 | ✅ **推荐** |
| **C. Electron** | 同 B 但用 Node + 打包 Chromium | 透明窗口/点击穿透/OLE 拖拽**成熟得多**；生态完善 | 体积 **80–150 MB**、内存 **100–200 MB** | ⚠️ 备选 |

**我倾向 B（Tauri v2）**，理由是场景特性：
这是一个**常驻不关**的应用，内存 30–80 MB 与 100–200 MB 的差别对"桌宠"来说很关键；
而 Rust 后端正好承接我现有的三类系统操作（扫进程、探端口、读文件/解压）。

> 对标参考：**Clawd on Desk 选了 Electron**（已验证可行，Win11/macOS/Linux 三平台），
> **taoes/pets 选了 Tauri 2**（Rust + React，透明 + 忽略鼠标 + 置顶 + 托盘，macOS/Windows）。
> 两者都能做成 —— 差别在取舍。Tauri 的坑集中在 macOS 透明与点击穿透，**必须真机验证**。

### 2.4 什么能直接复用，什么必须重写

| 资产 | 能否复用 | 说明 |
|---|---|---|
| 立绘 24 张 + 配件 18 张 PNG | ✅ **直接复用** | Web 技术渲染 PNG 更省事 |
| `wb_motion.py`（动效令牌） | ✅ **直接复用** | 已量化到 15fps 整数倍，是纯数据 |
| `wb_accessories.py`（几何 + 状态机） | ✅ **可直接移植** | `compute_placement()` 是纯几何，已刻意与绘图后端解耦 |
| `wb_usage/*` 数据层 | ✅ **直接复用** | 纯标准库；Rust 侧只需一个 SQLite 读接口 |
| 采集/数仓/接口 | ✅ **直接复用或平移** | SQLite + VIEW 方案与语言无关 |
| `wb_whale_win.py` 表现层 | ❌ **必须重写** | Win32 + GDI+ 专有 |
| `wb_whale_watcher.py` | ⚠️ **需按平台重写** | 三平台的自启/进程探测机制都不同 |

> **最大的资产其实是"被刻意解耦的那两层"**——`wb_motion.py` 的令牌化与
> `compute_placement()` 的纯几何。当初做这两件事（为了杜绝两处漂移）没有白做，
> 它们正好是跨平台的迁移载体。

### 2.5 打包与环境依赖

| 平台 | 打包 | 自启 | 备注 |
|---|---|---|---|
| Windows | NSIS / MSI（Tauri 或 Electron 内建） | **HKCU Run**（已实现，免管理员） | ✅ 现成 |
| macOS | `.dmg`（需 Apple Developer ID 签名 + 公证，否则 Gatekeeper 拦截） | LaunchAgent plist | ⚠️ 签名需付费账号（$99/年） |
| Linux | `.AppImage` / `.deb` | XDG autostart `.desktop` | 需处理 `WebKitGTK` 依赖 |

**环境依赖的现状与目标**：

- 现在：需要用户自备 **Python ≥ 3.8**（零第三方包，只有 DSH 解压需要 `zstandard`）。
- 打包后：**运行时不依赖 Python**（Tauri/Electron 把后端编进去了），
  用户**双击安装包即可**——这才是"每台电脑上直接安装使用"。

**冷启动成本**：现有实现是"用户已装 Python"的假设；打包后要一次下载 5–15 MB（Tauri）
或 80–150 MB（Electron），换来零环境配置。

### 2.6 跨平台的其他现实成本（容易被低估）

1. **点击穿透**：三平台机制完全不同 ——
   Windows `WS_EX_LAYERED` + alpha 命中测试（**现有实现已天然解决**）/
   macOS `hitTest` / Linux 依赖合成器。
   换成 Web 技术后这个"免费"能力会**退化**成需要主动轮询光标。
2. **开机自启**：三套机制（HKCU Run / LaunchAgent / XDG autostart），都要写。
3. **多显示器与 DPI**：现有 GDI+ 实现已处理；Web 侧要重新处理。
4. **代码签名**：Windows 建议（否则 SmartScreen 警告）、macOS **必须**（否则打不开）。
5. **动效一致性**：现在是 GDI+ 逐帧绘制；改成 Web 后要重新验证 15fps 基准与
   眨眼曲线的观感 —— **动效的"手感"是重写里最容易丢的东西**。

---

## 3. Agent 接入：不该自己做的部分（GitHub 调研结论）

### 3.1 调研成果（按相关度排序）

| 项目 | 相关度 | 关键可借鉴点 |
|---|---|---|
| **Clawd on Desk**<br>`rullerzhou-afk/clawd-on-desk` | ★★★★★ | **同一个产品形态，20+ agent**，含 WorkBuddy / ZCode / **DeepSeek Harness**。三层接入（hooks / 插件 / 日志轮询）、12 状态动画、状态机（多会话 + 优先级 + 最小显示时长）、主题脚手架 + 校验器、自定义 HTTP agent 注册。<br>⚠️ **AGPL-3.0**（美术资源另有版权）→ **只能借鉴设计，不能抄代码** |
| **ccusage**<br>`ryoppippi/ccusage` → `ccusage/ccusage` | ★★★★★ | **MIT**。**18+ 源**的本地用量解析（含 ZCode）。命名空间 CLI（`ccusage <source> daily`）、`--json` 导出、`--offline` 定价缓存、内置 MCP server、`blocks --live`。<br>**最适合作为数据后端直接依赖** |
| **Token Tracker**<br>`tokentracker.cc`（⭐794, MIT） | ★★★★ | **把 9 种日志格式归一化为统一 schema**（我的"规范用量形状"是同一思路的 4 源版）。本地 HTTP API + macOS 菜单栏 + 桌面 widget。**只记 token 计数、不记 prompt 内容**（隐私设计，值得抄） |
| **AIUsage**<br>`juliantanx/aiusage`（⭐116, MIT） | ★★★★ | TS + `better-sqlite3`（**与我现有架构最像**）；20+ 工具；本地 web dashboard + CLI + **桌面托盘 widget**；可选云同步（GitHub/S3/R2/MinIO） |
| **Agent Usage Widget**<br>`juanlatorre/agent-usage-widget` | ★★★★ | 原生 macOS + 桌面 widget。**追踪的是"订阅配额"而非 token 计数**（更贴近用户真问题："我还剩多少"）；`AvailabilityEngine` 纯函数派生 UI 状态；**"诚实状态"设计**——数据过期就显示变暗+时效，**不假装新鲜** |
| **VPet**<br>`LorisYounger/VPet` | ★★★ | Windows WPF 桌宠，**384 个动画 + Steam Workshop 插件体系**；Core 库可嵌入。**插件/Mod 体系**与"宠物动画打包格式"值得借鉴<br>（Windows only，不适合作为跨平台基座） |
| **Star Office UI**<br>`ringhyacinth/Star-Office-UI`（⭐7,162） | ★★★ | Phaser + Flask。**"每个 agent 一个工位"**的空间化表达——比我现在的"配件"更有叙事感 |
| **Clawmetry**<br>`vivekchand/clawmetry`（⭐340） | ★★ | Python + **OpenTelemetry 标准**；可视化推理图、执行流、token 消耗、告警 |
| **taoes/pets** | ★★ | **Tauri 2 + Rust + React** 的极简跨平台桌宠（透明 + 忽略鼠标 + 置顶 + 托盘）→ **跨平台技术选型的直接参考** |
| **Codex on Desk**<br>`G-ShiSi/codex-on-desk` | ★ | 单 agent 轻量版，证明"只做一个 agent 也有人用" |

### 3.2 最重要的三个结论

#### 结论 1：你的三个 agent，Clawd on Desk 全都支持

它的接入清单里明确有 **WorkBuddy**（`~/.workbuddy/settings.json`）、
**ZCode**（`~/.zcode/cli/config.json` → `hooks.events.*`）、
**DeepSeek Harness**（进程内 DSH 插件）。

> 也就是说：**"多 agent 桌宠"这个赛道已经有成熟玩家，且覆盖面远超我们。**
> 继续自己做 20 个解析器是**重复劳动**。

#### 结论 2：状态获取有三种层次，我们现在只做了最低那一层

| 层次 | 机制 | 实时性 | 需要 agent 配合 | 我们 | Clawd |
|---|---|---|---|---|---|
| **L1 hooks/插件** | agent 主动回调（事件流） | 实时，能拿到"思考中/等审批" | ✅ 要装 hook | ❌ | ✅ 主力 |
| **L2 日志轮询** | 读会话日志推断状态 | 秒级 | ❌ | ⚠️ 只用于**用量**，不用于状态 | ✅ 回退 |
| **L3 进程/端口探测** | 判断"活着" | 粗 | ❌ | ✅ **只有这层**（做配件） | ✅ 兜底 |

这解释了为什么我们的桌宠只能"戴配件"，**说不出"正在思考"**——
因为 L1 完全没做，L2 只用于数据。

**建议**：把"状态来源"做成**三级降级**，与现有的数据源抽象并列。
L1 优先（实时、状态丰富），L2 兜底（不依赖配合），L3 最低（保底"它在跑"）。

#### 结论 3：数据后端应该复用，不该重写

`ccusage` 是 MIT、支持 18+ 源、有 `--json` 输出。
**把 `ccusage <source> daily --json` 作为默认数据后端**，
自己的 `agents/` 只保留：
- 规范用量形状（`CANONICAL_USAGE_FIELDS`）—— 作为"归一契约"
- 直接解析路径（用于 ccusage 未覆盖的源、或用户不想装 Node 时）

这样 agent 覆盖面从 4 → 18+，成本是"写一个 JSON 适配器"而不是 14 个解析器。

### 3.3 顺带发现：值得抄的两个设计

1. **Token Tracker 的隐私取舍**：**只记 token 计数，不记 prompt 内容**。
   我们现在的 ODS 存了**完整原文**（`ods_jsonl_raw` 1.9 GB，占全库 84%），
   这既是膨胀的根因，也是"我把你的对话都抄了一份"的隐私面。
   → **建议改成默认不存原文**，或加"只记计数"模式。
2. **Agent Usage Widget 的"诚实状态"**：数据过期就**显示变暗 + 标注时效**，
   不假装新鲜。我们当前是"读不到就保留上次值"（对，但用户不知道数据是旧的）。
   → 建议加上"数据时效"的显式表达。

---

## 4. 模块结构与需要解耦/重构的部分

### 4.1 现状结构（已经分得不错）

```
┌─ 数据层（跨平台 · 纯标准库）──────────────────┐
│  wb_usage/agents/     数据源适配器（4 个）      │
│  wb_usage/zstdio.py   zstd 四层降级            │
│  wb_usage/wb_collect.py  采集                  │
│  wb_usage/wb_dw.py       数仓（全 VIEW）        │
│  wb_usage/wb_api.py      接口                  │
│  wb_usage/dashboard.html 前端                  │
└───────────────────────────────────────────────┘
┌─ 设计层（跨平台 · 纯数据）────────────────────┐
│  wb_motion.py         动效令牌（单一事实来源）   │
│  wb_accessories.py    配件几何 + 状态机         │
└───────────────────────────────────────────────┘
┌─ 探测层（平台相关）───────────────────────────┐
│  wb_agent_presence.py 进程 + 端口探测          │
└───────────────────────────────────────────────┘
┌─ 表现层（Windows 专有 · 需重写）──────────────┐
│  wb_whale_win.py      Win32 + GDI+ 桌宠        │
│  wb_whale_watcher.py  守望 + HKCU Run          │
└───────────────────────────────────────────────┘
```

**分层是清晰的**，问题只在**表现层的平台绑定**与 §1.2 的三处产品耦合。

### 4.2 需要改的地方（按优先级）

| # | 改动 | 类型 | 工作量 |
|---|---|---|---|
| 1 | `install.py` 预检：WorkBuddy 从**必需**改为**可选**（没有就不报错，只提示"该源不可用"） | 解耦 | S |
| 2 | 守望进程：拉起条件从"WorkBuddy 在跑"改为"**任一 agent 在跑 或 用户手动启动**" | 解耦 | S |
| 3 | 联动关闭：改为"**最后一个活跃 agent 退出后关闭**"或做成开关 | 解耦 | S |
| 4 | 修 3 个 dev 工具/测试里的技能绝对路径 | 解耦 | S |
| 5 | 引入 **Agent 能力声明**（支持 hooks？有日志？只有进程？）+ 三级状态来源降级 | 重构 | M |
| 6 | 引入 **Agent 安装/卸载/健康检查**（对齐 Clawd 的 Settings → Agents 模式） | 新增 | M |
| 7 | 数据后端接入 `ccusage --json`（保留自研解析器作为回退） | 新增 | M |
| 8 | `ods_jsonl_raw` 改为**默认不存原文**（或加"只记计数"模式） | 重构 | M |
| 9 | **表现层重写**（Tauri v2）+ 三平台自启 + 打包签名 | 重写 | **L** |
| 10 | 动效观感回归验证（15fps 基准、眨眼曲线、配件入退场） | 验证 | M |

> 1–4 是**必须先做**的（不做的话"给别人用"这件事根本不成立），但加起来是**小工作量**。
> 9–10 是**长尾大头**。

### 4.3 Agent 接入的可扩展设计（建议形态）

在现有 `AgentSource` 上补一组**能力声明**，对齐"三级状态来源"：

```python
class AgentSource:
    key / label / has_credit          # 已有
    # 新增：能力声明（决定用哪级状态来源，以及 UI 上怎么提示用户）
    caps = {
        "hooks":     False,   # 支持事件回调 → 能拿到"思考中/等审批"
        "log_poll":  True,    # 有会话日志 → 秒级状态 + 用量
        "presence":  {"procs": [...], "ports": [...]},   # 最低保底
        "install_hook": callable | None,   # 一键安装 hook（对齐 Clawd）
    }
```

UI 形态（对齐 Clawd 的 Settings → Agents）：
每个 agent 一行 → 「已接入 / 未接入 / 不可用」+「一键接入」按钮 +
说明"接入方式 = hooks / 日志 / 仅进程侦测"（**诚实告知能力边界**）。

---

## 5. 用量的获取与展示

### 5.1 获取（三层，按可靠性降级）

| 层 | 来源 | 内容 | 现状 |
|---|---|---|---|
| **订阅配额** | agent 官方 status-line / endpoint | **"还剩多少"**（最贴近用户真问题） | ❌ 未做 |
| **本地用量** | 会话日志（ccusage / 自研） | token / 成本 / 模型 / 项目 | ✅ 已做（4 源） |
| **活跃状态** | hooks / 日志轮询 / 进程端口 | 在跑 / 忙 / 需审批 | ⚠️ 只有进程端口 |

**建议补的第 1 层价值最大**：用户真正焦虑的是"配额还剩多少"，
而不是"我今天烧了多少 token"。参考 Agent Usage Widget 的做法——
从官方 status-line 的 `rate_limits` payload 本地采集（Clawd 也这么做）。

### 5.2 展示（两种载体，职责要分清）

| 载体 | 职责 | 原则 |
|---|---|---|
| **桌宠** | **指示灯 + 开关** | 不给细节；只表达"谁在跑 / 谁在忙 / 谁需要我"；配件系统是天然载体 |
| **看板** | **仪表盘** | 明细、趋势、对比、导出 |

**要补的两件事**：
1. **数据时效的显式表达**（"诚实状态"）——现在读不到就静默保留旧值，
   用户不知道数据是旧的。
2. **跨 agent 的统一时间线**（设计文档 §4 里已规划）——用户切 agent 时真正丢的是"整体感"。

---

## 6. 分阶段路线与可行性结论

### 阶段划分

| 阶段 | 目标 | 关键产出 | 规模 |
|---|---|---|---|
| **P0 解耦** | 摘掉"WorkBuddy 专用挂件"的帽子 | 4 处改动（§4.2 的 1–4）+ 换机可跑 | S |
| **P1 Agent 框架化** | 能"自主选择接入哪些 agent" | 能力声明 + 三级状态来源 + 接入/卸载 UI + 健康检查 | M |
| **P2 数据后端复用** | 覆盖面 4 → 18+ | `ccusage --json` 适配器 + 自研解析器降级 | M |
| **P3 表现层重写** | 跨平台 | Tauri v2 桌宠（消费 `wb_motion` 令牌 + `compute_placement` 几何） | **L** |
| **P4 分发** | 每台电脑双击可用 | NSIS/MSI + DMG + AppImage/deb + 自动更新 + 代码签名 | M |

### 可行性结论

| 维度 | 结论 |
|---|---|
| **技术上可行？** | ✅ 可行，且**已经有多个项目证明**（Clawd on Desk 三平台、taoes/pets Tauri 跨平台、Agent Usage Widget 原生 macOS） |
| **最大风险** | **表现层重写**（2950 行动效代码），以及重写后**动效手感可能退化** |
| **最大认知风险** | 误以为"跨平台 = 补个 macOS 分支"（实为重写）；误以为"agent 接入要自己做"（实为复用） |
| **最该先做的** | P0 解耦（小工作量、决定"能不能给别人用"） |
| **最该避免的** | 自己维护 20 个 agent 解析器；以及**在没做 P0 前就投入 P3** |

### 我的建议（如果只做一件事）

**做 P0 + P1。**
理由：P0 让产品成立（别人装得上、跑得起来），P1 让"自主选择接入 agent"这个
**你明确提出的需求**落地。这两步都不需要重写表现层，
而且 **P1 做完之后，即使仍是 Windows 版，它也已经是一个"能用的多 agent 桌宠"** ——
跨平台（P3）可以之后独立推进。

反过来，**先做 P3（跨平台）而不做 P0，收益是零**：一个跨平台但只能在有 WorkBuddy
的机器上安装的软件，没有意义。

---

## 7. 调研到的项目清单（便于后续深入）

| 项目 | 地址 | 许可 | 参考价值 |
|---|---|---|---|
| Clawd on Desk | `github.com/rullerzhou-afk/clawd-on-desk` | **AGPL-3.0**（美术另计） | 产品形态 / 20+ agent 接入 / 状态机 / 主题体系 |
| ccusage | `github.com/ccusage/ccusage` | MIT | **数据后端候选**（18+ 源、`--json`、MCP） |
| Token Tracker | `tokentracker.cc` | MIT | 9 格式归一 schema / 隐私取舍（只记计数） |
| AIUsage | `github.com/juliantanx/aiusage` | MIT | TS + SQLite 架构（与我现状最像）/ 托盘 widget |
| Agent Usage Widget | `github.com/juanlatorre/agent-usage-widget` | MIT | **订阅配额**追踪 / 诚实状态设计 / Swift 原生 |
| VPet | `github.com/LorisYounger/VPet` | 见仓库 | 动画体系 + Mod/插件 + Core 可嵌入 |
| Star Office UI | `github.com/ringhyacinth/Star-Office-UI` | MIT | 空间化多 agent 表达（工位隐喻） |
| Clawmetry | `github.com/vivekchand/clawmetry` | MIT | OpenTelemetry 标准 / 遥测可视化 |
| taoes/pets | `github.com/taoes/pets` | 见仓库 | **Tauri 2 跨平台桌宠最小实现** |

> ⚠️ **许可提醒**：Clawd on Desk 是 **AGPL-3.0**——**设计可借鉴，代码不可抄**
> （AGPL 有传染性，会让本项目也必须开源）。ccusage 是 MIT，可作为依赖或借鉴。
> 已有的 3 个"参考样式"素材（罗小黑 / DeepSeek 图标）我们当时处理成了**原创绘制**，
> 这个原则要继续保持。

---

**评估人**：小新
**日期**：2026-09-25
**一句话**：先摘掉 WorkBuddy 的帽子（P0），再把 agent 接入框架化并复用 ccusage（P1+P2），
最后才谈跨平台重写（P3）。顺序反了会白干。

---

## 附二：P1 进展（2026-09-25）

**已完成**：
1. **Agent 登记册** `app/assets/_agents.json`（唯一来源）+ 读取层 `wb_agent_registry.py`
   —— 登记 11 个 agent（4 个已支持 + 7 个「已知但未验证」默认关闭），带 `enabled` 开关、
   `caps` 能力声明、`presence` 探针、`config_paths` / `log_paths` 路径情报。配件层与探测层都从它读。
2. **看板「Agent 接入」面板**：`GET/POST /api/registry` + 开关 UI
   （白名单写入 + 原子替换 + 备份；只写自己的登记册，不碰 agent 配置）。

**未完成**：三级状态来源（hooks / 日志 / 进程）的**运行时降级**、接入健康检查。

> ⚠️ 一条必须记住的限制（评估时挖出来的）：**L3 进程探测对 CLI 类 agent 基本无效**
> （Claude Code / Codex / Kimi 都寄生在 node.exe / python.exe 上）。
> 能靠 L3 稳定识别的是**有独立 GUI 或独立服务**的（WorkBuddy / ZCode / DSH-web）。
> 要给 CLI 类做「活跃状态」，最终仍得上 hooks。

---

## 附：P0 解耦已完成（2026-09-25）

本文档 §1.2 列的三处「产品定义级」硬耦合**已全部修复**，项目现在是
「**通用多 agent 桌宠**」，不再是「WorkBuddy 专用挂件」。

| # | 原问题 | 修法 |
|---|---|---|
| ① | `install.py` 预检没有 `~/.workbuddy/projects` 就 **`return 1` 硬中止安装** | 改为查**任一已注册数据源**是否可用；一个都没有也只**警告**不阻塞（装好 agent 后自动采集，无需重装） |
| ② | 守望进程**只在 WorkBuddy 运行时才拉起桌宠** | 抽出 `active_agents()`：复用 `wb_agent_presence` 的**声明式探针**（登记册 `_agents.json` 的 `presence` 字段）。任一 agent 活跃即拉起；新增 agent 只改 JSON |
| ③ | 联动关闭以 **WorkBuddy 退出**为信号 | 新增 `_host_alive()`：宿主 = **活跃 agent 集合**。**WorkBuddy 退出但别的 agent 还在 → 桌宠不关**；最后一个也退出才关 |

同时把 `_wb_alive()` 改成「权威源（活跃探测）+ 兜底（原 WorkBuddy 三路探测）」的两级结构
——探测器不可用时自动回退，老环境行为不变。

**两个设计要点**：
1. **活跃探测器从配件开关下解耦**。原先它只在"配件开启"时创建；而它同时是联动关闭的判据，
   所以关掉配件会让联动关闭悄悄退回只认 WorkBuddy。现在探测器在 `__init__` 无条件启动，
   `_release_accessories()` 也不再销毁它。
2. **三值语义必须保住**。`_host_alive()` 返回 `True/False/**None**`，
   `None` = 探测未知（如尚未探满一轮）→ 联动关闭**什么都不做**。
   否则启动瞬间的空集合会被误判成"全退出了"而误关。

**验证**：
- 全量回归 **281 PASS / 0 FAIL**（`test_linked_close` 从 23 → 26 项，
  新增 5 条"探测未知不误杀"用例）
- 模拟"机器上没有 WorkBuddy"：`install.py --check` → **警告 + 退出码 0**（原先中止）
- 模拟"只有非 WorkBuddy 的 agent"：注入式探针正确识别，证明**新增 agent 只需改 JSON**
- 模拟联动关闭：只有 ZCode 活跃 → 不关；WorkBuddy 退了但 ZCode 还在 → **不关**；
  最后一个也退 → 宽限后关 ✅
- 桌宠真机冒烟：退出码 0，`db_ok/api_ok` 正常
- `grep` 全项目：**已无任何** `workbuddy-usage-dashboard` / 技能路径引用
