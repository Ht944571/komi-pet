# DeepSeek-Balance-Whale-Widget 调研报告

> 调研时间：2026-09-06（GitHub 搜索 API + 仓库 README + 官方插件站 + 第三方情报页交叉核实）
> 数据点：星标数/更新时间以检索时刻 GitHub 搜索 API 返回为准，可能略有滞后。

---

## 一、仓库定位（正主）

| 项 | 内容 |
|---|---|
| 主仓库 | **MeteorNOX/DeepSeek-Balance-Whale-Widget** |
| 地址 | https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget |
| 简介（原 README） | DeepSeek Harness（DSH）Web 界面右下角的常驻余额挂件：**小鲸鱼气泡图 + DeepSeek API 余额 + 今日已用 + 每轮对话消耗统计**，随界面打开自动启用 |
| 热度 | 约 1795★（检索时点），JavaScript，MIT License |
| 形态 | **标准 DSH（Cordis）bundle 插件包**，不是独立桌面程序——依赖 DeepSeek Harness 宿主才能运行 |
| 与你参考图的关系 | 你发的两张图片（鲸鱼系女仆角色 + “已思考(用时13秒) / 穷光蛋”气泡）正是此挂件：鲸鱼本体为 cut-out PNG，**气泡/文字由代码（SVG）绘制**，点击气泡切换随机台词 |

---

## 二、主要功能（README 特性）

- 🐋 **常驻自启**：随 DSH Web 每次打开自动出现
- 💰 **余额**：60s 自动刷新 + 点击手动刷新；变化时**数字滚动动画**；瞬时抖动沿用最近值不报错
- 📊 **今日已用·两种模式**
  - **小鲸鱼记账（推荐/默认，免令牌）**：观测余额差值本地记账（`.dshw-usage.json`），跨天自动归零归档 30 天，币种切换防污染账本
  - **实时·令牌（可选）**：用平台会话令牌调平台用量接口，按**峰谷定价表**换算（工作日高峰 9-12/14-18，周末全天谷价）
- 💬 **每轮对话消耗**：监听本机会话事件，轮次结束弹本轮真实消耗（无需任何令牌）；可开关、可设自动关闭秒数
- 🖱️ **交互**：拖拽 + 四边四分之一吸附；左吸附**水平镜像翻转**；按压 **Q 弹**效果（底部锚定）
- 🎚️ **汉堡菜单**（悬停鲸鱼右上角）：大小 0.6–2.5×、音效切换/音量、用量模式、峰谷文案皮肤、气泡开关等
- 🔊 音效（可选 mp3，缺失静默降级）；💬 随机台词（加权随机 + gif），气泡 5s 自动收起
- 📐 随浏览器窗口缩放

## 三、目标用途 / 适用场景

让 DSH 重度用户在对话界面内**随时看到余额与消耗**，不必切平台网页查余额；偏向成本控制、对账、或单纯想看每轮花多少钱。对“频繁开发/测试/自动化”场景尤其有用。不适合：不使用 DSH、对桌面浮空宠物反感者。

## 四、目录结构（插件包即仓库根目录）

```
DeepSeek-Balance-Whale-Widget/
├── package.json          # DSH bundle 插件元数据（包名 dsh-whale-widget）
├── cordis.patch.yml      # 插件挂载声明
├── lib/index.js          # 宿主侧插件本体（含 PRICING 定价表）
├── assets/
│   ├── DSH2.png          # README 展示图
│   ├── DSniang1.png      # 小鲸鱼本体（cut-out PNG，右下角定位 59.45%）
│   ├── DSniang02.png     # 备用整图
│   ├── rua.gif           # 随机台词 gif（可选）
│   └── Ya1/Ya2.mp3, D1/D2.mp3  # 音效（可选）
└── whale-widget-prompt.md # 完整视觉/交互规格与维护提示词
```

## 五、安装与接入（前提：已安装 DeepSeek Harness，pnpm 可用）

方式 A：GitHub 直装（推荐，装后可页面内更新）
```powershell
dsh plugin --profile web add github:MeteorNOX/DeepSeek-Balance-Whale-Widget
```
方式 B：本地仓库链接
```powershell
cd <仓库根目录>          # 根目录=插件包，勿写 link:.\dsh-whale-widget
dsh plugin --profile web add link:.
```
方式 C：npm
```powershell
dsh plugin --profile web add dsh-whale-widget
```
通用收尾：**重启 `dsh web` → 浏览器 F5**。

验证：
```powershell
dsh --profile web --dump-config | Select-String whale
curl http://127.0.0.1:3080/dsh-whale/balance.json   # 期望含 {"ok":true,"totalBalance":...}
curl http://127.0.0.1:3080/dsh-whale/widget.js      # 200 JS
```
卸载：`dsh plugin --profile web remove dsh-whale-widget`

## 六、关键配置步骤

1. **必需凭据 `DEEPSEEK_API_KEY`**：在 DSH 凭据服务配置（`.dsh/.credentials.yaml` 或管理界面），拉取 `api.deepseek.com/user/balance` 用；未配置则余额区提示“未配置密钥”。
2. **可选 `DEEPSEEK_PLATFORM_TOKEN`**（实时·令牌模式才需要）：
   - 登录 platform.deepseek.com → F12 → Network → 点“用量”找 `usage/by_api_key/amount` 请求 → 复制其 `Authorization`（`Bearer eyJ…`）→ 配为 DEEPSEEK_PLATFORM_TOKEN → 重启后在 菜单→用量 选「实时·令牌」
   - 注意：是网页会话令牌（非 `sk-`），重新登录后可能需重取；不配则自动用默认记账模式
3. **每轮消耗**：无需任何令牌，勾选菜单开关即可；定价表在 `lib/index.js` 顶部 `PRICING`，官方调价可自行改
4. 代理环境：先 `$env:http_proxy/https_proxy/all_proxy` 再执行安装
5. pnpm 报 allowBuilds 拦截时：在 `%USERPROFILE%\.dsh\profiles\web\pnpm-workspace.yaml` 的 allowBuilds 加对应包 key 后重跑

## 七、生态/衍生（与本机桌面集成相关性高者）

| 仓库 | 说明 |
|---|---|
| werairPL/DeepSeek-Balance-Whale-Widget-Electron | **桌面版**（Electron，2★） |
| Xgoddess/DeepSeek-Balance-Whale-Widget-desktop | 基于原版重构的 **Tauri 桌面版** |
| a0979283788-ctrl/whale-widget-android | Android 原生 overlay 移植 |
| CroniGato/DeepSeek-Balance-Whale-Widget-for-VSCode | VS Code 版 |
| Witherwithwinter/…-Bowl / GululuCopa/deepseek-whale-balance-widget 等 | 换皮/协议适配（GLM/Grok OAuth） |

## 八、与“当前项目（WorkBuddy 用量看板）”的关系与引入判断（重点）

**前提落差**：该组件是 **DSH/Cordis 宿主插件**，脱离 DeepSeek Harness 无法运行；而“当前项目”是 **WorkBuddy 本地用量看板**（Python jsonl 采集 + wb_api HTTP 服务 + Windows ctypes 悬浮球），宿主不同，**不能把 npm 包直接 `dsh plugin add` 进 WorkBuddy**。

对应此前“鲸鱼桌宠改造”需求的三种可行路线：

1. **直接使用（若你同时用 DSH）**：按第五节命令装进 DSH 即可得原版小鲸鱼，零代码。但数据口径是 DeepSeek 余额/今日已用，非 WorkBuddy 积分。
2. **原样引入当“视觉/交互参考源”**：其 MIT 许可允许复制改造。可复用思路——鲸鱼 cut-out 图 + SVG 气泡（文字/台词由代码画）、60s 轮询+数字滚动、拖拽吸附/镜像/按压 Q 弹、菜单缩放。
3. **移植数据源为 WorkBuddy 口径（对应上次需求）**：把 UI 壳（气泡绘制/拖拽/按压/台词逻辑）搬到本 skill 的悬浮球项目，把数据源从“DSH 事件+DeepSeek 余额 API”替换为**本机 wb_api（/api/kpi、/api/active-sessions…）+ workbuddy.db working 会话**；气泡内容改为“活跃会话/积分/Token/用时”。鲸鱼立绘需自行准备（原版 DSniang1.png 为原作者美术资源，直接复用前请确认其许可范围——README 未声明素材单独授权，稳妥做法是自制/替换）。

> 注：本报告只做资料整理，未下载其代码/素材，未对仓库代码做本地分析。若需走路线 3，下一步建议 `git clone` 后由我读 `lib/index.js` + 前端注入部分，梳理可复用函数清单并设计移植方案。

## 九、链接参考

- 主仓库：https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget
- 官方插件市场页（含中文安装说明）：https://dsh.yeyupiaoling.cn/plugin/6
- 安装/配置图文教程（作者博客，含令牌获取）：https://blog.yeyupiaoling.cn/article/1787626775875
- 第三方情报页：https://heatdrop.ai/repo/MeteorNOX/DeepSeek-Balance-Whale-Widget
- 桌面衍生（Electron）：https://github.com/werairPL/DeepSeek-Balance-Whale-Widget-Electron
- 桌面衍生（Tauri）：https://github.com/Xgoddess/DeepSeek-Balance-Whale-Widget-desktop
