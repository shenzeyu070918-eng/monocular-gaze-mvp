# 04 · 工具与脚本

工具保留在原位置，本目录只建索引，不复制第二套实现。在仓库根目录用项目虚拟环境Python运行，参数以各工具`--help`及[使用手册](../../docs/使用手册.md)为准。

| 工具 | 用途/数据范围 |
| --- | --- |
| [setup_mvp.py](../../setup_mvp.py) | Python3.12依赖/本地ptgaze/公开模型安装；`--overlay`安装桌面依赖 |
| [list_cameras.py](../../tools/list_cameras.py) | 本机相机编号、分辨率、亮度，需访问摄像头 |
| [calibrate_camera.py](../../tools/calibrate_camera.py) | 棋盘格内参标定，只默认保存参数 |
| [capture.py](../../gaze_core/capture.py) | `python -m gaze_core.capture --benchmark 秒数`，读帧后延迟非曝光端到端 |
| [replay.py](../../tools/replay.py) | JSONL数值回放与完整性提示 |
| [tune_filter.py](../../tools/tune_filter.py) | M2跳点录制调参，报告来自真实输入，不预填数字 |
| [compare_inference.py](../../tools/compare_inference.py) | 同输入CPU/MPS与线程实测；设备/分辨率异常停止 |
| [implicit_calib_experiment.py](../../tools/implicit_calib_experiment.py) | M4只读离线实验，公共测试集/预登记/探索分开 |
| [replay_autocal.py](../../tools/replay_autocal.py) | M5按采集时钟回放及模拟15fps对比 |
| [session_heatmap.py](../../tools/session_heatmap.py) | Python核对录制热图/区域统计，仅data下导出 |
| [compare_heatmap_render.py](../../tools/compare_heatmap_render.py) | 热图渲染对照，图像本地留存 |
| [preview_galaxy.py](../../tools/preview_galaxy.py) | 无摄像头本地星系预览，不连接实时桥 |
| [render_planet_check.py](../../tools/render_planet_check.py) | 合成WebGL取景/交互截图检查，不替代硬件FPS测试 |
| [attention_session.py](../../tools/attention_session.py) | M9本地重新绑定/OCR/统计，派生目录不覆盖原始输入 |
| [check_attention_windows.py](../../tools/check_attention_windows.py) | Mac受控底图排除自窗口像素检查，不打开相机 |

M10报告工具尚在暂停的PR #17中，不列成main可运行工具。个人输入与输出保留data/或已有忽略的实验报告目录；不要把参数中的个人路径、屏幕内容、密钥提交到Issue。
