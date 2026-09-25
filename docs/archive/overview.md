# 表情贴图精准定位优化 — 完成报告

## 问题
眨眼眼睑与腮红贴图位置不精准：
1. `_eye_config.json` 坐标是肉眼估算，且三张立绘（idle/happy/pout）共用一套——pout 眯眼、happy 歪头全对不上
2. 腮红位置纯硬编码（0.40 高 / 0.20 宽），无脸颊配置
3. 镜像立绘（_f）时坐标不翻转
4. 凝视/拖拽时立绘整体平移 head_dx，表情贴图不跟随 → 错位
5. 古见眼睛深紫色，深色眼睑盖上去视觉不可见（眨眼"没效果"）

## 方案与实现

### 1. 像素级面部特征检测（新工具 `scripts/tools/detect_face.py`）
- **眼睛**：虹膜特征色 (62,48,73)±8 连通域检测（深色连通域不可行——头发/描边连成一片）
- **腮红**：优先检测立绘自带红晕（面积 ≥2000px 防耳朵/阴影误检）；无红晕时「眼外下方候选网格 + 皮肤覆盖率迭代收缩」，确保腮红椭圆落在脸颊皮肤内
- 皮肤均色采样 → 眨眼盖板色

### 2. 配置升级 `_eye_config.json` v2
- 按 idle / happy / pout 三状态各存 eyes + cheeks 归一化坐标
- 新增 skin_color（#FEF2EA）；保留 v1 格式兼容

### 3. 渲染代码重构（`wb_whale_win.py`）
- 新增 `_face_cfg(state, facing)`：按状态取配置，_f 镜像自动翻转（cx→1-cx、左右互换）
- `_draw_blink`：改「肤色盖板从眼顶向下盖 + 全闭时睫毛线」——眨眼第一次真正可见
- 新增 `_draw_blush`：读 cheeks 配置，替代硬编码 0.40/0.20
- 表情贴图跟随 head_dx 平移，消除凝视/拖拽错位

### 4. 验证
- `tools/verify_face.py`：强制闭眼+腮红渲染 idle/happy/pout/idle_f 四帧，目视全部贴合
- test_motion.py（14 断言）、test_scale_switch.py 回归全部 PASS
- 桌宠已用新代码重启（pid 5508）

## 关键文件
- `scripts/tools/detect_face.py`（新）— 面部特征检测器，`--write` 落盘配置
- `scripts/assets/_eye_config.json` — v2 分状态配置
- `scripts/wb_whale_win.py` — `_face_cfg` / `_draw_blink` / `_draw_blush` 重构
- `scripts/tools/verify_face.py`（新）— 表情渲染验证

## 后续
- 换立绘后重跑 `python tools/detect_face.py --write` 即可重新标定
- `鲸鱼娘项目` 归档目录是换肤前快照，未同步本次改动（如需可再镜像）
