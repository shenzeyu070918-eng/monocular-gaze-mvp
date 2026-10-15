# ptgaze 来源记录

`ptgaze/` 内的 Python 包、数据配置、pyproject.toml、README 和 LICENSE 来源于：

- https://github.com/hysts/pytorch_mpiigaze_demo
- 提交：`7993db777ac0fae4f976213bfc4b1b8741ba164d`
- 包版本：0.3.0
- 许可证：MIT，原始文本保留在 `ptgaze/LICENSE`。

仅打包安装所需文件；未包含上游 Git 历史、图片、测试和开发工具。M0a 对 `ptgaze/head_pose_estimation/face_landmark_estimator.py` 作一处本地适配：视频时间戳改为 `time.perf_counter_ns()` 派生的严格递增毫秒，保留丢帧与暂停的实际时间间隔，替代每帧固定加 33 ms。其余源码保持原样；原始许可证保留。

安装程序用 `--no-deps` 安装此固定源码，再由项目 `requirements.txt` 提供当前 MediaPipe + ETH-XGaze 分支所需依赖；不安装未使用的 face-alignment / dlib 后端。MediaPipe 所需 OpenCV contrib 已覆盖当前代码的 cv2 功能，因此不同时安装 opencv-python。这一精简安装仅用于本 MVP；不承诺上游其他入口可用，`pip check` 也可能提示上游未使用的依赖缺失。
