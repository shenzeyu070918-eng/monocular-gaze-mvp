"""Read-only DeepSeek diagnostics. No completion requests or credential output."""
import argparse
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from gaze_core.config import DEFAULT_CONFIG,load_settings
from gaze_core.reporting.ai import bounded_request,endpoint,MissingCredentials,ProviderFailure,read_key


def check(options,*,request=bounded_request,key_reader=read_key):
    target=urlsplit(endpoint(options))
    if target.scheme!='https' or target.hostname!='api.deepseek.com' or target.port not in (None,443):
        raise ValueError('诊断仅支持 DeepSeek 第一方 HTTPS 地址 https://api.deepseek.com')
    key=key_reader(options)
    results={}
    for name,path in [('models','/models'),('balance','/user/balance')]:
        try:
            status,data,ca=request('https://api.deepseek.com'+path,key,time.monotonic()+options.ai_timeout_s)
            value=None
            if name=='models' and isinstance(data,dict) and isinstance(data.get('data'),list):
                value=any(isinstance(item,dict) and item.get('id')==options.model for item in data['data'])
            if name=='balance' and isinstance(data,dict) and type(data.get('is_available')) is bool:
                value=data['is_available']
            results[name]=dict(status=status,tls='正常（'+ca+'）',value=value,error=None)
        except TimeoutError:
            results[name]=dict(status=None,tls='未确认',value=None,error='请求超时')
        except ProviderFailure as exc:
            tls='失败' if exc.kind=='tls' else '正常' if exc.status is not None else '未确认'
            results[name]=dict(status=exc.status,tls=tls,value=None,error=exc.display())
    return results


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    args=parser.parse_args(argv)
    try:results=check(load_settings(args.config).report)
    except MissingCredentials:
        print('未读到 AI 密钥（环境变量或 macOS 钥匙串）')
        return 1
    except ProviderFailure as exc:
        print(exc.display())
        return 1
    except (ValueError,OSError):
        print('无法读取诊断配置，请核对 report 设置和 DeepSeek 第一方 HTTPS 地址')
        return 1
    for name,label in [('models','配置的模型在列表中'),('balance','余额可用')]:
        row=results[name]
        value='未知' if row['value'] is None else '是' if row['value'] else '否'
        path='/models' if name=='models' else '/user/balance'
        print(f'GET {path} · TLS {row["tls"]} · HTTP {row["status"] or "无"} · {label} {value}')
        if row['error']:print(row['error'])
    return 0 if all(row['value'] is True for row in results.values()) else 1


if __name__=='__main__':
    raise SystemExit(main())
