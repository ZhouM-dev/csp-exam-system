"""Map existing platform data to upstream HydroJudge; no checker or execution logic."""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from . import gojudge


def endpoint():
    base = os.environ.get('CSP_HYDRO_URL', 'http://127.0.0.1:8082').rstrip('/')
    url = urllib.parse.urlparse(base)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.path or url.query or url.fragment or url.username or url.password:
        raise gojudge.GoJudgeError('Hydro 适配服务必须使用本机回环地址')
    return base


def health():
    try:
        with urllib.request.urlopen(endpoint()+'/health',timeout=5) as response:
            result=json.load(response)
        if result.get('engine')!='HydroJudge' or result.get('version')!='4.0.6':
            raise ValueError('Unexpected HydroJudge engine/version')
        return result
    except (OSError,ValueError) as error:
        raise gojudge.GoJudgeError('HydroJudge 服务不可用：'+str(error)) from error


def judge(pid, source, ext, cases, per, *, code, time_ms, memory_mb, io_mode):
    base=endpoint()
    token = os.environ.get('CSP_HYDRO_TOKEN', '')
    if not token:
        raise gojudge.GoJudgeError('HydroJudge 尚未配置；不会回退到自编判题器')
    raw = source.encode('utf8') if isinstance(source,str) else source
    # Test data keeps the platform's original byte contents; only file metadata crosses HTTP.
    case_files = []
    for c, score in zip(cases, per):
        case_files.append({'input':c['name']+'.in','output':c['name']+'.out','score':score})
    data = {'pid':pid,'source_b64':base64.b64encode(raw).decode('ascii'), 'ext':ext,
            'code':code,'time_ms':int(time_ms),'memory_mb':int(memory_mb),'io_mode':'stdio' if io_mode=='stdin' else io_mode,'cases':case_files}
    request=urllib.request.Request(base+'/judge',data=json.dumps(data).encode(),
              headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=max(180, len(cases)*time_ms/1000*4+120)) as response:
            result=json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail=json.load(error).get('error','')[:300]
        except (ValueError,TypeError,AttributeError):
            detail=''
        raise gojudge.GoJudgeError('HydroJudge 服务错误：'+str(error)+' '+detail) from error
    except (urllib.error.URLError,TimeoutError,OSError,ValueError) as error:
        raise gojudge.GoJudgeError('HydroJudge 服务不可用：'+str(error)) from error
    if not isinstance(result,dict) or not isinstance(result.get('status'),int) or result.get('engine',{}).get('name')!='HydroJudge':
        raise gojudge.GoJudgeError('HydroJudge 返回格式无效')
    rows=[]
    for c in result.get('cases',[]):
        i=int(c['id'])-1
        if i<0 or i>=len(cases): raise gojudge.GoJudgeError('HydroJudge 返回未知测试点')
        rows.append({**c,'no':cases[i]['name'],'input':cases[i]['in'].decode('utf8','replace'),
                     'answer':cases[i]['out'].decode('utf8','replace'), 'output':'',
                     'status_text':str(c.get('message') or '')})
    row={**result,'testcases':rows, 'judgeTexts':result.get('messages',[]),
         'passed':sum(c.get('status')==1 for c in rows),'total':len(cases)}
    if result['status'] in (7,8,31): row['note']='HydroJudge 返回编译错误或系统／数据配置错误'
    if result['status']==31: row['status']=8
    return row
