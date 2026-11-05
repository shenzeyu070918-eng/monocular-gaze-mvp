"""M10 display and opt-in provider options. No credentials in configuration."""
from dataclasses import dataclass
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
    provider: str = 'responses'
    base_url: str = 'https://api.openai.com/v1'
    model: str = 'gpt-6.1-terra'
    key_env: str = 'OPENAI_API_KEY'
    keychain_service: str = 'monocular-gaze-report'
    keychain_account: str = 'openai'
    ai_timeout_s: float = 30.0
    ai_max_output_tokens: int = 3000
    input_usd_per_million: float | None = 1.0
    cached_usd_per_million: float | None = 0.05
    output_usd_per_million: float | None = 5.0
    price_date: str = '2026-11-03'

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
        if type(self.auto_stimulus) is not bool:raise ValueError('report.auto_stimulus 必须为布尔值')
        for name in ('input_usd_per_million','cached_usd_per_million','output_usd_per_million'):
            value=getattr(self,name)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0):raise ValueError('费用单价无效')
