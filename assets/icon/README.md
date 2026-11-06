# LocalRouter 图标源文件 (Icon Sources)

本目录是 LocalRouter 应用图标的**唯一来源**，随仓库版本管理。
所有产物（Windows `build/app.ico`、Web logo/favicon、macOS `.icns`）均从这里构建。
`build/` 整体被 gitignore，生成的 `build/app.ico` 只是本地构建产物——请勿只改产物。

## 当前主流程：AI 生图 → build_from_raster.py

当前采用图标为 `source/doubao_canvas_logo_A_key.jpg`（AI 生成的「钥匙-路由」3D 图，
主色调橙/青/蓝）：一把银色钥匙在齿部折射出三条光路——中间橙色主流、上下青/蓝备选，
呼应「API Key 智能路由分发」的产品定位。

```bash
cd assets/icon

# 1. 从栅格母图构建：自动裁掉白边、测量圆角(实测13.1%)、四角透明化、
#    输出 1024 主图与 256/128/64/48/32/24/16 尺寸（小尺寸带锐化）、合成 ICO
python build_from_raster.py source/doubao_canvas_logo_A_key.jpg

# 2. 分发
cp out/app.ico ../../build/app.ico
cp out/favicon.ico ../../frontend/public/favicon.ico
cp out/icon-master-1024.png ../../frontend/public/logo.png
```

脚本要点（`build_from_raster.py`）：

- 徽章检测：`min(RGB)<190` 或通道差 `>40`（排除中性灰阴影与白底）
- 圆角半径自动测量（顶行/左列首个徽章像素偏移），限制在边长 7%~22%
- Alpha 蒙版 4× 超采样 + 1px 内缩 + 羽化，圆角过渡带零白色残边（已程序化验证）
- 16/24/32px 额外做 UnsharpMask，保证任务栏/标签页可读

换新母图时直接替换 `source/` 下的文件重跑即可；要求：1:1 方形、徽章居中、
背景为纯色/浅色。

## 备选：SVG 矢量方案（generate_svgs.py）

`localrouter-icon-{a,b,c}[-compact].svg` 是此前手工设计的矢量备选方案
（A 青蓝徽章 / B 深空交换机 / C 极简线稿），几何参数集中在 `generate_svgs.py`，
配合 `build_icons.mjs`（需 sharp）+ `make_ico.py`（Pillow）构建。当前未启用，
留作将来需要纯矢量小图标时的底稿。

## 各端引用关系

- `build/app.ico`（生成）— Windows 桌面外壳（PyInstaller）与 Inno Setup 安装器图标
- `frontend/public/logo.png` — Web UI 侧边栏 logo 与 apple-touch-icon；macOS 构建脚本
  `scripts/build-macos-installer.sh` 亦以此生成 `.icns`
- `frontend/public/favicon.ico` — 浏览器标签页图标（`frontend/index.html`，无 SVG favicon）
- `frontend/dist/` — 构建产物，`npm run build` 时从 public/ 复制，无需手动改
- `out/` — 本地构建中间产物（已 gitignore）
