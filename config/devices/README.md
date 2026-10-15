# 设备文件

用 `tools/calibrate_camera.py` 生成 `<设备名>.yaml`，再在 `config/default.yaml` 中设置 `device_file: devices/<设备名>.yaml`。没有实测的两台机器参数不预填、不伪造；`data/` 里的采集图像和会话不提交。

schema_version 为 1，字段由工具生成并校验：

- `name`：设备名。
- `camera.matrix`：3×3 OpenCV K；`distortion`：5 项 k1/k2/p1/p2/k3；`resolution`：实际标定宽高像素；`reprojection_error_px`：整体 RMS；`calibrated_at`：ISO 日期时间。
- `screen.width_cm` / `height_cm`：可视区域实测厘米尺寸，可暂为空；`measured`：是否实测；`resolution`：系统逻辑宽高，可暂为空，真实使用前补全。
- `camera_offset_cm.horizontal`：用户面对屏幕时，摄像头中心在可视区域中线右侧为正（cm）。
- `camera_offset_cm.vertical`：摄像头中心在可视区域上沿上方为正（cm），与 `top_cm` 一致。

生成的 YAML 也含上述方向注释。不要把估计值标作实测。没有尺寸时仅能估计视距，不能产生屏幕坐标；未勾选实测时不会输出厘米误差。逻辑分辨率与相机像素分辨率不能混用。

完整操作、分辨率匹配与去畸变开关见仓库 README。
