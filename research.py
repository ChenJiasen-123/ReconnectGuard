"""四组缓存对照。先 repeat=1 验证干预，再冻结预算运行 repeat=10。"""
import argparse
import csv
import io
import json
from pathlib import Path
import uuid
from runner import ROOT, load_case, run_case, _save


def run_cache(output,repeat,ap,sta,*,session_factory=None):
    out=Path(output)/('cache-'+uuid.uuid4().hex);out.mkdir(parents=True)
    rows=[]
    plan={'repeat':repeat,'policies':{'A':'retain/retain','B':'flush/retain','C':'retain/flush','D':'flush/flush'},'recovery_timeout_s':20,'radio':'physical','ap_iface':ap,'sta_iface':sta,'runs':rows,'stop_reason':None}
    _save(out/'summary.json',plan)
    for n in range(repeat):
        for offset in range(4):
            policy='ABCD'[(n+offset)%4]
            result,folder=run_case(load_case(ROOT/'cases.yaml','client_disconnect_reconnect'),out,backend='real',radio='physical',ap_iface=ap,sta_iface=sta,cache_policy=policy,evaluator_spec='observer.oracle:Oracle',analyze=True,session_factory=session_factory)
            rows.append({'round':n+1,'policy':policy,'result':result,'output':str(folder.resolve())})
            if result['cleanup_status']=='failed' or result['error_category'] in ('cancelled','environment_error'):
                plan['stop_reason']=result['error_category'] or 'cleanup_error'
            _save(out/'summary.json',plan)
            print(json.dumps({'policy':policy,'execution':result['execution_status'],'auth_path':result['auth_path'],'error':result['error']},ensure_ascii=False),flush=True)
            if plan['stop_reason']:break
        if plan['stop_reason']:break
    stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['policy','executed','recovered','data_pass','full_sae','pmksa_reuse','unknown'])
    for p in 'ABCD':
        rs=[r['result'] for r in rows if r['policy']==p]
        writer.writerow([p,len(rs),sum(r['recovery']=='pass' for r in rs),sum(r['data_plane_recovery']=='pass' for r in rs)]+[sum(r['auth_path']==a for r in rs) for a in ['full_sae','pmksa_reuse','unknown']])
    (out/'comparison.csv').write_text(stream.getvalue())
    print('OUTPUT='+str(out.resolve()),flush=True)
    return plan


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repeat',type=int,default=1);p.add_argument('--ap-iface',required=True);p.add_argument('--sta-iface',required=True);p.add_argument('--output',type=Path,default=ROOT/'results');a=p.parse_args()
    if a.repeat<1:p.error('repeat must be positive')
    plan=run_cache(a.output,a.repeat,a.ap_iface,a.sta_iface)
    return 0 if len(plan['runs'])==a.repeat*4 and all(r['result']['execution_status']=='completed' for r in plan['runs']) else 1


if __name__=='__main__':raise SystemExit(main())
