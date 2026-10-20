# 视线实验台 — 单目摄像头笔记本 MVP

普通笔记本摄像头 → 本机三维视线估计 → 应用内注视点 → 九点个人校准 → 独立评估与 CSV 导出。首个测试版本 **v0.1.0** 是同学试用基线；当前开发分支为 **M1a（设备标定、相机几何与视距自检）**，尚未完成 M0 双机验收或项目精度验收。

**这是下载后在各自电脑运行的应用。GitHub 仓库页面不直接运行眼动模型；无需把摄像头画面传到云端。**

- [测试操作与记录指南](docs/测试操作与记录指南.md)
- [可复制填写的测试记录模板](docs/测试记录模板.md)
- [版本记录](CHANGELOG.md) · [已完成的验证](验证记录.md)

![界面预览，不含摄像头画面](界面预览.jpg)

## 首次安装

推荐 Python **3.12（64 位）**、Chrome、内置摄像头与单块笔记本屏幕。需要联网下载 Python 依赖及约 47 MB 模型；PyTorch 等依赖体积较大，请预留数 GB 空间。安装完成后，推理与画面处理在本机进行。v0.1.0 曾在 macOS M5 Pro 验证；新版在 Windows 和 Mac 的真实摄像头全流程仍待本次实机验证。Linux 暂不作为本轮支持目标。

1. 验证基线时，从 Releases 下载 **v0.1.0** 的 Source code ZIP，或切到 `v0.1.0` 标签。验证 M1a 时使用对应 PR 的 `m1a-device` 分支；合并后使用 `main`。两个版本分别解压/检出，环境不要混用。
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

`gaze_core/` 提供引擎、几何计算、数值录制和 Python 摄像头管线，不依赖 FastAPI 或网页。引擎与几何模块已从仓库根目录迁入。`app.py` 从公共包导入；网页仍是浏览器采集、同步逐帧请求 `/api/frame`，M1a 接入设备文件、相机去畸变与视距自检，保留现有个人校准/评估算法及显示平滑。CSV 原有 26 列名与顺序保留，仅在末尾追加相机来源与去畸变字段。评估点、按目标点统计与个人档案留待 M1b。

默认配置为 `config/default.yaml`：

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

工具可同时接收这些实测参数；也可之后手动填写设备 YAML，补全逻辑分辨率与实测尺寸。水平、垂直偏移分别量摄像头镜头中心相对可视区域上沿中点的位置，不用屏幕外壳顶边。仓库只提供字段说明，没有预填的双机标定值。

标定默认只在内存中保留角点，不保存采集图像。只有显式传入 `--save-images data/calibration/my-laptop` 才会把本机图像写入被 Git 忽略的 `data/`；拒绝目录越界和符号链接越界。会话录制始终不接收图像或 base64。Windows 可按需要加 `--backend dshow`；Mac 保持 `auto`。macOS 若权限失败，请先在「系统设置 → 隐私与安全性 → 摄像头」为实际使用的终端授权，退出占用摄像头的程序后重试。

内参使用规则：

- 实际与标定分辨率相同：使用标定内参。长宽比相同但尺寸不同：等比例缩放 K，提示已缩放；D 保持不变。这遵循 [OpenCV 相机标定文档](https://docs.opencv.org/4.10.0/d9/d0c/group__calib3d.html) 的缩放与去畸变约定。
- **长宽比不同：拒用该标定内参，改用估计 K 和零 D**。状态、录制会话/帧和 CSV 都标记 `intrinsics_source=estimated`、`intrinsics_reason=aspect_ratio_mismatch`，仍保留设备文件名。未选择文件时原因是 `no_device_file`。相同比例也不能保证视场相同；驱动模式、裁切或镜头改变应重新标定。
- `camera.undistort: true` 默认开启；`undistort_alpha` 控制 OpenCV 新矩阵，默认 0。映射按相机分辨率预计算并缓存，每帧只 remap；不再裁切 ROI。图像去畸变后，姿态与视线归一化统一使用对应新 K 和零 D，预览关键点映射回原始视频。未使用标定内参时不执行无意义的零畸变映射，明确记录「已开启、未应用」。
- 关闭去畸变用于对照：保留输入 K/D，姿态求解使用对应 D；视线图像归一化仍面对未矫正的画面，因此不能把关闭状态当作完全校正的链路。更改配置后重启。CSV 末尾追加 `intrinsics_source,device_file,intrinsics_reason,undistort_enabled,undistort_applied,intrinsics_scaled,camera_width_px,camera_height_px`；`geometry_estimated` 随实际内参来源更新，不代表全部物理参数已实测。

网页「视距自检」依次引导实测 **40 / 50 / 60 cm**。用尺子量镜头到眼睛/鼻梁附近面部中心的前后距离，保持正对相机；估计值是模型面部中心在相机坐标中的 **Z 深度**，并非侧坐时的欧氏直线距离。每档先量好再点击采样，配置 `distance_check` 控制稳定等待、采样时长、最低有效样本数与偏差提示阈值。显示估计中位数及 `估计 − 实测` 的带符号偏差；样本不足时记录空估计并提示重试，不补值。

自检复用正在进行的手动录制；否则自动开启单一会话，完成或取消后关闭，不嵌套。结果写 `distance_check`，`sample_start_ms` / `sample_end_ms` 来自有效帧的浏览器取帧时钟，`browser_time_ms` 是结果处理时刻；服务器另记单调时钟。自检不参与九点拟合或评估统计。相机标定前后 A 组对照及双机待测记录见 `验证记录.md`；评估边缘覆盖、按目标点统计与个人档案属于后续 M1b。

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
2. 面向屏幕坐稳，保持约 40–60 cm 距离和正常室内光照。建议点「全屏体验」。
3. 点击「九点校准」，依次看着蓝色圆点，每个点约 2.2 秒，不用点击。丢失或眨眼时会等待有效样本。
4. 校准完成后，绿色注视点表示当前预测位置。测试区外的结果仅用橙色边界点提示方向。
5. 点击「独立评估」，注视另一组目标点；完成后查看未校准 / 已校准两列结果，并导出 CSV。
6. 可切换灰度输入；输入条件或窗口尺寸变化时，已有校准会清除。点击「暂停并关闭摄像头」停止采集。

厘米统计需要量出屏幕实际宽高，在「屏幕与相机参数」中填写并勾选「尺寸已实测」，同时进入全屏。否则只显示像素误差。新版不预填屏幕厘米尺寸；缺少宽高时先补全尺寸，视距自检仍可单独使用。

## 已实现

- 浏览器摄像头采集，最长边约 640 像素；图像仅发送给 127.0.0.1 的本机推理服务。
- MediaPipe 人脸关键点、ptgaze ETH-XGaze / ResNet18 预训练视线模型、头姿与视线向量。
- 视线与屏幕平面求交，未个人校准坐标；九点仿射个人修正。
- 彩色 / 灰度（三通道复制）对照、眼睛闭合阈值过滤、无人脸状态。
- 注视点显示、轻度轨迹平滑；评估使用未平滑、未裁剪的坐标。
- 随机独立目标点评估、平均 / 中位 / P90 误差、有效跟踪率；厘米统计可用时显示两个阈值达标率。
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
```

Windows 将前两行的 Python 路径换成 `.\.venv\Scripts\python.exe`。Node.js 只用于开发者前端测试，同学使用应用不需要安装 Node.js。

包含屏幕中心/左右方向、错误射线、屏外坐标保留、独立点上的仿射恢复和退化校准拒绝。v0.1.0 中曾使用上游仓库的公开示例图片验证彩色、灰度推理，以及黑帧无人脸返回；这些测试不代表用户的真实注视精度。

M0a 新增设备/配置、视频时间戳、假摄像头/假引擎线程生命周期、测速统计、安装配置重试和同步 HTTP 接口回归测试。生命周期测试包含约 30 ms 的阻塞读帧、仅采集线程在读帧结束后释放摄像头、连续 20 次 start/pause，以及多次暂停/恢复仅创建一次引擎。自动化测试不打开真实摄像头、不需要 GPU 或模型权重；不代表两台机器的真实推理、性能或精度验收。

M0b 增加数值录制/回放一致性、禁止图像/base64、后台写入与失败路径、末行截断和缺少结束记录，以及网页时间采样、会话复用、跳点覆盖/最小间距的测试。实机请按 [验证记录](验证记录.md) 分别填写网页链路和 Python 管线记录：两台机器各录 5 分钟网页数据并包含一次跳点任务，再回放核对帧数、时间戳、坐标、事件和结束记录；未取得结果前保持「未验证」。

M1a 增加设备文件读取、内参比例换算与长宽比回退、偏移符号、合成棋盘格标定、去畸变矩阵/缓存/预览点映射、视距采样和录制回放测试；包含 NumPy 数值。假页面检查新增来源列只追加在 CSV 末尾，以及自检复用手动会话、完成/取消和样本不足。自动化数据为合成输入，不能当作任何设备的标定误差或个人精度。
