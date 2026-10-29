# Windows安装与更新

本说明用于当前源码；v0.1.0归档不包含后续修复。项目文件夹可以含中文和空格，例如`D:\课程展示\视线 项目`。本分支的人脸/手势MediaPipe加载器先由Python读取模型字节，再用`model_asset_buffer`初始化，避免Windows原生路径读取失败。

## 安装或更新

安装64位Python 3.12，进入项目文件夹打开PowerShell，执行：

```powershell
py -3.12 setup_mvp.py --overlay
```

仅使用网页时可省略`--overlay`。RTX 5070沿用README的`--torch-build cu130`选项。更新本修复后也要重新执行安装命令，让`vendor/ptgaze`中的修复重新安装到项目环境；不要手动修改`.venv`中的源码，重装时会被覆盖。

如果已安装Python，但`py -3.12`找不到它，使用实际安装位置的完整路径。以下路径只是示例，必须替换为本机Python 3.12的路径：

```powershell
& "C:\Users\你的用户名\AppData\Local\Programs\Python\Python312\python.exe" setup_mvp.py --overlay
```

在命令提示符中使用同样的命令时去掉开头的`&`，路径仍加双引号。不要复制其他电脑的`.venv`。

## 下载出错或卡住

下载依赖或模型出现SSL报错时，重新运行**相同的安装命令**即可；如果卡住，先Ctrl+C结束，再重新运行。已经安装的依赖和摘要正确的权重会复用，不必删除整个环境或已下载成功的模型。安装成功前不算完成；如果持续失败，保留完整错误信息，再检查网络与来源可访问性。

## 启动

- 悬浮层/眼手星系：双击`启动悬浮层-Windows.cmd`。
- 网页：双击`启动-Windows.cmd`，保持终端打开，按README访问本机网页。

两个脚本都切换到脚本所在目录并引用项目环境，支持中文和空格路径。本次自动化验证在Mac上使用含中文/空格的临时路径、真实模型和合成黑帧；**修复后的Windows启动/相机/手势实机复测仍待确认**。请在同学原先出错的中文路径重新安装并分别启动网页和眼手星系，记录是否还出现模型打开错误。其余步骤见[README](../README.md)，记录边界见[验证记录](../验证记录.md)。

也可单独运行不打开相机的路径回归：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_model_paths.py -v
```

真实face/hand加载两项需要本地已下载的task权重；缺权重会明确跳过，不能把跳过当通过。字节参数用例不依赖权重。
