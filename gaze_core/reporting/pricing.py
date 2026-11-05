"""Offline pricing snapshots. No calendar/network lookup and never a billed fee."""
from datetime import datetime,timezone
from urllib.parse import urlsplit

DEEPSEEK_ALIASES={'deepseek-v4-flash':'deepseek-flash','deepseek-v4-flash-vision-exp':'deepseek-flash'}


def utc_now():return datetime.now(timezone.utc)
def is_deepseek(options):return urlsplit(options.base_url).hostname=='api.deepseek.com'
def provider_name(options):
    host=urlsplit(options.base_url).hostname
    return 'DeepSeek' if host=='api.deepseek.com' else 'OpenAI' if host=='api.openai.com' else '兼容服务（'+str(host)+'）'


def image_mode(options,requested=None):
    requested=options.ai_text_only if requested is None else bool(requested)
    supported=options.model in options.ai_vision_models
    if is_deepseek(options) and options.model=='deepseek-v4-pro':supported=False
    if not supported:return True,'所选模型未确认支持图片，自动纯文字；仅发送白名单统计和短标签'
    if requested:return True,'用户选择纯文字；仅发送白名单统计和区域短标签'
    return False,'发送所选场景的合成缩略图、白名单统计和区域短标签'


def snapshot(options,when=None):
    when=when or utc_now()
    if when.tzinfo is None:raise ValueError('计价时间必须包含时区')
    when=when.astimezone(timezone.utc)
    base=dict(date=options.price_date,currency='USD',base_url=options.base_url,scheme=options.pricing_scheme,model=options.model)
    if options.pricing_scheme=='flat':return dict(base,input=options.input_usd_per_million,cached=options.cached_usd_per_million,output=options.output_usd_per_million,period='flat',estimated=True,rule='配置平价快照，非服务账单')
    model=DEEPSEEK_ALIASES.get(options.model,options.model)
    table=options.deepseek_prices.get(model) if is_deepseek(options) else None
    schedule=dict(peak_windows_utc=[list(v) for v in options.price_peak_windows_utc],weekdays=list(options.price_peak_weekdays),offpeak_multiplier=options.price_offpeak_multiplier,holidays=list(options.price_holidays),holiday_years=list(options.price_holiday_years))
    if table is None:return dict(base,input=None,cached=None,output=None,period='unknown',estimated=True,rule='服务地址/模型无已核实的DeepSeek价格，费用未知',schedule=schedule)
    hour=when.hour+when.minute/60+when.second/3600
    peak=when.weekday() in options.price_peak_weekdays and any(a<=hour<b for a,b in options.price_peak_windows_utc)
    known_holiday=when.date().isoformat() in options.price_holidays
    unknown_calendar=peak and not known_holiday and when.year not in options.price_holiday_years
    if known_holiday:peak=False
    factor=1. if peak else options.price_offpeak_multiplier
    rates={k:None if table.get(k) is None else table[k]*factor for k in ('input','cached','output')}
    rule='工作日UTC 01–04/06–10为高峰，周末及中国法定假日非高峰；非高峰半价。'
    if unknown_calendar:rule+='假日表未核实，按高峰上限估算，实际可能半价。'
    return dict(base,**rates,period='peak' if peak else 'offpeak',estimated=True,holiday_uncertain=unknown_calendar,
                lower_multiplier=options.price_offpeak_multiplier if unknown_calendar else 1.,rule=rule+'按请求时段快照估算，不是账单；跨时段归属以服务端为准。',schedule=schedule)


def crosses_period(options,start,end):
    return snapshot(options,start)['period']!=snapshot(options,end)['period']
