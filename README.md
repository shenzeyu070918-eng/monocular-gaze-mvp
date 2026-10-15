# 视线实验台 — 单目摄像头笔记本 MVP

普通笔记本摄像头 → 本机三维视线估计 → 应用内注视点 → 九点个人校准 → 独立评估与 CSV 导出。首个测试版本 **v0.1.0** 是同学试用基线；当前开发分支为 **M0a（仅 M0 任务 1–4）**，尚未完成项目精度验收。

**这是下载后在各自电脑运行的应用。GitHub 仓库页面不直接运行眼动模型；无需把摄像头画面传到云端。**

- [测试操作与记录指南](docs/测试操作与记录指南.md)
- [可复制填写的测试记录模板](docs/测试记录模板.md)
- [版本记录](CHANGELOG.md) · [已完成的验证](验证记录.md)

![界面预览，不含摄像头画面](界面预览.jpg)

## 首次安装

推荐 Python **3.12（64 位）**、Chrome、内置摄像头与单块笔记本屏幕。需要联网下载 Python 依赖及约 47 MB 模型；PyTorch 等依赖体积较大，请预留数 GB 空间。安装完成后，推理与画面处理在本机进行。v0.1.0 曾在 macOS M5 Pro 验证；M0a 在 Windows 和 Mac 的真实摄像头全流程仍待本次实机验证。Linux 暂不作为本轮支持目标。

1. 验证基线时，从 Releases 下载 **v0.1.0** 的 Source code ZIP，或切到 `v0.1.0` 标签。验证 M0a 时使用对应 PR 的 `m0a-core` 分支；合并后使用 `main`。两个版本分别解压/检出，环境不要混用。
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

在 M0a 目录中运行（首次安装和更新已有环境都使用此命令）：

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

## M0a 配置与公共核心

`gaze_core/` 提供引擎、几何计算和 Python 摄像头管线，不依赖 FastAPI 或网页。引擎与几何模块已从仓库根目录迁入。`app.py` 从公共包导入；网页仍是浏览器采集、同步逐帧请求 `/api/frame`，`static/`、显示平滑和 CSV 格式保持与 v0.1.0 一致。

默认配置为 `config/default.yaml`：

- `inference.device`：`auto`（默认，有 CUDA 时使用 GPU，否则 CPU）、`cpu` 或 `cuda`。显式指定 `cuda` 但不可用时报告错误。按本轮约定，Mac 使用 CPU，不自动选择 MPS。
- `camera.index`、`width`、`height`：Python 管线的摄像头编号和请求尺寸，默认请求 **640×480**，与网页请求一致。设备可能返回不同尺寸，以测速报告中的实际帧尺寸为准。
- `camera.grayscale`：Python 推理是否使用与网页相同的三通道灰度转换。网页继续使用原有灰度开关。
- `camera.backend`：默认 `auto`。Windows 自动后端打不开、返回黑帧或持续读帧阻塞时，可退出程序后改为 `dshow`（DirectShow）再试，并记录 `actual_backend`。它是兼容性选项，不保证提高帧率或减少延迟；macOS 保持 `auto`。
- `camera.startup_timeout_s`、`stop_timeout_s`：资源初始化和停止等待时间。若后端/驱动阻塞导致线程无法退出，会明确报错，不能把这次释放视为成功；可检查权限、占用程序和后端。
- `inference` 其余项和 `geometry`：保留 v0.1.0 的默认数值。默认屏幕尺寸只是估计，不能据此报告个人厘米精度。

视频模式使用严格递增的单调时钟毫秒。ptgaze 的这一处本地适配已记入 `vendor/NOTICE.md`；每次更新源码后需要重跑安装程序，使环境使用更新后的适配。

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

`CameraPipeline` 的 `start()` / `resume()` 创建采集与推理线程，中间只保留最新待处理帧；`wait_for_result(after_sequence=..., timeout=...)` 获取新结果。`pause()` 停止两条工作线程并释放摄像头，保留已加载的引擎；`resume()` 重新打开摄像头、创建工作线程并复用该引擎（统计重新开始，结果序号继续递增）；`close()` 等待工作线程退出后才关闭引擎，可重复调用，关闭后不能重启。普通运行只保留最新帧/结果和计数，只有测速显式收集耗时样本，不写会话文件。

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

本轮没有录制、回放、录制开关或跳点任务，也没有加入新的显示滤波。

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

厘米统计需要量出屏幕实际宽高，在「屏幕与相机参数」中填写并勾选「尺寸已实测」，同时进入全屏。否则只显示像素误差。默认 30.2 × 19.6 cm 仅供运行，不能代替测量。

## 已实现

- 浏览器摄像头采集，最长边约 640 像素；图像仅发送给 127.0.0.1 的本机推理服务。
- MediaPipe 人脸关键点、ptgaze ETH-XGaze / ResNet18 预训练视线模型、头姿与视线向量。
- 视线与屏幕平面求交，未个人校准坐标；九点仿射个人修正。
- 彩色 / 灰度（三通道复制）对照、眼睛闭合阈值过滤、无人脸状态。
- 注视点显示、轻度轨迹平滑；评估使用未平滑、未裁剪的坐标。
- 随机独立目标点评估、平均 / 中位 / P90 误差、有效跟踪率；厘米统计可用时显示两个阈值达标率。
- CSV 包含坐标、标签、时间、灰度状态、校准编号、尺寸信息和质量标记；不含图像。评估标签不会写入校准模型。
- 默认不保存人脸画面，模型权重本地加载；页面没有远程脚本或分析服务。

## 当前限制

- 这是可运行的工程基线。相机内参使用可调的近似焦距，屏幕相对相机的旋转暂按共面处理；未实施棋盘格设备标定。所有未个人校准结果均明确标记为估计几何。
- 默认单屏、内置摄像头、摄像头位于屏幕水平中央。不支持多屏精确映射或桌面覆盖。窗口模式的系统边框偏移为近似值，正式测量请用全屏。
- 视线网络为 11,177,538 参数，尚未达到最终 <5M 的轻量化目标；完整 FLOPs 未测量。
- 面部尺度来自通用人脸模型，视距为估计值。眨眼过滤为眼部纵横比启发式，并非经过专项验证的眨眼分类器。
- 页面「单帧处理」指本机检测和推理时间，不代表摄像头采集到屏幕显示的完整延迟。「有效更新率」为最近 2 秒有效响应数量 / 2。
- 目标点是指令注视标签，不是眼动仪真值。个人精度需用户在正确坐姿下完成校准和独立评估。
- 当前模型与合成几何 / 校准计算已验证；尚未完成多用户盲测、10 分钟坐定测试或一周自适应实验。

## 运行环境与来源

- v0.1.0 开发记录：macOS / Apple M5 Pro，Python 3.12，CPU 推理；本次 M0a 实测状态见验证记录。
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
```

Windows 将前两行的 Python 路径换成 `.\.venv\Scripts\python.exe`。Node.js 只用于开发者数学测试，同学使用应用不需要安装 Node.js。

包含屏幕中心/左右方向、错误射线、屏外坐标保留、独立点上的仿射恢复和退化校准拒绝。v0.1.0 中曾使用上游仓库的公开示例图片验证彩色、灰度推理，以及黑帧无人脸返回；这些测试不代表用户的真实注视精度。

M0a 新增设备/配置、视频时间戳、假摄像头/假引擎线程生命周期、测速统计、安装配置重试和同步 HTTP 接口回归测试。生命周期测试包含约 30 ms 的阻塞读帧、仅采集线程在读帧结束后释放摄像头、连续 20 次 start/pause，以及多次暂停/恢复仅创建一次引擎。自动化测试不打开真实摄像头、不需要 GPU 或模型权重；不代表两台机器的真实推理、性能或精度验收。
