# M8 眼控交互 Demo

基线 main `aa53452`（PR #13已合并），分支`m8-gaze-demo`。只实现已批准的M8；不加入新的校准模型、键盘监听、屏幕/相机画面采集或自动上传。

## 模块与接口

- `gaze_core/dwell.py`：`DwellSelector.set_buttons / feed / advance`，输入逻辑坐标、真实采集时间和当前单调时刻；输出一次触发或进度。无Qt、硬件、文件或系统时钟依赖。当前按钮独享扩大命中区，离开/无效区间冻结，连续有效采集间隔才累计。冷却后重新积满，不重用已触发进度。
- `gaze_core/demo.py`：误差半径约束布局、分页、一次练习、沟通反馈、游戏随机目标/计分和撤销；随机源可注入。所有交互结果均由数值事件输出，语音只是反馈，不影响拟合或评分。
- `overlay/demo_session.py`：只在匹配的Python B/C和空闲相机条件下开启；保存并恢复原可见性，协调可选现有录制、原有显示坐标与系统语音。应用条件/矩阵版本改变即退出，避免按钮尺度和修正中途变化。Demo不申请任务全速、不自动开录。
- `overlay/demo_window.py`：完整内屏、不穿透的普通全屏窗口；复用任务窗口设置屏幕、激活、Esc与退出全屏取消方式。首次绘制确认前不接收选择；按钮/进度/圆环变化只更新相应区域，无变化不重绘。深蓝背景、浅色按钮、琥珀进度，文字默认48逻辑像素且不允许小于40；游戏目标为矢量绘制。
- `overlay/speech*.py`：Mac系统say、Windows系统SAPI，各自隔离。固定程序/脚本，不插值用户文字；UTF-8文本只经stdin，异步完成或失败，退出时取消并异步回收，防止阻塞界面和堆积朗读。
- `overlay/actions.py / panel.py / hotkeys.py / runtime.py`：同一动作供面板、托盘和现有Carbon/RegisterHotKey使用；增加demo，不监听任意键盘事件。Esc只由Demo自身窗口处理。自身鼠标点击仍经原OwnWindows排除。
- `overlay/window.py / label.py / startup_log.py / app.py`：动态演示外观与持久状态条，九点成功保存后的3秒独立反馈，默认精简终端及详细启动日志；网页入口和网页日志行为不改。

## 时钟与状态

`capture_ns`与`event_ns`共用perf_counter_ns时钟。前者只使用OpenCV读帧返回时间；后者为主线程执行选择/撤销/结束时刻。显示点直接复用现有个人修正→眼跳检测→One Euro输出，不另算/平滑/裁剪。一次按钮累计只认可相邻有效新帧；重复、乱序、未来或过期帧不能推动进度。短暂离开和眨眼不补算时间，无帧超过失效容忍重置。

配置默认：停留1000ms、离开宽限150ms、冷却800ms、无效容忍400ms、当前命中区扩大0.5×对应误差半径。按钮每轴≥3×对应圆环半径，间距≥该轴半径；DPR不重复相乘。优先六个大按钮，必要时减少每页数量并保留同样大小的翻页按钮；不安全时拒绝布局。

练习成功后自动进入应用；practice=false可关闭。沟通反馈至少保持配置时长，等待系统朗读，超时取消而不挂住。游戏计时在实际目标首次绘制时开始，每次新目标先重新确认绘制；旧目标时期采集的帧不能触发新目标。

Esc总是退出，若在最后触发后≤1000ms，还撤销该次选择/得分。完成游戏时保留最后一次触发的撤销窗口，之后再确认汇总；异常提前结束时仍未完成观察窗口的记录为待确认，不能虚构false。操作定义不等于真实意图的客观误触标签。

## 数值录制

原有单会话、后台队列、flush及隐私白名单不变，仅新增两种Python事件，replay支持同一事件时钟排序：

- `demo_selection`的triggered包含run_id、selection_id、app(board/game)、group(B/C)、stage(practice/application)、固定target_id(0–5/practice/next)、实际归一化目标中心、dwell_ms、selection_ms、可空reaction_ms、capture_ns、event_ns、scored、hit_expand_ratio、false_activation(null)。confirmed/undo只允许相同ID及布尔最终标记，不接受任意目标文本或图像数据。开始准备录制时暂存事件，文件就绪后写入；结束Demo仍将暂存事件交给原队列。不同录制会话间不追加孤立撤销。
- `demo_summary`包含run_id、app、group、event_ns、reason、completed、score、selection_count、false_activation_count、pending_count、reaction_count、mean_reaction_ms、mean_selection_ms、elapsed_ms、hit_expand_ratio。练习和翻页不计入应用汇总，撤销的得分不计入游戏有效反应时间。停录后不写盘，不自动另建会话。

不把展示选择写成M4真实鼠标click，不改变评估CSV。录制不含沟通文字、语音、窗口标题、屏幕或摄像头内容；启动日志也省略自定义沟通文字。预览仅由合成数值绘制Demo自身UI，保存在忽略的data/。

## 验证边界

纯逻辑测试覆盖numpy、真实间隔、宽限、粘滞只作用当前按钮、冻结/超时、冷却、分页、首次绘制、练习一次、计分及最后一秒撤销。Qt离屏验证交互标记、最小字体、局部更新、还原隐藏状态、条件改变退出、语音失败/取消；真实写入回放检查ID、时间与坐标，白名单拒绝图像/base64。现有校准/核对、原始取样、点击和热图测试全部回归。

Mac实机需分别测试粘滞关/开各20次，投影远端可读性、真实朗读、完整60秒游戏、演示模式、退出还原和profiler开销；Windows系统语音、热键、缩放、窗口与退出待本机验证。软件测试不能替代这些验收，不从背景精度/延迟范围生成新成绩。
