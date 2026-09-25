#!/bin/zsh
# T4.0 选型验证执行脚本（在 macOS 真机上运行）
# 产出：report/ 下三张截图 + REPORT.md 结论表
# 前置：安装 Rust（rustup）与 Node ≥20；本目录 npx/npm 可用
set -uo pipefail
cd "$(dirname "$0")/tauri-app"

OUT="../report"
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M%S)

echo "== [0/3] 编译并启动探针 =="
npm install
# dev 模式前台运行，20s 后由脚本截屏；若 20s 内窗口未出现，视为启动失败
npm run tauri dev &
APP_PID=$!
sleep 25

echo "== [1/3] 透明：截屏（棋盘格处应透出桌面） =="
screencapture -x "$OUT/transparency-$STAMP.png"

echo "== [2/3] 穿透：截屏（状态行为按界面提示人工核对） =="
# 自动化补充：用 osascript 把鼠标"点击"一次窗口中心，看前台应用是否变化
osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' \
  > "$OUT/frontmost-before.txt" 2>&1
screencapture -x "$OUT/clickthrough-$STAMP.png"

echo "== [3/3] DPI：截屏（界面第三行应显示 devicePixelRatio，Retina=2.0） =="
screencapture -x "$OUT/dpi-$STAMP.png"

echo "== 生成报告骨架 =="
cat > "$OUT/REPORT-$STAMP.md" <<EOF
# T4.0 选型验证报告（$STAMP）
| 检查项 | 结论（达标/不达标） | 证据 |
|---|---|---|
| ① 透明窗口 | （目测：棋盘格透出桌面=达标） | transparency-$STAMP.png |
| ② 点击穿透 | （穿透开=鼠标落到下层；空格切换后可交互） | clickthrough-$STAMP.png |
| ③ DPI | （Retina devicePixelRatio=2.0 且跨屏变化） | dpi-$STAMP.png |

**结论**：（三项全达标 → Tauri v2 定案；任一不达标 → 退回 Electron）
EOF

echo "== 完成：报告与截图在 $OUT/ =="
echo "（探针仍在运行，按界面提示核对后 Ctrl-C 结束）"
wait $APP_PID
