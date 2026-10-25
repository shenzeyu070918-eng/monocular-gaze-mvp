# 本地三方资源

three.js **0.180.0 / r180**，来源：
https://github.com/mrdoob/three.js/tree/r180

`three/` 保存原始 ES modules、GPUComputationRenderer、EffectComposer、
UnrealBloomPass、OutputPass 及这些模块实际引用的依赖闭包。
许可为 MIT，原始版权及许可全文见 `three/LICENSE`；各文件 SHA-256 见
`three/manifest.json`。模块固定版本，本页不会从 CDN 或其它网络地址加载资源。

粒子图案、星云着色器及交互逻辑为本项目代码；文字由系统字体在内存中栅格化，
不包含外部图片、屏幕截图或用户摄像头内容。

手势权重不放在此目录。Google Hand Landmarker float16 v1 的原始下载地址和
SHA-256 在 `download_models.py` / `model-sha256.txt`，只在安装时下载至忽略的
`models/`，运行时不下载。模型文档：
https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker
