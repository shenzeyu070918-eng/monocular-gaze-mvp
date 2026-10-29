# M9：本地屏幕注意力采集

批准范围为 ROADMAP 第十五节；预注册标准在实现前的提交 `9f9b655` 中锁定于 [m9-acceptance.md](m9-acceptance.md)。本里程碑不生成报告界面，不做上传、逐行/逐字分析或新校准算法。

## 数据流与隔离

`CameraPipeline 数值结果 → 有界队列 → 九点修正 → M2 I-VT → 注视段`

`Mac ScreenCaptureKit → 内存缩略图 → 分块场景检测 → 原子 PNG + scenes.jsonl`

`已保存场景 + 注视段 → 单调时钟区间连接 → 本地 Vision 子进程 → 块级 AOI → 硬/高斯软归属 → render_heatmap / JSON / CSV`

会话结束后才启动 OCR；进程通过 Pipe 只传路径和识别结果，超时或删除时 terminate/join，不阻塞推理或 Qt。各阶段使用自己的参数快照；旧数值录制白名单、CSV、九点/核对、点击和 M6 逻辑不变。

`ScreenCaptureBackend` / `OCRBackend` 接口在 contracts.py。平台截屏注入 AttentionSession，核心不依赖 Qt。Mac 适配器仅在主动开始时导入 ScreenCaptureKit；Vision 按需加载。Windows 本轮明确不支持；后续 Windows.Graphics.Capture + WDA_EXCLUDEFROMCAPTURE，本地 OCR 可接 PaddleOCR。

## 场景与时钟

默认2Hz按采样开始时间定节奏，不能把截图耗时再加成固定等待。系统指针不进图、不采音频。缩略图分块阈值、小面积忽略、重复局部区域忽略和最大不稳定时间均在 attention 配置。跨越大部分页面的稀疏文字滚动不作为“小动画”屏蔽。

稳定确认后，回溯连续与最终画面一致的首次样本，场景从该样本开始。不确定区间只留最后旧样本至首次新样本；暂停或结束时尚未确认的变化也写 transition_pending。前台切换的在途帧丢弃，相关保护区间纳入不确定总时长。跨边界注视拆分，不能把中间过渡整段归给旧截图。

超过 max_unstable_ms 的场景标 dynamic=true，以首个变化样本开始代表该动态片段；截图是该片段的一个快照，不代表每一时刻内容相同。局部动态掩码比例另记；M10 不能从动态快照推出细粒度内容。不同应用的待确认片段不混合成同一场景。

所有视线仍用 OpenCV read 返回时的 capture_ns。截图存 request_ns / received_ns，midpoint 只是采样约定，不保证 OS 真正呈现时刻被这一区间严格夹住；timestamp_bound_guaranteed=false。2Hz会漏掉不足一个采样周期的短暂变化，不声称帧级精确。

同应用的相邻场景用低分辨率相位相关记录 vertical_px、horizontal_px、confidence、accepted。负的 vertical_px 表示新画面内容相对旧画面向上移动；confidence 是质量分数，不是已校准统计概率。只提供滚动候选，不实际拼接，不保证切标签等变化不会产生偶然相关。

## 坐标、刺激物与 AOI

屏幕截图是完整内屏逻辑尺寸（含菜单栏/刘海），DPR仅记录，不二次乘入。Vision的左下原点框转为左上原点归一化框。

刺激物仅PNG/JPG；Qt与Pillow均应用EXIF方向。用实际全屏客户区（可从y=32开始）等比留边，DemoCoordinates先把完整内屏点换到客户区，再换到图片坐标；留边区不算图片注视。显示后文件改变、客户区尺寸改变会拒绝/结束，避免混用几何。保存原文件SHA256、规范PNG校验和和图片矩形；不调用截屏后端。PDF暂不实现。

文字按行距/对齐合并，并提供标题/段落/按钮候选；边缘和颜色连通区提供图片/大色块候选，均不宣称可靠语义分类。小于误差尺度的相邻块合并，保留 merged_ids；不相邻的过小块标 below_error_scale，不能假装定位更精细。手动矩形也保留合并来源和原矩形。

sigma与圆环同源，唯一换算在 kernel_scale：匹配B核对平均误差；没有则配置示意值。sigma=误差半径，未实测屏幕不伪造厘米。刺激物把同一个核换到图片归一化空间。

硬归属按中心及确定的重叠优先级。软归属对不重叠区域归属网格积分二维高斯，背景和屏外概率保留，不把所有质量强行分入AOI。高斯模型分摊不是精度保证。真实屏外估计另列，不裁剪为屏内命中。场景热图按时长累计、不衰减，图像放大/上色复用 render_heatmap；不会改变现有实时热图。

## 存储与生命周期

全部位于被Git忽略的data/attention/<id>/。session.json保存版本/条件/矩阵/参数快照；scenes.jsonl包括scene、screen_sample、state、transition_pending、验收事件和end；gaze.jsonl只有白名单数值，raw必须2维、origin_m必须3维，拒绝任意数组/图像；fixations.jsonl是绑定后的注视。

scenes/为PNG；aoi.json保存本地OCR文字/候选及坐标空间；heatmaps/为PNG/网格CSV；stats/为区域及会话JSON/CSV。completion.json在主要文件flush/fsync后记结束至整理耗时（不含完成标记自身写入）。processing_ms只表示处理阶段，不能冒充所有文件落盘完成耗时。

配额含临时文件和派生输出，预留收尾空间。达到上限停止；不静默丢行、删旧场景。逐行flush，PNG/JSON原子替换；缺end或截断尾行警告。离线重跑写derived/<版本>/，不修改原始日志或档案。

暂停/结束使用状态和epoch屏障，连后端初始化期间的暂停也禁止随后发起截图；已在途图像丢弃。删除先取消、终止OCR并停止所有写者，再删除整个目录，后台不得复写。

## 隐私和性能

截图按本程序进程的原生应用过滤器排除，包含穿透层、提示、面板、菜单及后创建窗口，不依赖先hide。活动事件只存应用名，不查询系统窗口标题。前台排除应用时暂停；过滤器还排除已知名单应用窗口。应用激活变更、前后应用清单不一致时丢弃在途帧。缺授权/不能建立排除/内屏不可用时拒绝采集。

默认隐藏圆环；独立录制提示不淡出，临时显示圆环不改档案。页面只是127.0.0.1已知测试内容，令牌、Host、Origin和字段白名单校验，不提供会话文件或截屏接口。刺激物不截屏；摄像头图像始终不保存、不上传。

额外CPU<单核10%是尚待真人会话验证的目标。自动验收基线与录制各约30秒，包含进程树，WindowServer另列。正式CPU验收不加--profile-seconds；主线程剖析另做开/关各一次。默认录制隐藏圆环，基线采用正常非录制显示，这一差异应随实测说明。

受控图案/OCR/合成视线测试不能替代真人硬归属、真实滚动/切换、权限拒绝及性能验收。指南见README和验证记录；绝不提前标通过。
