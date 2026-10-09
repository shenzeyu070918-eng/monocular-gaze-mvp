# 模型文件

首次安装时由 `download_models.py` 从原始发布地址下载两个模型，共约 47 MB，并检查 SHA-256；权重不提交到 Git。

- `eth-xgaze.safetensors`：hysts 的 [ETH-XGaze / ResNet18](https://huggingface.co/hysts/ptgaze-eth-xgaze-resnet18)，固定版本 `5b6aebd02c7a612b50afb2e48c453f23533b8de3`。
- `face_landmarker.task`：Google 的 [Face Landmarker 模型](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task)，float16，第 1 版。

来源链接和摘要见 `download_models.py` 与根目录 `model-sha256.txt`。下载后运行不需要模型服务或账号。
