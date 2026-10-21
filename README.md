# 视线实验台 — 单目摄像头笔记本 MVP

普通笔记本摄像头 → 本机三维视线估计 → 应用内注视点 → 九点个人校准 → 独立评估与 CSV 导出。首个测试版本 **v0.1.0** 是同学试用基线；当前开发分支为 **M3b 第二阶段（原生热键、面板启动、推理对比与校准补看，实机待验收）**。PR #8 的 Mac 核对结果见验证记录；尚未完成双机或项目精度验收。

**这是下载后在各自电脑运行的应用。GitHub 仓库页面不直接运行眼动模型；无需把摄像头画面传到云端。**

- [测试操作与记录指南](docs/测试操作与记录指南.md)
- [可复制填写的测试记录模板](docs/测试记录模板.md)
- [版本记录](CHANGELOG.md) · [已完成的验证](验证记录.md)

![界面预览，不含摄像头画面](界面预览.jpg)

## 首次安装

M3a 最小窗口可先独立验证（不打开摄像头、不加载模型）：

```sh
# Mac：进入项目文件夹，使用已有项目虚拟环境
./.venv/bin/python -m pip install -r requirements-overlay.txt
./.venv/bin/python overlay.py --diagnostic
```

屏幕底部居中显示「未校准 · 窗口诊断（无摄像头）」，状态条底边默认离完整屏幕底边 90 逻辑像素，四角有编号标记。诊断模式的状态条常驻，不自动淡出。保持单块内置屏环境；有外接屏时先断开。菜单栏托盘提供显示/隐藏和退出。可加 `--duration 30` 在指定秒数后自动退出，方便先检查窗口是否会挡住操作。

Mac 托盘使用透明背景的黑色模板圆环（含 @2x），由系统按深浅色外观着色。**刘海机型菜单栏图标太多时，系统仍可能隐藏或遮住托盘图标**；模板图标不能增加菜单栏可用空间。可先收起其他菜单栏图标或切换菜单较少的前台应用，再检查圆环。启动终端会打印 `isSystemTrayAvailable`、`tray.isVisible()` 和 `tray.geometry()`；可用且 visible 为 True 只表示程序已请求显示，不保证没有被刘海或其他图标遮住，geometry 为空也不能单独证明创建失败。请保留这些原值用于排查。

**看不到托盘时可使用控制面板，或切回启动程序的终端按 Ctrl+C 退出**，无需点击托盘。终端启动时会显示「按 Ctrl+C 退出」；正常采集仍等待工作线程和摄像头清理。Mac 默认启动即显示面板。真实采集模式支持下文的原生系统热键；无摄像头诊断可用 Ctrl+C 或 `--duration` 自动退出，不注册热键。

Mac 优先验证：在标记和文字下面点击、拖动、滚动；切换浏览器、编辑器、不同桌面空间和另一个应用的全屏模式，检查悬浮层仍可见、下方应用保持焦点且正常操作。记录 macOS/芯片、启动命令、每项通过或失败。终端显示完整逻辑尺寸与 DPR；刘海机型应核对完整屏（例如 1512×982），不是扣除菜单栏后的工作区。保存四角截图时不要包含摄像头画面；刘海物理遮挡不改变坐标范围。诊断模式不触发摄像头权限，不提供真实校准或核对，也不能代替真实采集测试。

## M3b：启动悬浮层与控制面板

已有项目环境可按上方命令只安装悬浮层依赖；完整安装可使用 `python3.12 setup_mvp.py --overlay`（Windows 为 `py -3.12 setup_mvp.py --overlay`，RTX 5070 沿用 `--torch-build cu130`）。网页的原安装、启动入口保持原用途。

```sh
# Mac：先结束诊断；关闭网页摄像头，再启动真实 Python 采集
./.venv/bin/python overlay.py --control-panel
# 或双击「启动悬浮层.command」
```

```powershell
# Windows：本轮尚未实机验证
.\.venv\Scripts\python.exe -m pip install -r requirements-overlay.txt
.\.venv\Scripts\python.exe overlay.py --diagnostic
.\.venv\Scripts\python.exe overlay.py
# 或双击「启动悬浮层-Windows.cmd」
```

正常模式直接使用 OpenCV、`camera.index`、预热/重试/黑屏检查以及 M2 默认显示参数，显示时钟仅取读帧的 `capture_ns`。Mac 本机仍需在忽略提交的 `config/local.yaml` 中选择 `devices/mbp.yaml` 和 `camera.index: 1`；index 0 曾实测为黑屏设备。也可用 `--config <文件>` 选择独立配置。网页与 OpenCV 可能占用同一摄像头，请停止一路再切换；不会自动把网页档案用于悬浮层。

没有条件匹配的 Python 档案时，悬浮层显示 **「未校准」**。匹配后显示「九点」，个人修正只进入显示链路，仍采用 M2 眼跳检测和 One Euro；眨眼、失跟或结果超过 `stale_ms` 会让最后有效位置淡出，眼跳不另加位置动画。半径优先取当前条件、当前矩阵最近一次完整 B 组核对的逐目标等权平均误差；无合格核对时使用 `overlay.radius_cm` 配置示意值，屏幕未实测时改用 `fallback_radius_px` 逻辑像素。状态条和面板始终注明半径来源，不把配置值当作精度。

正常模式的状态条在启动或收到有效提示后显示 4 秒，再按 `overlay.fade_ms` 淡出隐藏。眨眼、丢脸、画面无效或过期等状态需持续 `overlay.label_debounce_s`（默认 1 秒）才重新显示；短暂异常不换状态条文字、不重启计时。异常恢复正常跟踪时，若状态条可见，立即从当前透明度进入淡出，跳过剩余保持时间；已隐藏则保持隐藏。启动、暂停、恢复、出错和屏幕变化立即提示，用户操作不受防抖影响；托盘提示始终实时更新。文字用 `QFontMetrics` 测量宽度，左右各留 12 逻辑像素，过长时限制在屏幕内并省略末尾。状态条仅更新新旧所在区域，淡出不影响注视圈。可在 `config/local.yaml` 中覆盖以下选项：

```yaml
overlay:
  label_position: bottom_center  # bottom_center / bottom_right / top_left
  label_bottom_inset_px: 90     # 底部两种位置距完整屏幕底边的逻辑像素
  label_padding_px: 12          # 左右各自的逻辑像素内边距
  label_autohide_s: 4           # 0 表示常驻；--diagnostic 始终常驻
  label_debounce_s: 1.0         # 短暂异常的持续时间门槛；0 表示不防抖
```

`top_left` 沿用左上 40 逻辑像素位置；`bottom_right` 距右边默认 40 逻辑像素，两者的侧边距可用 `label_inset_px` 调整。Mac 实测时核对默认状态条在 Dock 上方、不再遮住左上红黄绿按钮；自然眨眼时应在启动约 4 秒后淡出并保持隐藏，持续离开镜头超过 1 秒才重新提示，回到镜头后不续时。分别触发暂停/恢复，检查立即重新显示，再用 `label_autohide_s: 0` 检查常驻。

托盘和面板共用同一套操作：当前模式、九点校准、精度核对、手动加载 web 档案、停用修正、录制、显示/隐藏、暂停/恢复、打开网页和退出。`overlay.panel_on_start` 可设 `auto / always / never`，默认 `auto`：**macOS 等同 always，不依赖托盘几何**；Windows 在托盘不可用或延迟复查不可见/高度为 0 时打开面板。`never` 禁止自动弹出，`--control-panel` 可强制打开。延迟复查仍打印原始 isVisible/geometry，不能据此判断刘海下图标可见。关闭面板仅隐藏，可通过已成功注册的热键重开。

真实模式使用 macOS Carbon `RegisterEventHotKey`、Windows `RegisterHotKey`，只接收已注册组合；无全键盘监听，不申请「输入监控」权限。默认 `Ctrl+Alt+G` 显示/隐藏、`Ctrl+Alt+P` 暂停/恢复、`Ctrl+Alt+C` 九点、`Ctrl+Alt+O` 打开面板（Mac 为 Control+Option，字母指 ANSI 对应物理键）。在 `overlay.hotkeys` 的 `toggle / pause / calibrate / panel` 下覆盖，支持 Ctrl、Alt/Option、Shift、Meta/Command/Win 加一个字母或数字，空字符串禁用该项。与输入法或其他应用冲突时，终端、状态条及面板逐项列出失败组合和系统错误，其他组合继续可用；修改配置后重启。退出时注销热键；任务就绪限制同面板按钮。

隐藏悬浮层不停止采集；暂停会取消任务、结束录制，停止采集/推理线程并由采集线程释放摄像头，保留模型供恢复；退出才释放模型。初始化与退出仍在后台控制线程执行，失败可重试。打开网页会先暂停悬浮层摄像头，再打开配置的本机地址（网页服务须已启动）。屏外坐标不裁剪，不伪造边缘位置。

九点校准和精度核对只在单块内置屏的独立全屏窗口进行；任务期间关闭穿透层显示，Esc、窗口关闭、退出全屏、暂停或错误均可取消，完成/取消后恢复穿透层。校准复用网页的九点、稳定等待、停留和最小有效帧数配置，取各点原始坐标中位数拟合；核对复用相同独立点位与 `evaluate()`，报告 A/B、中央/边缘、带符号偏移、缩放斜率及三维射线角误差，核对点永不参与拟合。两者按 `capture_ns` 归属取样窗口，不用滤波输出。首次九点未完成时取消不保存，原有档案保留。

完成九点后，面板显示九个**校准点自身拟合残差**：将各点原始中位位置经过本次仿射矩阵，与该校准目标比较；这不是独立 B 组精度，也不用于圆环半径。尺寸实测时用厘米，超过 `overlay.calibration_redo_cm`（默认 2.0）会询问「是否补看这些点」。同意只重看标出的点，将每点新旧稳定样本合并、重新取中位数，再等权拟合九点；**最多一轮，不引入非线性映射**。拒绝提示，或补看时按 Esc/关闭/退出全屏，会保留首次完整结果；暂停、错误、条件变化或退出则终止本次候选保存。屏幕未实测时只显示逻辑像素残差，不将 2 cm 当作像素阈值，也不据此自动提示补看。仍可主动重新开始完整九点。

JSONL 新增严格数值 `calibration_quality` 事件：`phase` 为 initial/final/redo_cancelled，`quality` 含 unit、round（0/1）、threshold、九个 points（target_id、目标、raw_median、corrected、residual、sample_count、redo）及 mean/maximum。档案 `calibration.quality` 保存最终选用结果；内部原始样本只用于这次合并，不写入档案，不包含图像。初次及补看距离样本一起求中位数。任务和补看借用同一录制会话，独立核对仍应另做。

Python 档案写入被 Git 忽略的 `data/profiles/python/<设备名>.json`，含 `pipeline: python`、矩阵、时间、坐标说明、条件、`calibration` 和 `latest_check`；无设备文件时名称取 `overlay.profile_name`。设备指纹仅计算内参、畸变、标定分辨率、屏幕尺寸/分辨率、摄像头偏移等语义字段，改注释、排版、日期或 RMS 不使档案失效。匹配还要求实际摄像头编号/分辨率、灰度、实际内参与去畸变、完整逻辑屏/DPR和实测几何一致；首帧条件就绪后才自动加载，并按 `profile_poll_s` 检查替换/删除，错误会卸载修正并说明原因。运行中设备文件参数改变需重启，不把旧引擎当作新内参。

旧无 `pipeline` 档案按 web，新网页档案标为 web，原路径与网页流程不变。手动 web 加载必须确认裁切、内参、时间戳差异风险，不改写为 Python 档案，也不使用网页 B 组半径。面板显示「九点 · …分钟前」，时间仅显示，不自动过期。校准及核对稳定窗口内估计人脸到屏幕距离的中位数（当前引擎 `distance_cm`，面部中心到屏幕平面的垂直距离）写入档案和 `evaluation_summary`；缺失写 null，只记录，不作距离拦截或警告。

录制开关无需重开摄像头。任务借用已有手动会话，否则自动开启并在结束后关闭，始终只有一个会话；录制失败在面板和状态条提示，手动开关回到关闭。JSONL 写原始帧、target、calibration、calibration_quality、evaluation_summary 和 end，不写图像。Python 帧用 `capture_ns`；新事件的 `event_ns` 为单调时钟，目标取首次绘制提交时刻，实际扫描显示可能稍晚。`server_ns` 保留为入队时刻；回放新事件优先 event_ns、旧事件回落 server_ns，不伪填 browser_time_ms。

连续 `no_face` 达到 `overlay.idle_after_s` 后，以 `idle_fps` 限制 OpenCV 读帧调用频率；发现人脸后恢复原读取方式。首次重新发现人脸最多需等下一个低频读帧周期；不是预测恢复。该设置不保证驱动改变硬件帧率或达到某个 CPU 数字，须实测。默认不开录制，不写图像、屏幕或输入数据。

Mac 首次使用前检查「系统设置 → 隐私与安全性 → 摄像头」中的终端权限；未决定时 OpenCV 可能触发系统请求，允许后若预热已超时，可从托盘恢复，必要时重启终端。已拒绝/受限时启动会给出中文指引，不将权限问题误报为标定结果。M3a 不需要也不申请「输入监控」权限。

目前只接受唯一启用的内置屏；检测到外接/多屏或无法确认内置连接时拒绝启动。Windows 的屏幕连接识别使用系统只读查询，尚待实机核对。运行中屏幕数量、逻辑尺寸或缩放改变时暂停并提示重新启动，避免继续使用旧坐标；不要假定外接主屏就是内置屏。Windows 独占全屏应用可能无法覆盖。

M3b 实机检查清单见 `验证记录.md`。推理配置示例（只改本机 `config/local.yaml`，不要覆盖已有相机/设备设置）：

```yaml
inference:
  device: cpu      # auto / cpu / mps / cuda；Mac auto 暂保持 CPU，Windows CUDA 兼容
  num_threads: 4   # 可比较 2；旧 cpu_threads 仍可用，同层两者冲突时报错
  max_fps: null    # null 不限制；有限正数只限制 Python 推理启动频率
```

显式 MPS 不可用时回到 CPU 并打印原因，实际设备写入录制；算子不支持等推理错误会明确停止，不偷偷以 CPU 逐算子回退冒充 MPS。请取消 `PYTORCH_ENABLE_MPS_FALLBACK=1` 后再测。采集仍只保留最新帧、暂停可中断帧率等待；帧时间仍为真实 `capture_ns`，显示继续 One Euro，不插值。网页同步流程不受 max_fps 限制。

关闭悬浮层和网页摄像头后，在 Mac 运行（相机输入仅在内存中，数量有上限，不保存画面）：

```sh
./.venv/bin/python tools/compare_inference.py --frames 20 --warmup 3 --threads 2 4
# 仅有数值 JSON 输出，可自行重定向到 data/ 下供比较
```

工具让 CPU2/4、MPS2/4 处理同一组原始帧，并让各网络处理同一组 CPU 基准归一化人脸块；预热后输出实际设备、状态/有效数、完整引擎耗时、网络前向耗时、含预处理/传输的人脸块路径耗时（中位数/P90），以及相对 CPU 基准的原始坐标和模型弧度输出绝对差。MPS 前后同步；无脸时没有固定人脸块数据，差异为空，不给精度结论。MPS 不可用标为 skipped，执行失败标为 failed，不生成假成绩。完整引擎含 MediaPipe 检测/归一化和几何处理，网络前向比较才是同一张量输入口径。输出差异不是对真实目标的精度。

`num_threads` 只约束 [PyTorch 算子内 CPU 并行](https://docs.pytorch.org/docs/main/generated/torch.set_num_threads.html)，不等于进程总线程数；此前 cpu_threads 已为 4，用户实测却约 63 线程。线程池还来自 MediaPipe 的任务/推理执行器（含 TensorFlow Lite/XNNPACK）、OpenCV 及其底层并行库、Qt/macOS 事件与图形服务，另有采集/推理/录制/控制线程。**63 个线程的实际分项尚未采样，不能断言每库各占多少**。用活动监视器同时记录 CPU、% GPU、线程总数，必要时通过「取样进程」按调用栈归属各库；对比 num_threads=2/4 和 max_fps=null/不同正数时，保持设备、输入、显示/录制及其他程序条件一致，记录采样时长、耗时/有效率、主观响应和功耗。不预先改变默认后端或线程数。

## 网页首次安装

推荐 Python **3.12（64 位）**、Chrome、内置摄像头与单块笔记本屏幕。需要联网下载 Python 依赖及约 47 MB 模型；PyTorch 等依赖体积较大，请预留数 GB 空间。安装完成后，推理与画面处理在本机进行。v0.1.0 曾在 macOS M5 Pro 验证；新版在 Windows 和 Mac 的真实摄像头全流程仍待本次实机验证。Linux 暂不作为本轮支持目标。

1. 验证基线时，从 Releases 下载 **v0.1.0** 的 Source code ZIP，或切到 `v0.1.0` 标签。验证 M2 时使用对应 PR 的 `m2-filter` 分支；合并后使用 `main`。两个版本分别解压/检出，环境不要混用。
2. 从 [Python 官网](https://www.python.org/downloads/) 安装 Python 3.12。Windows 安装时启用 Python Launcher；macOS 安装后重新打开终端。
3. 在解压目录打开终端，执行对应命令（整个目录路径含空格或中文也可以）：

**Windows（PowerShell 或命令提示符）**

```powershell
py -3.12 setup_mvp.py
.\.venv\Scripts\python.exe run.py
```

以后可双击 `启动-Windows.cmd`。不需要激活虚拟环境或更改 PowerShell 执行策略。

**macOS**

```sh
python3.12 setup_mvp.py
./.venv/bin/python run.py
```

以后可双击 `启动.command`；若 Finder 因权限或系统策略无法双击，仍用上面的运行命令。

4. 保持启动终端打开，在 Chrome 访问 **http://127.0.0.1:8765**，等待「模型就绪」。关闭终端或在终端按 Ctrl+C 会停止服务。

安装程序会创建项目内 `.venv/`，安装当前推理链路所需依赖，再安装 `vendor/` 中固定版本的 ptgaze。默认安装无需另装 Git、dlib、CUDA Toolkit 或提供 Hugging Face 账号。Windows RTX 5070 请按下节安装 cu130 构建。模型从原始来源下载并校验；已经存在且校验通过时跳过下载。初次安装失败后可重新执行安装命令。不要复制别人的 `.venv` 文件夹到自己电脑使用。

## Windows RTX 5070：cu130 安装与设备检查

在新版目录中运行（首次安装和更新已有环境都使用此命令）：

```powershell
py -3.12 setup_mvp.py --torch-build cu130
.\.venv\Scripts\python.exe run.py
```

此安装路径先从 PyTorch 官方 [torch cu130 索引](https://download.pytorch.org/whl/cu130/torch/)与 [torchvision cu130 索引](https://download.pytorch.org/whl/cu130/torchvision/)安装 `torch==2.14.1+cu130`、`torchvision==0.29.1+cu130`，再安装公共依赖和本地 ptgaze。官方索引存在 Windows Python 3.12 x64 轮子；Mac 默认安装的公共版本也是 **torch 2.14.1 / torchvision 0.29.1**，Windows 完整版本多出 `+cu130` 构建标识。显式固定此标识，避免同版本 CPU 构建被误认为已满足安装要求。

[NVIDIA 官方表](https://developer.nvidia.com/cuda/gpus)将 RTX 5070 列为 Blackwell 计算能力 12.0；[PyTorch 官方 CUDA 支持矩阵](https://github.com/pytorch/pytorch/blob/main/RELEASE.md#pytorch-cuda-support-matrix)列出 2.14 的 Windows CUDA 13.0 构建支持 Blackwell 12.0。因此采用 CUDA 13.0（满足 12.8 或更新的要求），统一两平台公共版本。Windows 仍需支持 CUDA 13.0 的 NVIDIA 驱动；索引和支持矩阵核对不等于已经验证 RTX 5070 实机安装或推理成功。

安装选择保存在 `.venv/.torch-build`，以后省略选项重新安装会沿用该选择，下载失败后重试也保留。旧环境保存的 `cu128` 选择会自动迁移为 `cu130`；升级前检查驱动是否支持 CUDA 13.0。改用默认依赖入口可使用 `--torch-build default`；后续重装请继续使用安装程序，以保持选定的 CUDA 索引。

检查实际环境：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print('torch:', torch.__version__); print('CUDA build:', torch.version.cuda); print('CUDA available:', torch.cuda.is_available()); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
nvidia-smi
```

启动网页服务后打开 `http://127.0.0.1:8765/api/status`，记录服务真正使用的 `device` 和 `device_name`。Python 检查 CUDA 可用只是环境检查，仍需真实推理成功才能勾选实机项；失败时保留错误，不能把 CPU 回退写成 GPU 验证通过。

## 配置与公共核心

`gaze_core/` 提供引擎、几何计算、数值录制和 Python 摄像头管线，不依赖 FastAPI 或网页。引擎与几何模块已从仓库根目录迁入。`app.py` 从公共包导入；网页仍是浏览器采集、同步逐帧请求 `/api/frame`，M1a 接入设备文件、相机去畸变与视距自检；M1b 增加全屏校准/评估、逐点汇总、三维射线角度与个人档案，显示平滑仍仅用于显示。CSV 原有 26 列及 M1a 追加的 8 列全部保持名称、顺序和含义，新增字段继续追加在末尾。

默认配置为 `config/default.yaml`，本机覆盖见下方 M1b 说明：

- `inference.device`：`auto`（默认，有 CUDA 时使用 GPU，否则 CPU）、`cpu` 或 `cuda`。显式指定 `cuda` 但不可用时报告错误。按本轮约定，Mac 使用 CPU，不自动选择 MPS。
- `camera.index`、`width`、`height`：Python 管线的摄像头编号和请求尺寸，默认请求 **640×480**，与网页请求一致。设备可能返回不同尺寸，以测速报告中的实际帧尺寸为准。
- `camera.grayscale`：Python 推理是否使用与网页相同的三通道灰度转换。网页继续使用原有灰度开关。
- `camera.backend`：默认 `auto`。Windows 自动后端打不开、返回黑帧或持续读帧阻塞时，可退出程序后改为 `dshow`（DirectShow）再试，并记录 `actual_backend`。它是兼容性选项，不保证提高帧率或减少延迟；macOS 保持 `auto`。
- `camera.warmup_timeout_s`：首帧预热窗口，默认 3 秒，从开始尝试读帧计时；窗口内的失败会重试，取得首个有效画面即继续，不固定等待 3 秒。标定工具与 Python 管线共用此逻辑。超时仍从未读到画面时，提示检查终端摄像头权限并关闭占用摄像头的网页或程序；该提示是排查建议，不能据此认定本次故障一定是权限或占用。
- `camera.read_retry_count` / `read_retry_interval_s`：取得首帧后，默认失败一次再重试 3 次（连续第 4 次失败才报「运行中途读帧失败」），失败之间等待 0.05 秒；每次成功重置计数，成功读帧不加等待。连续失败期间不重复返回上一帧，也不增加采集帧数。暂停/关闭可以立即打断重试等待；驱动内部阻塞的 `read()` 仍须自行返回，摄像头仍只由持有它的线程释放。
- `camera.startup_timeout_s`、`stop_timeout_s`：资源初始化和停止等待时间。若后端/驱动阻塞导致线程无法退出，会明确报错，不能把这次释放视为成功；可检查权限、占用程序和后端。
- `device_file`：默认 `null`；设置为 `devices/<设备名>.yaml` 后，相对 `config/default.yaml` 所在目录读取设备文件。明确配置的文件缺失或损坏会报错，不会悄悄回退。
- `geometry.width_cm` / `height_cm`：默认空值，移除 Mac 专用尺寸。优先读取设备文件，也可在配置或网页填写实测可视区域宽高。缺少尺寸时仍可估计视距，但不产生屏幕注视坐标（`screen_unconfigured`）。未确认实测或未全屏时，评估不输出厘米误差。
- `recording.output_dir`：默认 `data/sessions`；`recording.queue_size` 为各数值队列上限，`close_timeout_s` 为关闭时的等待时间。`request_timeout_s` 是网页录制请求超时，需大于关闭等待时间；超时会明显提示，收尾不无限等待回执。`data/` 已加入 `.gitignore`，录制文件不提交到仓库。
- `jump.point_count`、`dwell_ms`、`min_distance`：网页跳点任务默认点数、每点停留毫秒数和相邻目标的最小欧氏距离，可在「跳点任务设置」中调整。距离单位为归一化全屏坐标，不是厘米；至少 8 点，以包含四角及四条边的锚点。无法同时满足覆盖和间距的配置会拒绝启动。
- `jump.margin`：按屏幕宽、高分别留出的边距比例，默认 `0.05`（5%）；网页可用百分比调整，需大于 0% 且小于 50%。锚点向内移动，随机点也只在 `[margin, 1-margin]` 内生成，最小间距按调整后的实际坐标计算。边距不足以容纳完整目标圆时会拒绝开始，需增大边距或全屏尺寸。

视频模式使用严格递增的单调时钟毫秒。ptgaze 的这一处本地适配已记入 `vendor/NOTICE.md`；每次更新源码后需要重跑安装程序，使环境使用更新后的适配。

## M1a：设备标定与视距自检

先关闭网页及其他程序对摄像头的占用。在项目目录使用当前 `.venv` 的 Python 运行：

```powershell
# Windows
.\.venv\Scripts\python.exe tools/calibrate_camera.py --device my-laptop --corners 9 6 --square-mm 20
```

```sh
# Mac
./.venv/bin/python tools/calibrate_camera.py --device my-laptop --corners 9 6 --square-mm 20
```

命令中的 **9×6 是内角点数量**，**20 mm 是示例方格边长**，必须按自己打印并实测的棋盘格填写，不能量整张纸或外框。默认请求 640×480；预览与终端都会输出**实际采集分辨率**。请先从网页「相机内参状态」或 `/api/status` 确认网页实际送入引擎的分辨率，再用 `--width` / `--height` 在该分辨率下标定。网页可能把浏览器摄像头原始画面等比例缩小；若驱动不能提供对应模式，不应把模式裁切误当作缩放。

预览中显示检测到的角点：空格采集，Enter 计算，R 清空角点重采，Q / Esc 退出。需要清晰、完整的棋盘，覆盖不同水平/垂直位置、距离和倾斜角度；过于相似的视图会拒收，数量或位置/角度覆盖不足时拒绝计算。`--views` 控制最低数量（默认来自 `calibration.views`），多样性阈值也在配置中。工具输出整体重投影 RMS 与每视图 RMS（像素）；超过 `--max-error-px` 或配置阈值时提示重新采集，**不保存、不覆盖设备文件**。低重投影误差不能证明视线精度。

成功后保存 `config/devices/my-laptop.yaml`，设置 `device_file: devices/my-laptop.yaml` 并重启服务。文件包含 K、5 项畸变系数 `[k1,k2,p1,p2,k3]`、实际标定像素分辨率、重投影误差、UTC 标定时间，以及以下实测字段：

| 字段 / CLI 参数 | 含义 |
|---|---|
| `screen.width_cm` / `--screen-width-cm` | 屏幕可视区域实测宽，不含边框 |
| `screen.height_cm` / `--screen-height-cm` | 屏幕可视区域实测高，不含边框；宽高须同时提供 |
| `screen.measured` | 工具在提供实测宽高时设为 true；未填写时尺寸为 null，不能报告厘米误差 |
| `screen.resolution` / `--screen-resolution 宽 高` | 系统逻辑分辨率；Windows 缩放与 Mac Retina 下不要填面板物理像素；网页 `screen.width/height` 可核对 |
| `camera_offset_cm.horizontal` / `--horizontal-cm` | **用户面对屏幕时，摄像头中心在可视区域中线右侧为正**，单位 cm |
| `camera_offset_cm.vertical` / `--top-cm` | **摄像头中心在可视区域上沿上方为正**，单位 cm，与既有 `top_cm` 一致 |

工具可同时接收这些实测参数；也可之后手动填写设备 YAML，补全逻辑分辨率与实测尺寸。水平、垂直偏移分别量摄像头镜头中心相对可视区域上沿中点的位置，不用屏幕外壳顶边。仓库包含用户实测的 `config/devices/mbp.yaml`，只适用于该 Mac 的设备条件；Windows 等其他设备必须自行标定与实测，不可借用该参数。

标定默认只在内存中保留角点，不保存采集图像。只有显式传入 `--save-images data/calibration/my-laptop` 才会把本机图像写入被 Git 忽略的 `data/`；拒绝目录越界和符号链接越界。会话录制始终不接收图像或 base64。Windows 可按需要加 `--backend dshow`；Mac 保持 `auto`。macOS 若权限失败，请先在「系统设置 → 隐私与安全性 → 摄像头」为实际使用的终端授权，退出占用摄像头的程序后重试。

内参使用规则：

- 实际与标定分辨率相同：使用标定内参。长宽比相同但尺寸不同：等比例缩放 K，提示已缩放；D 保持不变。这遵循 [OpenCV 相机标定文档](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html) 的缩放与去畸变约定。
- **长宽比不同：拒用该标定内参，改用估计 K 和零 D**。状态、录制会话/帧和 CSV 都标记 `intrinsics_source=estimated`、`intrinsics_reason=aspect_ratio_mismatch`，仍保留设备文件名。未选择文件时原因是 `no_device_file`。相同比例也不能保证视场相同；驱动模式、裁切或镜头改变应重新标定。
- `camera.undistort: true` 默认开启；`undistort_alpha` 控制 OpenCV 新矩阵，默认 0。映射按相机分辨率预计算并缓存，每帧只 remap；不再裁切 ROI。图像去畸变后，姿态与视线归一化统一使用对应新 K 和零 D，预览关键点映射回原始视频。未使用标定内参时不执行无意义的零畸变映射，明确记录「已开启、未应用」。
- 关闭去畸变用于对照：保留输入 K/D，姿态求解使用对应 D；视线图像归一化仍面对未矫正的画面，因此不能把关闭状态当作完全校正的链路。更改配置后重启。CSV 末尾追加 `intrinsics_source,device_file,intrinsics_reason,undistort_enabled,undistort_applied,intrinsics_scaled,camera_width_px,camera_height_px`；`geometry_estimated` 随实际内参来源更新，不代表全部物理参数已实测。

网页「视距自检」依次引导实测 **40 / 50 / 60 cm**。用尺子量镜头到眼睛/鼻梁附近面部中心的前后距离，保持正对相机；估计值是模型面部中心在相机坐标中的 **Z 深度**，并非侧坐时的欧氏直线距离。每档先量好再点击采样，配置 `distance_check` 控制稳定等待、采样时长、最低有效样本数与偏差提示阈值。显示估计中位数及 `估计 − 实测` 的带符号偏差；样本不足时记录空估计并提示重试，不补值。

自检复用正在进行的手动录制；否则自动开启单一会话，完成或取消后关闭，不嵌套。结果写 `distance_check`，`sample_start_ms` / `sample_end_ms` 来自有效帧的浏览器取帧时钟，`browser_time_ms` 是结果处理时刻；服务器另记单调时钟。自检不参与九点拟合或评估统计。相机标定前后 A 组对照及双机待测记录见 `验证记录.md`；M1b 的评估边缘覆盖、按目标点统计与个人档案说明见下节。

## M1b：本机配置、全屏评估与个人档案

`config/local.yaml` 已被 Git 忽略，用于本机设置；默认文件继续保持 `device_file: null`。例如本次实测 Mac 使用：

```yaml
device_file: devices/mbp.yaml
camera:
  index: 1
  undistort: true
```

读取顺序为 default → local：同名映射递归合并，数组/标量整项替换，`null` 明确覆盖；合并后统一校验。设备文件提供的屏幕实测尺寸与偏移随后覆盖几何字段。启动终端和网页「实际生效配置与来源」显示实际值及逐项来源；改动后重启服务。Python 测速显式 `--config` 指定其他文件时只使用该文件，不混入本机 local；CLI 灰度或标定工具的相机参数优先，并标注命令行来源。网页灰度及可编辑几何由界面控制，录制记录实际请求值。

**相机编号保护**：先关闭网页摄像头和其他占用程序，再列出候选编号：

```sh
# Mac
./.venv/bin/python tools/list_cameras.py --indices 0 1 2
```

```powershell
# Windows
.\.venv\Scripts\python.exe tools/list_cameras.py --indices 0 1 2
```

工具逐个打开、预热、输出打开状态、实际分辨率及平均亮度（0–255），随后释放，不保存图像。无画面仍按原预热/重试规则报错。标定工具与 Python 管线共用黑屏保护：从开始读帧起等待 `camera.warmup_timeout_s` 的曝光稳定窗口，之后均值不大于 `camera.black_threshold`（默认 2）的连续帧达到 `camera.black_frame_count`（默认 10）时停止并提示选择正确编号；亮帧会清零连续计数。黑暗环境也可能触发，请核对现场光照，不把此诊断当作相机身份识别。

Mac 实测 index 0 为黑屏设备、index 1 为内置相机，这是本机观察，其他机器需重新列出。Python 录制会话的 `camera.camera_index` 是实际使用的 OpenCV 编号。浏览器没有可靠的 OpenCV 编号映射：网页记录视频轨道实际返回的 `camera.browser_camera.device_id/label`，同时将 Python 配置单独记为 `configured_camera_index`，不将后者冒充网页实际编号。

**全屏坐标和目标**：九点校准、独立评估必须全屏才能开始；退出全屏、改变窗口或输入条件会取消。目标直接使用全屏归一化坐标显示，与测试区布局无关，校准矩阵为 `[raw_x, raw_y, 1] @ matrix`，输入、输出均为全屏归一化坐标，未滤波、不裁剪。评估保留中央区域点，并加入四角及四边锚点；边距与跳点共用设置，默认 5%，启动时检查目标圆完整显示。评估点与校准点错开，采用随机顺序，`target_id` 是稳定身份，区别于呈现顺序 `index`。评估标签不参与校准拟合；没有加入固定 16°、固定平移或缩放修正。

`evaluation` 配置中央点、九点校准点、稳定等待和每点停留时长。默认取目标出现后 650–2200 ms 的评估帧（浏览器 drawImage 后的取帧时间），校准有效样本不足时可延长至配置上限；每点以原始坐标中位数参加仿射拟合。任务切换或结束时，晚返回的帧仍归属取帧时的目标，收尾等待在途帧后才汇总。目标事件时间取设置位置的 requestAnimationFrame 回调，实际显示可能晚一个刷新周期，不能当作物理显示时间。

**统计口径**：原逐帧平均/中位/P90、阈值比例与有效率继续保留；新增汇总先对每个点的稳定窗口取径向误差中位数，再让各目标点等权汇总平均值、P90、≤1.5 cm、≤2 cm 比例。中央、边缘及全部分别报告；缺失目标保留为无结果，显示有效点数/预期点数及完整性，未完成任务明确标注。阈值比例分母为有有效厘米结果的目标点数，必须连同覆盖数查看，不能把缺失边缘当作达标。A 原始输出和 B 九点修正后输出分开，未校准时没有 B。带符号的水平/垂直偏移也是先取各点中位数再平均，预测比目标向右/向下为正。

**角度采用三维射线**：以每帧 `origin_m`（相机坐标中的面部中心，单位 m）为共同起点，预测点、目标点按实测屏幕尺寸和摄像头偏移换算到屏幕平面 `Z=0`。坐标换算为 `X=(宽/2+水平偏移−u×宽)/100`、`Y=(v×高+上沿偏移)/100`。报告两条三维射线夹角，并报告射线航向之差 yaw（用户向右为正）和仰俯角之差 pitch（向下为正）；yaw 为 `atan2(-X,-Z)`，pitch 为 `atan2(Y,hypot(X,Z))`，其中 XYZ 是从 origin 指向屏幕点的向量。两分量不是平面位移除视距的近似，也不保证其平方和等于夹角平方。角度按点取中位数后等权平均。有效预测缺少 origin 的帧单独计数，非法 origin 另计；它们保留位置统计、排除角度统计。没有实测屏幕尺寸时厘米和角度均不可用。

水平/垂直缩放系数分别对各点的预测坐标中位数与目标坐标做含截距的一元线性拟合，单位为归一化坐标比；没有目标轴向变化时不可用。拟合仅作诊断，不将结果反馈到模型或校准。汇总写入录制的 `evaluation_summary` 事件，含 A/B、各区域和逐点统计、覆盖/缺 origin 计数、任务完成状态及浏览器单调时间，replay 支持该事件。

CSV 原有前 34 列保持不变：其中 `target_x/y`、`uncalibrated_x/y`、`calibrated_x/y` 继续为测试区归一化坐标，`raw_screen_x/y` 为全屏原始坐标；像素误差使用逻辑像素。新增末尾列为 `region,target_id,target_screen_x,target_screen_y,calibrated_screen_x,calibrated_screen_y,origin_x_m,origin_y_m,origin_z_m,browser_capture_ms`；后者为当前页 performance.now() 毫秒，旧 `capture_time_ms` 保持原有导出含义，不拿它跨页面对齐录制。不会把 DPR 再乘一次。

**个人档案**：九点校准完成后，原子写入忽略的 `data/profiles/<设备名>.json`，包含全屏矩阵、nine_point 来源、B 组、保存时间、校准编号、设备文件名、屏幕条件及坐标定义，供后续悬浮层读取。选了设备文件时固定使用该文件的设备名；没有设备文件时，需在网页填写设备名。写入失败明显提示，不声称保存成功；成功拟合仍可在当前页面使用。不会自动加载历史档案为 A 组制造个人修正。

## M2：显示滤波、对比试用与离线调参

显示现在使用同一套 Python 核心：**未裁剪全屏原始坐标 → 当前个人修正矩阵 → I-VT 眼跳检测 → x/y 独立 One Euro（眼跳时 reset）→ 网页显示**。窗口坐标换算和边界提示在最后处理。`static/app.js` 原固定平滑已移除；网页仍同步逐帧请求，显示处理随同一响应返回，不增加逐帧 HTTP 请求。原始 `raw`、校准样本、评估 A/B 坐标及 CSV 误差不经过显示滤波；原始数值录制格式保留。

**时间轴**：网页显示只使用 `browser_capture_ms`（drawImage 后取的 performance.now()），Python 可选显示管线只使用 `capture_ns`（OpenCV read 返回后）。不以服务器接收、推理完成或浏览器收到响应的时刻替代。Python 显示将首个 capture_ns 保存为整数原点，再计算相对秒数；浏览器各页时钟彼此独立。页面、摄像头开启世代、显示修订号隔离滤波状态，旧请求不能覆盖新状态；校准/模式/几何改变、暂停恢复、丢失跟踪及长间隔会重置。重复/倒退时间戳不更新历史，缺少取帧时间明确报告不可用。

One Euro 的 `filter.min_cutoff`、`beta`、`d_cutoff` 都可通过 `config/local.yaml` 覆盖；x/y 分别保存状态，参数作用于全屏归一化单位。实现参考 [One Euro 作者的算法定义](https://gery.casiez.net/1euro/)。**默认采用候选 44：min_cutoff 2.0、beta 0.05、d_cutoff 1.0、角阈值 90°/s，来源为 Mac 单用户、B 九点校准后 3 次跳点任务及非盲测主观试用，待多人验证。** 未加入固定俯仰角、平移或缩放修正，滤波不能消除偏差。

I-VT 在 `fixation.window_ms` 的短时间窗口比较首尾三维单位射线夹角，除以真实时间差得到 deg/s。射线由各帧实际 `origin_m` 和修正后的屏幕点构造，复用 M1 的屏幕几何；窗口保留一帧跨越边界的样本，不假定固定帧率。`angular_threshold_deg_s` 控制眼跳判定。缺少/非法 origin 或屏幕未实测时使用独立的 `fallback_threshold_norm_s`（全屏归一化距离/秒），输出回退原因和单位，不拿两种速度共用阈值。窗口、最短注视时长和断流上限均在配置中。输出 unknown/candidate/fixation/saccade，以及注视段起止时间、中心、时长、样本数和速度来源；中心由修正后未滤波坐标计算。真实 origin 的估计误差及头部运动会影响角速度，仍须本机数据验证。

网页「**对比显示**」默认关闭，打开后绿色为滤波结果，橙色为同一份个人修正后的未滤波结果；未校准模式中两者均从未个人修正的坐标开始。两个点使用相同几何、逻辑像素与边界提示，超出屏幕的数值不裁剪进统计。为保持原任务采样行为，校准、评估和跳点任务中注视显示点仍隐藏；完成任务后在普通显示区主观比较。对比开关只改变可见性，不改变滤波历史或评估数据。

当前九点校准矩阵可直接使用；也可在全屏下明确点击「**加载本机九点档案**」。加载检查设备文件、全屏坐标定义、逻辑尺寸、实测状态和相机偏移，条件不匹配则拒绝。不会自动加载档案使 A 组变成 B 组；启用后使用 B 组个人修正。旧档案未记录全部相机输入模式，仍需确认与保存时的分辨率、去畸变及摄像头条件一致。

Python 可选接入同一显示管线，默认测速仍保持原始推理链路：

```python
from gaze_core.capture import CameraPipeline
from gaze_core.config import load_settings
from gaze_core.display import DisplayPipeline

settings = load_settings()
display = DisplayPipeline(settings.filter, settings.fixation)
# 使用已核对的档案矩阵时传入 DisplayPipeline(..., matrix=profile['matrix'])。
pipeline = CameraPipeline(settings, display_pipeline=display)
# latest Prediction.data 的 display 字段为派生显示；原始录制仍是 engine 输出。
```

离线函数 `gaze_core.filters.zero_phase_smooth` 单独提供固定系数低通的前后向处理，使用 [SciPy sosfiltfilt](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html)。不均匀时间先按连续段重采样，处理后映回原时间点；不跨断流，短段原样返回并注明未滤波，端点仍可能存在边界效应。截止频率、阶数及断段阈值由 `offline_filter` 配置。该函数用于后续离线分析，当前不接入实时显示、校准或评估。

**Mac 实机调参流程（本轮三份录制已分析，单用户非盲测试用已记录，待多人验证）**：

1. 重启新版服务并刷新网页，核对 local 的 mbp 设备、camera.index 1、标定内参＋去畸变、全屏及实测尺寸，保持相同用户/坐姿/灰度/光线条件。先做九点校准。
2. 在全屏下依次运行 **3 次跳点任务，每次 20 点**。建议让任务自动录制，每次结束检查正常收尾；不要在任务中换矩阵或设备条件。记下网页显示的三个 JSONL 文件名。
3. 安装离线绘图依赖，再把下面示例文件名换成实际三个文件运行：

```sh
# Mac
./.venv/bin/python -m pip install -r requirements-analysis.txt
./.venv/bin/python tools/tune_filter.py "data/sessions/session-1.jsonl" "data/sessions/session-2.jsonl" "data/sessions/session-3.jsonl"
```

```powershell
# Windows（本轮实机仍未验证）
.\.venv\Scripts\python.exe -m pip install -r requirements-analysis.txt
.\.venv\Scripts\python.exe tools/tune_filter.py "data/sessions/session-1.jsonl" "data/sessions/session-2.jsonl" "data/sessions/session-3.jsonl"
```

工具只读数值录制，不加载模型、不打开相机。搜索范围和统计门槛在 `config/filter_tuning.yaml`，`--config` 可指定另一份调参配置；实时检测窗口等固定条件取当前生效配置并写进报告。按浏览器时钟排序，应用当时的 calibration 事件矩阵；未知矩阵坐标、未实测尺寸、全屏未确认、任务中途换矩阵、任务未完成或缺 end 均不能冒充有效调参数据。重复输入同一任务不算三次。不同 A/B 或设备/灰度/相机条件要求分开分析。

输出 `reports/filter_tuning.md`、可核对全部候选/逐点/逐跳结果的 JSON 及有实测数据时的 PNG 取舍图；报告包含输入与算法 SHA256、配置和推荐理由。主抖动使用各目标固定稳定窗口的 x/y 总体标准差合成量（cm 和射线 yaw/pitch 度），另列检测注视段抖动及覆盖率。主延迟以旧、新目标稳定窗口内**修正后未滤波信号的二维中位位置**为共同起点和终点，沿其厘米方向投影至 90%；滤波和未滤波共用端点、阈值和有效帧，从目标切换开始搜索到达，逐跳相减得到额外延迟。稳定终点只用于离线测量，不进入实时滤波。旧「目标坐标→目标坐标」定义保留为 `legacy_target_*` 附加列，不参与主延迟推荐，以免精度偏差混入响应指标。未到达和缺帧单列，负差保留，不补成 0。该值是取帧时间轴上的算法响应差，不包含滤波运行开销、网络及实际屏幕呈现，不称端到端延迟。

`min_signal_jump_snr`（`config/filter_tuning.yaml`，默认 3）控制可辨识跳动：信号端点间厘米距离须不小于该倍率 × 两稳定窗口未滤波位置标准差的 RMS，即 `sqrt((SD_old² + SD_new²)/2)`；零位移也排除。相对噪声过小的跳动单独计数，从延迟分母扣除，但仍参与抖动和检测代理。缺少合格稳定参考/无效跳动不扣分母；没有可测跳动时不能通过覆盖门槛。该倍率是可调整的测量规则，尚未跨用户验证；逐跳端点、距离、噪声及阈值均可在 JSON 核对。

检测漏检代理为未匹配目标切换/可判定切换；误检代理为未匹配眼跳检测/全部被评估检测（FP/(TP+FP)，不是负类 FPR），仅统计可判定区间。目标切换不是眼动仪真值。推荐需满足任务数、有效帧、角度/注视/跳动覆盖和检测代理门槛；从合格帕累托前沿按归一化拐点候选选择。门槛也是可审查的配置，不保证能得到合格推荐。默认不自动改写实时参数。

4. 核对报告各轮结果与输入，记录推荐的 min_cutoff/beta/d_cutoff、角阈值、滤波前后抖动、额外延迟、未到达数、检测代理及覆盖；将推荐值写入 **local**，重启服务，在匹配条件下开启「对比显示」进行主观试用，填写 `验证记录.md`。大纲要求的其他 2–3 人试用仍需真实反馈。

工具退出码：0 表示有合格推荐，或显式 `--pending` 写待测报告；2 表示生成了报告但无合格推荐；1 表示读取/计算失败。当前报告使用 2026-10-20 的三份 Mac B 组录制：18/54 组通过，算法拐点为候选 35（`1.0 / 0.2 / 2.0 / 90`），抖动 1.0179 cm、额外延迟 42.8708 ms。**用户最终选候选 44（`2.0 / 0.05 / 1.0 / 90`）作为默认**：同在合格前沿，抖动 1.0846 cm、额外延迟 28.4294 ms；接受高约 0.067 cm 的抖动，换取低 14.4 ms 的额外延迟，优先后续 M3 的响应速度。用户反馈未滤波点明显更跳、未察觉滤波点滞后；对比受颜色与短轨迹样式差异影响，未做盲测，待多人验证。工具仍不自动写配置，本次按明确取舍更新默认文件，local 覆盖规则保留。报告和验证记录包含详细结果，合成测试结果不写入实测报告。原始录制和个人档案仍放在忽略的 `data/`。

## Python 摄像头管线与测速（单独记录）

这条链路供后续悬浮层使用，当前不接入网页。先在网页点「暂停并关闭摄像头」，并关闭会议软件等占用摄像头的程序。**macOS 首次从后台采集线程打开摄像头时，权限请求可能失败；请先在「系统设置 → 隐私与安全性 → 摄像头」中为运行 Python 的终端授权，再重新运行。**

**Windows**：

```powershell
.\.venv\Scripts\python.exe -m gaze_core.capture --benchmark 60 --no-grayscale
.\.venv\Scripts\python.exe -m gaze_core.capture --benchmark 60 --grayscale
```

**Mac**：

```sh
./.venv/bin/python -m gaze_core.capture --benchmark 60 --no-grayscale
./.venv/bin/python -m gaze_core.capture --benchmark 60 --grayscale
```

这里的 60 秒是建议统一的采样时长，不是已取得的成绩。也可传入其他正秒数，或用 `--config <文件>` 指定配置；省略灰度选项时沿用配置。测速不保存或上传图像，只在终端输出 JSON：

| 字段 | 含义 |
|---|---|
| `measurement_seconds`、`device`、`device_name` | 实际采样时长和实际推理设备；模型、摄像头初始化不在采样窗口内 |
| `requested_resolution`、`actual_resolution`、`grayscale` | 请求分辨率、实际读到的帧尺寸、是否灰度 |
| `requested_backend`、`actual_backend` | 请求和实际摄像头后端 |
| `captured_frames`、`capture_fps` | 采样窗口内成功读帧数量 / 实际采样时长，包含无人脸帧 |
| `predicted_frames`、`valid_predictions`、`prediction_status_counts` | 完成推理的数量、有效结果数量和状态分布；无人脸/眨眼与正常跟踪不能混在一起解释性能 |
| `inference_ms` | 整次引擎调用的耗时，中位数及 P90，包含人脸处理和视线推理 |
| `read_to_result_latency_ms` | 从 **OpenCV `read()` 返回**到结果就绪的延迟，中位数及 P90；包含单槽等待和推理 |
| `dropped_frames` | 新帧覆盖尚未推理旧帧的次数；不是设备或驱动内部丢帧数 |
| `pending_frames`、`in_flight_frames` | 统计截止时尚待推理、正在推理的帧；与覆盖丢帧分列 |

**延迟不含摄像头曝光与驱动缓冲，也不含屏幕显示，不是端到端延迟。** 没有样本时统计值为 `null`；不能把空值当作零耗时。统计在关闭前截取；关闭时等待当前推理完成，之后完成的结果不补入测速报告。

网页「有效更新率」只计算有效响应，Python `capture_fps` 计算成功采集帧；两者也经过不同采集路径。按 [验证记录](验证记录.md) 分为两组保存，不能把 Python FPS 当作网页版本对比的结果。

`CameraPipeline` 的 `start()` / `resume()` 创建采集与推理线程，中间只保留最新待处理帧；`wait_for_result(after_sequence=..., timeout=...)` 获取新结果。`pause()` 停止两条工作线程并释放摄像头，保留已加载的引擎及可选录制会话；`resume()` 重新打开摄像头、创建工作线程并复用引擎和会话（测速统计重新开始，结果序号继续递增）；`close()` 等待工作线程退出、交付最后一个推理结果后关闭录制器及引擎，可重复调用，关闭后不能重启。普通运行只保留最新帧/结果和计数，只有测速显式收集耗时样本；默认不录制，传入 `recorder` 或 `recording_factory(engine, actual_resolution)` 才启用数值录制，两种注入方式不能同时使用。

摄像头只由采集线程在退出循环后的 `finally` 中释放。暂停/关闭只发出停止信号并等待线程退出；推理失败也只发出停止信号，不从控制线程或推理线程调用 `camera.release()`，不靠释放摄像头中断 `read()`。如果读帧或推理没有在 `stop_timeout_s` 内自然返回，会明确报告停止超时；此时不能声称摄像头或引擎已释放，应记录错误，待阻塞调用返回后重试清理。

两台机器都需要连续暂停/恢复 **20 次**。使用 `.venv` 的 Python 交互环境运行下列步骤，在每个暂停/关闭点观察摄像头指示灯，并确认其他应用可重新打开摄像头；逐次记录是否恢复出结果、是否出现崩溃、卡死或超时。其他应用确认完成后应关闭摄像头，再恢复管线：

```python
from gaze_core.capture import CameraPipeline
p = CameraPipeline()
try:
    p.start()
    result = p.wait_for_result(timeout=30)
    if result is None:
        raise RuntimeError("等待首次推理结果超时")
    for attempt in range(1, 21):
        p.pause()
        input(f"第 {attempt}/20 次：确认摄像头已释放，关闭占用它的其他应用后，按回车恢复：")
        result = p.resume().wait_for_result(after_sequence=result.sequence, timeout=30)
        if result is None:
            raise RuntimeError(f"第 {attempt}/20 次恢复后等待结果超时")
        print(f"第 {attempt}/20 次恢复已获得结果，序号 {result.sequence}")
finally:
    p.close()
```

## 数值录制与全屏跳点

在网页开启摄像头，固定灰度、屏幕参数和全屏状态，再打开「录制原始数据」。录制期间灰度和几何参数锁定；屏幕/窗口及全屏字段是开始录制时的快照，正式录制请先固定窗口。界面显示录制状态及当前文件名；录制失败会显示明显且持续的错误提示。每份会话保存在 `data/sessions/<UTC日期时间>-<会话标识>.jsonl`，只记录数值结果和任务事件，**绝不写入摄像头图像、人脸画面、图像字节或 base64**。

同一时间仅一个网页录制会话。手动录制中启动九点校准、独立评估或跳点任务时，任务事件写入现有会话，任务结束后继续手动录制；未手动录制时，任务自动开启会话并在完成或取消后关闭。等待在途帧和事件提交后才写结束记录，不允许嵌套会话。关闭摄像头会结束网页录制；关页时尽力发送目标结束和会话停止请求，浏览器不能保证交付或顺序，强制关闭页面或进程可能无法收尾，回放会明确警告。缺失回执的已完成帧在收尾时保留，`browser_response_ms` 为 `null`。

运行跳点时，先进入「全屏体验」，再点击「全屏跳点任务」；非全屏时拒绝开始，任务中退出全屏会取消。坐标仍以全屏左上 `(0, 0)`、右下 `(1, 1)` 归一化；目标在配置的边距内覆盖四角及四条边，再加入随机目标。默认目标中心位于 `[0.05, 0.95]`，四角锚点为边距位置；每次跳动均满足调整后坐标的最小间距，录制的 `target` 就是实际显示位置的全屏归一化坐标。目标圆无法完整显示时拒绝开始。

跳点期间隐藏任务横幅和取消按钮，只在不会覆盖任何可能目标圆的空白边带显示「Esc 取消」；边带空间不足时连提示也隐藏，Esc 始终可取消。任务只提供后续 M2 分析用的原始流和目标事件，不参与个人校准，不进入独立评估统计或 CSV，也不在本次自动生成抖动、延迟或精度成绩。

JSON Lines 的第一行为 `session`，包含程序版本/提交、系统和设备名、实际推理设备、灰度、`source`（`web` / `python`）、屏幕厘米尺寸及是否实测、相机内参及来源。相机内参来源为 `calibrated` 或 `estimated`，并保存设备文件名、回退原因、实际/标定分辨率、输入内参与实际使用内参、去畸变开关与实际应用状态；每帧也记录实际使用的 `camera`，以覆盖运行中分辨率变化。网页还记录启动时的逻辑屏幕尺寸、`devicePixelRatio`、窗口尺寸、测试区位置及是否全屏。Python 使用设备文件中已填写的逻辑屏幕尺寸，没有的浏览器字段留空，不伪造为实测值。

每个完成推理的结果写一行 `frame`，包括无效结果及其 `status`；有效帧保留未滤波、未裁剪的 `raw`，以及已有的视线起点/方向、头姿、视距、睁眼程度和推理耗时。状态未提供的数值留 `null`。`target` 记录目标出现、切换和结束、任务类型与全屏坐标；校准/评估同时保留测试区原目标坐标，换算不改变原有拟合或评估。`calibration` 记录当前修正参数的应用/清除；`distance_check` 记录每档视距自检的实测值、估计深度中位数、带符号偏差/百分比、有效样本数、采样窗口时间与相机状态；`click` 仅预留格式，本次没有全局输入监听。

写入由后台线程完成，生产者不等待磁盘；每行完整 JSON 后立即 `flush`。有界队列满或磁盘失败时停止录制、报告错误，推理继续。正常结束写 `end`，`written_lines` 包含会话首行、帧/事件及 `end` 自身，另含 `failed_records` 和结束原因。`flush` 保证提交到操作系统，不等同于断电落盘保证；突然中断可能留下截断末行，之前完整行仍可回放。

### 两种来源的时间字段

| 来源 / 字段 | 定义和用途 |
|---|---|
| 网页 `browser_capture_ms` | `drawImage(video, ...)` 取帧后立即读取 `performance.now()`；分析时用它与 `target.browser_time_ms` 对齐 |
| 网页 `target.browser_time_ms` | 目标出现/切换时，取自设置目标位置的 `requestAnimationFrame` 回调内的 `performance.now()`；结束事件取完成/取消处理时刻 |
| 网页 `browser_request_ms` / `browser_response_ms` | 调用帧请求前 / 收到 `fetch` 响应时的同一浏览器单调时钟；响应用单独回执补入录制，不把缺失回执伪造为服务器时间 |
| 网页 `page_clock_id` | 标识同一个页面时钟；刷新页面后变更，不能把不同页面的时间直接合并 |
| 网页 `video_frame` | 支持 `requestVideoFrameCallback` 时保存最近回调的元数据，来源标为 `requestVideoFrameCallback_latest`；不支持时来源为 `drawImage` |
| 网页 `request_received_ns` / `inference_completed_ns` | 本机帧接口进入处理时（图像解码前）/ 完成推理的 `time.perf_counter_ns()` |
| Python `capture_ns` / `inference_completed_ns` | OpenCV `read()` 返回后立即采样 / 完成推理的服务器单调时钟；沿用管线结果时间，记录器不重新生成采集时间 |
| 两种来源 `server_ns` / `seq` | 记录被接纳时的服务器单调时间 / 文件写入序号；用于追溯，不代替网页抓帧或目标显示时间 |

`target` 的出现/切换时间记录的是设置目标位置的回调时刻，实际显示晚于该时间：正常前台刷新时最多相差一个屏幕刷新周期，不能当作实测呈现时刻。浏览器掉帧、卡顿或后台节流时不保证这一上限；不要据此预填显示延迟。[requestAnimationFrame 说明](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame)介绍了回调与后续重绘的关系。

浏览器时间单位为毫秒，Python/服务器时间单位为纳秒；两种时钟没有共同原点，**不能跨时钟直接相减**。`video_frame` 中 `mediaTime`、`processingDuration` 按浏览器 API 约定为秒，其余时间元数据按 API 原单位保留；`captureTime` / `receiveTime` 等字段可能缺失。最近一次回调的元数据可能对应已显示过的另一帧，不能证明与当前 `drawImage` 的像素一一对应，也不能代替真实曝光时间或用来声称端到端延迟。Python 读帧后延迟同样不含曝光和驱动缓冲。

### Python 管线录制与回放

给原测速命令加 `--record` 即可录制 Python 链路，默认仍关闭。首个待推理帧到达后，在独立录制线程建立会话，使用实际帧尺寸和已加载引擎的实际设备写首行；准备期间已推理的数值结果暂存有界队列，不让查提交、打开文件或首次刷新阻塞推理。只记录完成推理的帧，覆盖丢弃的待处理帧不补造记录。暂停保留会话，关闭先等待采集和推理退出，再排空数值队列、等待写入收尾；超时会报错，可重试关闭。

```powershell
# Windows：建议与网页分开运行，不要同时占用摄像头
.\.venv\Scripts\python.exe -m gaze_core.capture --benchmark 300 --record
.\.venv\Scripts\python.exe tools/replay.py "data/sessions/实际文件名.jsonl"
.\.venv\Scripts\python.exe tools/replay.py "data/sessions/实际文件名.jsonl" --rows
```

```sh
# Mac：300 秒为验证要求，不是已测成绩
./.venv/bin/python -m gaze_core.capture --benchmark 300 --record
./.venv/bin/python tools/replay.py "data/sessions/实际文件名.jsonl"
./.venv/bin/python tools/replay.py "data/sessions/实际文件名.jsonl" --rows
```

测速报告中的 `recording` 给出文件、已写行数及错误；录制关闭失败也会报告。测速统计仍以关闭前的采样窗口为准，录制会等待当时正在推理的最后一帧，所以录制帧数可能比测速快照多，核对时分别使用各自的统计口径。

回放无需摄像头或模型，命令行摘要包括同一来源时钟下的时长、帧数、有效率、事件数量/类型及状态分布；时长按录制中可用的同源起止时间计算，不把缺失的浏览器时间换成服务器时间。`--rows` 将每条 JSON 输出到标准输出，摘要和警告写到标准错误。它按时间顺序读取，不模拟实时播放速度。网页按浏览器抓帧/目标时间排序，Python 按读帧单调时间排序，同时间按 `seq` 稳定排序，会话首行与结束行固定在两端。

`tools.replay.read_session(path)` 提供元信息、排序记录、警告和 `summary()`；`iter_records(path)` 遍历全部帧及事件；`replay(path, process=处理函数)` 将每个帧字典送入处理函数并逐个返回结果，供后续接入滤波、校准等逻辑，本次不添加这些算法。回放保留原始时间戳与坐标，不裁剪、不重新拟合。

5 分钟实机录制正常停止后，可在仓库目录进入 Python 交互环境，用下列代码核对完整文件的帧和事件；把文件名换成本机实际文件，保留实际输出。比较按文件序号还原后进行，包含时间戳和坐标在内的所有原字段都必须一致；回放顺序另由 `--rows` 检查。

```python
import json
from pathlib import Path
from tools.replay import read_session
path = Path("data/sessions/实际文件名.jsonl")
original = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
session = read_session(path)
assert not session.warnings, session.warnings
assert session.ended_normally, "会话未正常结束"
for kind in ("frame", "target", "calibration"):
    before = [row for row in original if row["type"] == kind]
    after = sorted((row for row in session.records if row["type"] == kind), key=lambda row: row["seq"])
    assert before == after, f"{kind} 回放与原行不一致"
    print(kind, "核对一致，数量：", len(after))
assert original[-1]["written_lines"] == len(original)
print(session.summary())
```

截断且未换行的最后一行可以安全跳过并提示；中间坏行或完整坏 JSON 会报错。缺少 `end` 时提示「会话未正常结束」，有失败数时也警告；能读出部分文件不代表录制完整。

## 常见启动问题

| 现象 | 处理 |
|---|---|
| 找不到 `py` 或 `python3.12` | 确认已安装 Python 3.12，重新打开终端；Windows 检查 Python Launcher |
| 下载失败或超时 | 检查能否访问 Python 包源、Hugging Face 和 Google 模型来源，保留错误，再执行安装命令；不需要删掉已下载成功的模型 |
| 页面连接失败 | 先启动服务并保持终端打开，确认地址是 `127.0.0.1:8765`，不要直接打开 `static/index.html` |
| 端口已占用 | 检查是否已经启动过同一应用；已有服务可直接使用，不要重复启动 |
| 摄像头打不开 | 在 Chrome 和系统隐私设置中允许相机访问，关闭会议软件后重试；macOS 的 Python 管线首次从后台线程打开摄像头可能因权限请求失败，需先在「系统设置 → 隐私与安全性 → 摄像头」中为终端授权 |
| 模型加载失败 | 保存页面与终端错误，通过测试反馈模板报告；不要把个人路径或摄像头画面贴到公开 Issue |
| 切换灰度、全屏或窗口后没有已校准结果 | 设置或窗口尺寸变化会清除校准。先固定条件，再重新校准 |

## 使用

1. 在 Chrome 中打开本地地址，点击「开启摄像头」，允许摄像头访问。
2. 面向屏幕坐稳，保持约 40–60 cm 距离和正常室内光照。校准和评估前必须点「全屏体验」。
3. 确认设备文件，或填写个人档案设备名；点击「九点校准」，依次看着蓝色圆点，每个点约 2.2 秒，不用点击。丢失或眨眼时会等待有效样本。
4. 校准完成后，绿色注视点表示当前预测位置。测试区外的结果仅用边界虚线点提示方向。
5. 点击「独立评估」，注视另一组目标点；完成后分别查看 A 未个人校准 / B 九点校准的逐帧结果和各点等权汇总，并导出 CSV。退出全屏会取消任务。
6. 可切换灰度输入；输入条件或窗口尺寸变化时，已有校准会清除。点击「暂停并关闭摄像头」停止采集。

厘米统计需要量出屏幕实际宽高，在「屏幕与相机参数」中填写并勾选「尺寸已实测」，同时进入全屏。未实测尺寸时只显示逐帧像素误差，厘米与角度显示不可用。新版不预填屏幕厘米尺寸；缺少宽高时先补全尺寸，视距自检仍可单独使用。

## 已实现

- 浏览器摄像头采集，最长边约 640 像素；图像仅发送给 127.0.0.1 的本机推理服务。
- MediaPipe 人脸关键点、ptgaze ETH-XGaze / ResNet18 预训练视线模型、头姿与视线向量。
- 视线与屏幕平面求交，未个人校准坐标；九点仿射个人修正。
- 彩色 / 灰度（三通道复制）对照、眼睛闭合阈值过滤、无人脸状态。
- 注视点显示、One Euro 显示滤波和眼跳 reset；评估使用未滤波、未裁剪的坐标。
- 全屏独立目标点评估、原逐帧统计与中央/边缘逐点等权统计、三维射线角度及轴向缩放诊断；九点个人档案。
- CSV 包含坐标、标签、时间、灰度状态、校准编号、尺寸信息和质量标记；不含图像。评估标签不会写入校准模型。
- 默认不保存人脸画面，模型权重本地加载；页面没有远程脚本或分析服务。
- 网页手动/任务自动数值录制、目标/校准事件、只在全屏运行的跳点任务；Python 管线可选录制；离线 JSON Lines 顺序回放和摘要。

## 当前限制

- 这是可运行的工程基线。已接入棋盘格设备标定；没有设备文件或分辨率长宽比不匹配时仍使用估计焦距。屏幕相对相机的旋转暂按现有平面模型处理；真实标定、视距与精度仍待双机验证。
- 默认单屏、内置摄像头，现支持摄像头水平和垂直偏移。不支持多屏精确映射或桌面覆盖。窗口模式的系统边框偏移为近似值，正式测量请用全屏。
- 视线网络为 11,177,538 参数，尚未达到最终 <5M 的轻量化目标；完整 FLOPs 未测量。
- 面部尺度来自通用人脸模型，视距为估计值。眨眼过滤为眼部纵横比启发式，并非经过专项验证的眨眼分类器。
- 页面「单帧处理」指本机检测和推理时间，不代表摄像头采集到屏幕显示的完整延迟。「有效更新率」为最近 2 秒有效响应数量 / 2。
- 目标点是指令注视标签，不是眼动仪真值。个人精度需用户在正确坐姿下完成校准和独立评估。
- 当前模型与合成几何 / 校准计算已验证；尚未完成多用户盲测、10 分钟坐定测试或一周自适应实验。

## 运行环境与来源

- v0.1.0 开发记录：macOS / Apple M5 Pro，Python 3.12，CPU 推理；各次 M0 实测状态见验证记录。
- 新安装环境位于当前仓库 `.venv/`。原开发机的 macOS 启动入口兼容已有环境；新电脑无需该目录结构。
- ptgaze 源码：<https://github.com/hysts/pytorch_mpiigaze_demo>，提交 `7993db777ac0fae4f976213bfc4b1b8741ba164d`，MIT，许可证见 `THIRD_PARTY_LICENSE.txt`。
- 视线权重：<https://huggingface.co/hysts/ptgaze-eth-xgaze-resnet18>。
- 关键点权重：<https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task>。
- `requirements.txt` 是默认安装入口，`requirements-runtime.txt` 是公共依赖，`requirements-cu130.txt` 是 Windows CUDA 13.0 配对，`requirements-dev.txt` 是 HTTP 测试的额外依赖；`requirements-lock.txt` 是原开发机完整安装快照，仅供追溯，不用于跨平台安装。`model-sha256.txt` 记录权重摘要；`vendor/NOTICE.md` 记录上游源码版本和精简安装方式。

原始 ptgaze 许可证保留在 `THIRD_PARTY_LICENSE.txt` 和 `vendor/ptgaze/LICENSE`。模型来源与版本见 [模型说明](models/README.md)。测试数据默认不提交 Git；仓库不包含同学的人脸图像、原始评估文件或私人项目文档。

## 验证

```sh
./.venv/bin/python -m pip install -r requirements-dev.txt
./.venv/bin/python -m unittest discover -s tests -v
node tests/test_math.mjs
node tests/test_recording.mjs
node tests/test_jump.mjs
node tests/test_distance_check.mjs
node tests/test_evaluation.mjs
```

Windows 将前两行的 Python 路径换成 `.\.venv\Scripts\python.exe`。Node.js 只用于开发者前端测试，同学使用应用不需要安装 Node.js。

包含屏幕中心/左右方向、错误射线、屏外坐标保留、独立点上的仿射恢复和退化校准拒绝。v0.1.0 中曾使用上游仓库的公开示例图片验证彩色、灰度推理，以及黑帧无人脸返回；这些测试不代表用户的真实注视精度。

M0a 新增设备/配置、视频时间戳、假摄像头/假引擎线程生命周期、测速统计、安装配置重试和同步 HTTP 接口回归测试。生命周期测试包含约 30 ms 的阻塞读帧、仅采集线程在读帧结束后释放摄像头、连续 20 次 start/pause，以及多次暂停/恢复仅创建一次引擎。自动化测试不打开真实摄像头、不需要 GPU 或模型权重；不代表两台机器的真实推理、性能或精度验收。

M0b 增加数值录制/回放一致性、禁止图像/base64、后台写入与失败路径、末行截断和缺少结束记录，以及网页时间采样、会话复用、跳点覆盖/最小间距的测试。实机请按 [验证记录](验证记录.md) 分别填写网页链路和 Python 管线记录：两台机器各录 5 分钟网页数据并包含一次跳点任务，再回放核对帧数、时间戳、坐标、事件和结束记录；未取得结果前保持「未验证」。

M1a 增加设备文件读取、内参比例换算与长宽比回退、偏移符号、合成棋盘格标定、去畸变矩阵/缓存/预览点映射、视距采样和录制回放测试；包含 NumPy 数值。假页面检查新增来源列只追加在 CSV 末尾，以及自检复用手动会话、完成/取消和样本不足。自动化数据为合成输入，不能当作任何设备的标定误差或个人精度。

M2 新增 One Euro/真实时间间隔、射线角速度与坐标回退、注视段、离线零相位、网页/Python 取帧时钟、对比显示、原始数据隔离、档案条件检查和调参统计/帕累托/报告测试。绘图测试只使用临时合成输入；完整测试依赖由 requirements-dev.txt 安装，实机调参状态见验证记录和 reports/filter_tuning.md。
