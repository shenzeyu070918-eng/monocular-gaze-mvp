"""M10 display and opt-in provider options. No credentials in configuration."""
from dataclasses import dataclass,field
import math,re


@dataclass(frozen=True)
class ReportSettings:
    auto_stimulus: bool = True
    scanpath_max_points: int = 80
    scanpath_merge_sigma: float = 0.0
    scanpath_gap_s: float = 0.5
    scene_expand_count: int = 3
    scene_min_valid_s: float = 0.5
    short_label_chars: int = 8
    editor_idle_s: float = 1800.0
    image_max_width: int = 1600
    ai_image_max_width: int = 960
    provider: str = 'chat'
    base_url: str = 'https://api.deepseek.com'
    model: str = 'deepseek-flash'
    key_env: str = 'DEEPSEEK_API_KEY'
    keychain_service: str = 'monocular-gaze-report'
    keychain_account: str = 'deepseek'
    ai_timeout_s: float = 30.0
    ai_max_output_tokens: int = 3000
    ai_text_only: bool = False
    ai_thinking: bool = False
    ai_vision_models: tuple = ('deepseek-flash','deepseek-v4-flash','deepseek-v4-flash-vision-exp')
    pricing_scheme: str = 'deepseek'
    deepseek_prices: object = field(default_factory=lambda:{'deepseek-flash':dict(input=.3,cached=.006,output=1.2),'deepseek-v4-pro':dict(input=1.32,cached=.044,output=3.96)})
    price_peak_windows_utc: tuple = ((1,4),(6,10))
    price_peak_weekdays: tuple = (0,1,2,3,4)
    price_offpeak_multiplier: float = .5
    price_holidays: tuple = ()
    price_holiday_years: tuple = ()
    input_usd_per_million: float | None = None
    cached_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    price_date: str = '2026-11-05'

    def __post_init__(self):
        for name in ('base_url','model','key_env','keychain_service','keychain_account','price_date'):
            value=getattr(self,name)
            if not isinstance(value,str) or not value.strip() or any(c in value for c in ('\r','\n','\0')):raise ValueError('report.'+name+' 必须为非空单行字符串')
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',self.key_env) is None:raise ValueError('key_env只填写环境变量名，不是密钥')
        for name in ('scene_expand_count','short_label_chars','scanpath_max_points','image_max_width','ai_image_max_width','ai_max_output_tokens'):
            value=getattr(self,name)
            if type(value) is not int or value<=0:raise ValueError('report.'+name+' 必须为正整数')
        for name in ('scene_min_valid_s','editor_idle_s','scanpath_gap_s','ai_timeout_s','scanpath_merge_sigma'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('report.'+name+' 无效')
        if not 0<self.ai_timeout_s<=30:raise ValueError('AI总预算必须在(0,30]秒内')
        if self.provider not in ('responses','chat'):raise ValueError('report.provider 必须为 responses / chat')
        if self.pricing_scheme not in ('deepseek','flat'):raise ValueError('report.pricing_scheme 必须为deepseek/flat')
        if type(self.ai_text_only) is not bool or type(self.ai_thinking) is not bool:raise ValueError('AI文字/思考开关必须为布尔值')
        if not isinstance(self.ai_vision_models,(list,tuple)) or not all(isinstance(v,str) and v for v in self.ai_vision_models):raise ValueError('图片模型清单无效')
        if not isinstance(self.deepseek_prices,dict):raise ValueError('DeepSeek模型价格表无效')
        for rates in self.deepseek_prices.values():
            if not isinstance(rates,dict) or set(rates)!={'input','cached','output'} or any(v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0) for v in rates.values()):raise ValueError('DeepSeek价格必须为非负有限数或null')
        if isinstance(self.price_offpeak_multiplier,bool) or not isinstance(self.price_offpeak_multiplier,(int,float)) or not math.isfinite(self.price_offpeak_multiplier) or not 0<self.price_offpeak_multiplier<=1:raise ValueError('非高峰折扣无效')
        if not isinstance(self.price_peak_windows_utc,(list,tuple)) or any(not isinstance(v,(list,tuple)) or len(v)!=2 or any(type(n) is not int for n in v) or not 0<=v[0]<v[1]<=24 for v in self.price_peak_windows_utc):raise ValueError('UTC计价窗口无效')
        if not isinstance(self.price_peak_weekdays,(list,tuple)) or any(type(v) is not int or not 0<=v<=6 for v in self.price_peak_weekdays):raise ValueError('计价星期无效')
        from datetime import date
        try:
            if not isinstance(self.price_holidays,(list,tuple)) or not isinstance(self.price_holiday_years,(list,tuple)):raise ValueError()
            for value in self.price_holidays:date.fromisoformat(value)
            if any(type(v) is not int or not 2000<=v<=9999 for v in self.price_holiday_years):raise ValueError()
        except (ValueError,TypeError):raise ValueError('假日配置无效') from None
        if type(self.auto_stimulus) is not bool:raise ValueError('report.auto_stimulus 必须为布尔值')
        for name in ('input_usd_per_million','cached_usd_per_million','output_usd_per_million'):
            value=getattr(self,name)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0):raise ValueError('费用单价无效')
