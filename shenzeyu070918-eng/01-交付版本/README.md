# 01 · 交付版本

从 [GitHub Releases](https://github.com/shenzeyu070918-eng/monocular-gaze-mvp/releases) 获取明确版本，不在仓库内存放大zip。

当前正式版本为 **[v1.0.0](https://github.com/shenzeyu070918-eng/monocular-gaze-mvp/releases/tag/v1.0.0)**，tag固定在4ec7aaa。v0.1.0保留为历史测试基线。按[使用手册](../../docs/使用手册.md)安装。

已发布v1.0.0：以M9及已合并Demo为功能基线，包含本轮仓库整理。M10 PR #17暂停，不在功能范围内。历史身份修正已验证并经本人确认推送；含PR #19 Windows脚本修复，PR #20为之后的文档补记，不再移动发行tag。

发布包为源码安装包，包含说明、启动脚本、第三方许可与SHA256清单；不含.venv、模型权重、data、本机配置、密钥或个人日志。首次安装需要下载依赖与公开模型，不能称为免安装或完全离线安装包。

安装完成后，Mac用“启动悬浮层.command”，Windows用“启动悬浮层-Windows.cmd”。原v0.1.0附件保留，不静默替换；新包核对校验值、实际版本与功能边界。[发布流程](../../docs/仓库历史与发布.md)。
