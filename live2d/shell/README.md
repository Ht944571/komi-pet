# Live2D 桌宠外壳（Electron + l2d）

> 分工：**拆层/绑骨（你）→ 运行时与周边（小新，本目录）**。
> 本外壳先用官方示例模型调通；你的 `.moc3` 绑出来后放进 `models/` 直接换。

## 跑起来

```bash
cd /d D:\workbuddy\古见同学桌宠\live2d\shell
npm start
```

首次安装（已装过可跳过）：
```bash
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
npm install --registry=https://registry.npmmirror.com
```

## 冒烟自检（验证窗口/WebGL/l2d/模型加载整条链路）

```bash
npm run smoke
```
看到 `SMOKE:{"ok":true,...}` 即整条链路通；`hitAreas` 会列出模型定义的命中区名。

## 当前模型

`models/Hiyori/`（Cubism 官方示例，moc3 v3）与 `models/Mao/`（**moc3 v5** ——
用于验证「Cubism Editor 5 导出可被加载」，Core 6 向前兼容 v3/4/5）。

切换模型：给 URL 加参数 `?model=../models/Mao/Mao.model3.json`（`renderer/pet.js` 顶部可改默认值）。

## 你的模型接进来（清单）

1. 放到 `models/<名字>/`（model3.json + moc3 + 贴图 + physics3.json）
2. `renderer/pet.js` 顶部 `MODEL` 指向它
3. **参数名对齐**（见 `../拆层手册.md` §5.2）：`ParamEyeLOpen/ROpen`、`ParamEyeBallX/Y`、
   `ParamAngleX/Y/Z`、`ParamMouthOpenY`——名字一字不差，运行时按名字驱动
4. model3.json 里定义 `HitAreas`（`Head` / `Body` / `Skirt`），分区点击就按名字分发

## 本外壳已实现 / 未实现

**已实现**：透明置顶无边框窗 · 逐像素穿透（命中区判定 + `setIgnoreMouseEvents(forward)`）·
拖拽移动（松手记忆位置）· 托盘（显示隐藏/穿透开关/大小/退出）· 视线跟踪（15fps 节流）·
点击分档反馈（气泡 + 参数小反应）· 位置持久化 · 冒烟自检

**未实现（等模型）**：6 档眨眼贴片驱动（手册 §5.2 的参数接上即可）· 睡眠态 ·
穿透模式的托盘细节打磨 · 与 Python 守望（自启/联动关闭）的对接

## 本沙箱的坑（真机没有这些问题）

| 症状 | 原因 | 处理 |
|---|---|---|
| electron.exe 把 JS 当 Node 跑（`require('electron')` 是字符串） | 沙箱预设了 `ELECTRON_RUN_AS_NODE=1` | 启动时 `env -u ELECTRON_RUN_AS_NODE`（npm 的 electron shim 在真机自动处理） |
| GPU 进程连环崩溃（0xC0000005） | 沙箱会话拿不到 GPU | 加 `--disable-gpu --single-process` 仅用于验证逻辑 |
| 页面加载不完成 / executeJavaScript 挂起 | 同上（透明窗 + 无 DWM） | 真机验证 |

> **结论：本外壳在沙箱里无法完成 GUI 级验证，请在真机桌面跑 `npm start` 看效果。**
