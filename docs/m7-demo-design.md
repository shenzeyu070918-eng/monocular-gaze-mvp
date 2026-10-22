# M7 展示基础：渲染与内置屏投影适配

基线main `df2c80b`，分支`m7-demo-ready`。只做用户批准的热图显示、屏幕适配及Windows脚本；不实现参考大纲的演示模式、截屏/OCR/AI或后续里程碑。数值采集、校准/核对算法、热图累加/核尺度、点击筛选/拟合与网页流程保持原口径。

## 渲染

`gaze_core/heatmap_render.py::render_heatmap`是实时与PNG共同入口：非负浮点网格 → `cv2.resize(INTER_CUBIC)`到逻辑屏宽高各乘`render_scale` → 限制到原网格[0,max]以防三次插值过冲 → 归一化、阈值、smoothstep、暖色 → 有序透明度抖动 → 8位RGBA。缩放与配色是显示计算，不写回累计网格、CSV或区域统计。导出图例仍遵循同一阈值/色阶。

配置默认`render_scale: 0.5`、`dither_strength: 0.5`。后者单位为alpha量化级峰峰值，不是0–1不透明度：固定8×8 Bayer图案，`((B+0.5)/64-0.5)*strength`加到0–255 alpha再量化；默认扰动幅度不足±0.25/255，0关闭，范围[0,1]。阈值以下仍精确透明、最大不透明度不超过0.4。固定相位，不引入时间随机闪烁。初始强度在生成对照中未见明显噪点，不替代用户桌面/投影观感验收。

查看期间默认1Hz在`gaze-heatmap`线程计算；QImage预乘与所有权转换也在该线程。主线程只接收隐式共享QImage，版本/标记不变不更新。隐藏时不做快照/插值/配色/QImage转换，不增加热图重绘；后台原有累加/断帧检查继续，主动导出仍按需计算。逐次渲染耗时写`data/performance/*-heatmap-render.jsonl`，文件首次渲染才创建，只存单调时间、耗时、尺寸和参数。导出耗时另写regions.json，单次离线耗时不代表Qt/WindowServer性能。

## 屏幕与生命周期

`overlay/screens.py`管理纯逻辑状态转换与Qt拓扑事件合并，macOS/Windows原生实现分文件。只在启动/拓扑事件检查，不在16ms tick枚举屏幕；默认300ms合并、最多额外3次重试，用于原生与Qt列表短暂不同步。参数在overlay配置。内置QScreen移除时立即隐藏，防止Qt将可见圆环迁到外接主屏；几何不变重接后恢复原可见状态。

- macOS：在线物理显示器中必须有唯一、未休眠的`CGDisplayIsBuiltin`；沿`CGDisplayMirrorsDisplay`找到镜像源。用AppKit源屏的名称、完整逻辑几何/DPR交叉对应Qt屏幕，不用外接主屏顺序猜测。通过已验证内屏的厂商/型号/序列标识保持进程内身份；不依赖当前PyObjC未导出的UUID创建函数。串号为0时仍要求唯一物理内屏及唯一几何对应，不能放松为任意同型号屏幕。Cocoa到Qt的y翻转取整个桌面的主屏参考，不误用内屏自身高度。
- Windows：沿用WMI连接类型确定内屏设备接口，再以`EnumDisplayDevicesW(EDD_GET_DEVICE_INTERFACE_NAME)`对应GDI/Qt名称；WMI身份进程内缓存，活动接口及Qt选择实时检查。鼠标物理矩形用该GDI名的`GetMonitorInfoW`选取，不以分辨率或列表顺序选主屏。没有唯一映射就拒绝。

| 情况 | 行为 |
|---|---|
| 单内屏 | 正常启动并匹配档案 |
| 镜像 | 允许；Qt可能只提供镜像源，仍先确认物理内屏属于该镜像组；投影显示其镜像 |
| 扩展 | 只绑定内屏；外接主屏不会成为追踪屏，外屏点击仍丢弃 |
| 内屏不可用/合盖 | 启动拒绝并说明；运行中暂停/隐藏，恢复内屏后重新核对，不降级外屏 |

拓扑变化仅比较内屏身份、逻辑x/y/宽高/DPR。外屏数量变化而上述不变，继续原采集、校准及统计；只更换Qt对象或桌面翻转参考时刷新窗口/点击适配，不重新拟合。几何变化则停止采集/监听，取消原任务、结束旧录制；等待采集线程按原finally协议释放相机后，更新窗口/目标QScreen/录制元信息/档案条件，撤下旧B/C并清空旧热图与旧点击样本。提示「屏幕设置已变化，请重新九点校准」，用户可恢复摄像头并在同一进程重做九点。原采样和拟合计算不改，取消不保存半成品。

## 对照与验收

`tools/compare_heatmap_render.py`输出合成网格与指定录制最后完整核对的三版本对照：旧8位RGBA后放大、新浮点先放大不抖动、新浮点先放大并抖动。同一数据/背景/局部、4倍最近邻放大，明确展示像素，不混入屏幕内容；manifest记录输入/网格哈希和选中时段。只写data/heatmaps/新子目录，PNG/个人manifest不进Git。

沿用M6性能预算作为待验目标：主线程tick中位≤0.35ms、P90≤0.8ms，不是新实测。Mac按相同条件分别后台不查看/查看1Hz，记录Python和WindowServer CPU/GPU、采样时长、实际fps、屏幕/DPR、逐次render及主线程tick/update/paint中位/P90。无法取得的GPU写不可用；未查看时渲染日志不增加、paint未调用，不填假0。镜像与扩展真实连接、热插拔、合盖、九点+独立核对、教师/用户观感及Windows全部单列待测，不用模拟或无摄像头诊断代替。

原生依据：[Apple内置屏标记](https://developer.apple.com/documentation/coregraphics/cgdisplayisbuiltin(_:))、[镜像时active含义](https://developer.apple.com/documentation/coregraphics/cgdisplayisactive(_:))、[Qt Cocoa显示器处理源码](https://github.com/qt/qtbase/blob/6.10/src/plugins/platforms/cocoa/qcocoascreen.mm)、[Microsoft EnumDisplayDevicesW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumdisplaydevicesw)、[OpenCV resize](https://docs.opencv.org/4.x/da/d54/group__imgproc__transform.html)。
