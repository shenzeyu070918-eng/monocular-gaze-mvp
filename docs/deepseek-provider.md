# M10 DeepSeek 接入与价格查证

查证日期：**2026-11-06**。仅查阅DeepSeek第一方公开文档；未调用模型、余额或模型列表API，未发送任何会话数据。

## 正式模型与地址

[官方模型/价格页](https://api-docs.deepseek.com/quick_start/pricing/)与[Vision指南](https://api-docs.deepseek.com/guides/vision/)确认：

- API正式名称 **`deepseek-flash`**，当前服务版本 **DeepSeek-V4.1-Flash-1017**，支持图片输入。
- `deepseek-v4-flash-vision-exp`、`deepseek-v4-flash`仍作为兼容名称接受，但对应旧模型已退役，转由当前Flash提供服务并按Flash价格计费；不将实验名设为默认。
- `deepseek-v4-pro`不支持图片。本项目对此模型自动使用纯文字模式；其它未在本地核实图片清单中的模型也先用纯文字，不在确认前联网探测。
- 第一方OpenAI兼容 `base_url` 为 **`https://api.deepseek.com`**，本项目使用 **`POST /chat/completions`**。

默认配置：

```yaml
report:
  provider: chat
  base_url: https://api.deepseek.com
  model: deepseek-flash
  key_env: DEEPSEEK_API_KEY
  keychain_service: monocular-gaze-report
  keychain_account: deepseek
  ai_text_only: false
  ai_thinking: false
  ai_timeout_s: 30.0
```

环境变量和钥匙串账户独立于旧OpenAI配置，不回退读取旧密钥。更新代码后重新启动悬浮层以载入默认值；`config/local.yaml`中的同名项仍优先。密钥不写入YAML、JSON、日志或报告。

## 在 macOS 钥匙串保存 API key

1. 用 Spotlight 打开「钥匙串访问」，选左栏的「登录」钥匙串。
2. 按 `⌘N` 新建密码项目。项目名称填 `monocular-gaze-report`，账户名称填 `deepseek`，将 API key 粘贴到密码栏，再点「添加」。已有同名同账户项目时，编辑其密码即可。
3. 重新启动 `m10-report` 分支的悬浮层。如果 macOS 询问是否允许 `security` 读取该项目，核对名称后选择「允许」。

把 key 只粘贴到钥匙串的密码栏。配置文件只需保存服务和账户名称。这样 key 留在加密钥匙串中，终端历史也只有你正常使用的启动命令。启动环境若已有 `DEEPSEEK_API_KEY`，应用会优先使用该变量。 [Apple 钥匙串快捷键](https://support.apple.com/en-ca/guide/keychain-access/kyca699a9058/mac) · [Apple 钥匙串访问授权](https://support.apple.com/en-gb/guide/mac-help/kychn002/26/mac/26)

## 请求格式与本地校验

按[Chat Completions参考](https://api-docs.deepseek.com/api/create-chat-completion/)与[JSON Output指南](https://api-docs.deepseek.com/guides/json_mode/)，DeepSeek请求使用：

- `response_format: {type: json_object}`；完整Schema、json关键词及示例写入系统提示词，不发送OpenAI的`json_schema`格式。
- `max_tokens`控制输出上限。不发送`store`、`max_completion_tokens`或OpenAI式的`reasoning`对象。
- 依据[Thinking Mode指南](https://api-docs.deepseek.com/guides/thinking_mode/)，显式传 `thinking: {type: disabled}`。官方默认开启思考，本项目默认关闭以控制耗时，仍以30秒总截止/降级保证预算，不承诺服务必定30秒完成。

JSON模式只保证语法倾向，不替代本地验证。中文、结构/键、证据`metric_id/value/unit`、人数和所选场景仍严格校验；空白输出、截断、错误证据、未知单位或数字等均拒绝，保留统计报告，无自动重试。`reasoning_content`不进入报告或日志。

## 图片与纯文字

确认窗显示**Provider名称、实际服务地址、模型、发送模式、所选场景、全部JSON字段和费用估算**。图片模式仍只发所选缩略图和白名单统计；不发摄像头画面。可主动勾选“纯文字模式”，或在不支持图片/能力未知时自动启用：

- 只发白名单统计JSON及区域短标签，不生成或携带图片/base64，不发完整OCR。
- 短标签可能摘自OCR的前几个字或用户手动命名，确认窗明确说明；不能把“纯文字”理解为完全不含画面中的文字。
- 纯文字时完整OCR选项关闭，不允许仅凭统计臆测未提供的画面内容、颜色和文字。
- 改模式/模型/场景/内容/价格后需要新确认；未确认零发送，不做API能力探测，也不在图片请求失败后私自补发纯文字请求。

当前Vision文档给出的单张图片token上限为1024；本地估算采用此图像预算与文本字节启发式，不沿用旧实验公告的图片预算，也不冒充精确tokenizer或账单。模型返回usage后再作费用估算。

## 当前单价与时段

以下均为**USD / 百万token**，来源：[官方模型/价格页](https://api-docs.deepseek.com/quick_start/pricing/)，配置价格快照2026-11-05，2026-11-06复核一致。

| 模型 | 高峰输入（未命中） | 高峰输入（命中） | 高峰输出 | 非高峰输入（未命中） | 非高峰输入（命中） | 非高峰输出 |
|---|---:|---:|---:|---:|---:|---:|
| deepseek-flash | 0.30 | 0.006 | 1.20 | 0.15 | 0.003 | 0.60 |
| deepseek-v4-pro | 1.32 | 0.044 | 3.96 | 0.66 | 0.022 | 1.98 |

官方高峰：**周一至周五UTC 01:00–04:00、06:00–10:00，中国法定假日除外**。其余为非高峰，周末及中国法定假日全天非高峰，价格为高峰一半。台北时间对应09:00–12:00、14:00–18:00；判定代码使用UTC，不依赖电脑本地时区。

配置 `deepseek_prices`记录高峰单价；`price_peak_windows_utc`、`price_peak_weekdays`、`price_offpeak_multiplier`记录时段规则。假日表没有随意猜测：

- `price_holidays`可填已核实的中国法定假日，格式YYYY-MM-DD；只有列入`price_holiday_years`的年份才声明该年日历完整。
- 默认两者为空。周末或峰窗外可按非高峰；未核实年份的周一至周五峰窗，按高峰上限估算并注明可能半价。不会在确认前联网下载日历。
- 确认后、实际发送前若计价时段变化，要求重新确认，零补发。
- 请求跨时段时保存开始/结束UTC及两个价格快照，结合返回usage给估算区间；服务端具体扣费时刻未由本项目宣称已知。

DeepSeek的`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`用于核对缓存量，输出量取`completion_tokens`；总量不一致拒绝。缺usage/缓存明细/单价时费用为未知，不填0。所有金额标记**估算、非账单**；官方可能调整价格，账户赠送余额或实际扣款也不能从token估算推定。

自定义地址/未知模型不暗套第一方单价：显示费用未知，或用户为其它服务明确配置`pricing_scheme: flat`及单价。现有OpenAI兼容适配与测试保留，但本项目默认已改为DeepSeek。

## 验证状态

已验证：全部使用模拟provider，覆盖图片请求字段、显式关闭思考、JSON模式、本地证据校验、纯文字无图片、模型不支持图片时自动降级、缓存usage、UTC边界、周末/模拟假日、跨时段估算、时段变化再确认、未确认零请求及密钥不落盘。

未验证：真实DeepSeek请求的延迟、输出质量、真实usage与账单。需用户配置自己的密钥，在确认窗亲自选择数据并确认后才可实测；模拟测试不当作真实30秒或费用验收。
