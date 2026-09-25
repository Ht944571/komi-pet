# T4.0 选型验证套件（任务文档 §6 T4.0 的执行载具）

> 目的：在 **macOS 真机**（或 GitHub Actions macOS runner）上验证
> **透明窗口 / 点击穿透 / DPI** 三项——Tauri v2 已知风险点（任务文档 §5.4/§8 R1）。
> **三项全达标 → Tauri v2 定案；任一不达标 → 退回 Electron**（不要硬扛）。

## 在 macOS 真机上跑（约 10 分钟）

前置：Rust（`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`）+ Node ≥ 20。

```bash
cd t40-kit
chmod +x run_t40_checks.sh
./run_t40_checks.sh
```

脚本会：编译并启动探针窗口 → 截三张图（透明/穿透/DPI）→ 在 `report/` 生成
报告骨架。人工核对三条：

1. **透明**：窗口的棋盘格区域应直接透出桌面背景（若是白/黑底 → 不达标）；
2. **穿透**：探针初始为点击穿透——鼠标点窗口，落点应在下层应用；按 `空格`
   切换后，点击窗口内的元素应弹出 alert（证明输入可被接收）；
3. **DPI**：界面第三行 `devicePixelRatio` 在 Retina 屏应为 **2.0**，把窗口拖到
   外接屏后数值应随屏变化（错值/不变 → 不达标）。

把三条结论填进 `report/REPORT-*.md`，连同三张截图归档到本目录。

**结果回传（给下次会话收口 T4.0 用）**：跑完后把整个 `report/` 目录拷回
Windows 机器的 `D:\workbuddy\古见同学桌宠\t40-kit\report\`（或直接把三条结论
粘贴给会话），下一次会话即可按结论收口 T4.0 → 进入 T4.1。
本机仓库未入 Git，**没有自动回传**——拷贝/粘贴这一步需要手动做。

## 用 GitHub Actions macOS runner 跑（可选）

把 `github-actions-t40.yml` 放进仓库 `.github/workflows/`，推送后自动在
`macos-14` 上编译探针并截屏，产物在 Actions artifacts 里下载核对。
注意：runner 无真机 Retina 屏，**DPI 项只能验证逻辑读数非零/随窗口变化**，
「Retina=2.0」仍需真机确认。

## 文件

| 文件 | 说明 |
|---|---|
| `tauri-app/` | Tauri v2 最小探针：透明 + 置顶 + 无边框 + `set_ignore_cursor_events` 穿透切换（空格）+ DPI 读数 |
| `run_t40_checks.sh` | macOS 执行脚本：编译/启动/截屏/报告骨架 |
| `github-actions-t40.yml` | 可选：CI 自动编译 + 截屏 |
| `REPORT_TEMPLATE.md` | 结论表模板 |

## 结论怎么落

- 三项达标 → 任务文档 §9 T4.0 标 ✅，选型定 **Tauri v2**，继续 T4.1 抽象表现层接口；
- 任一不达标 → 在报告写明不达标项，任务文档 §9 T4.0 标「退回 Electron」，
  T4 选型改 Electron（体积代价 150MB 内，任务文档 §5.4 已列成熟度优势），再进 T4.1。
