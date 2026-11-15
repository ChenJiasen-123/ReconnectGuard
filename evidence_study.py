"""冻结 v2 分析规则后，独立执行实体缓存对照及固定五包诊断。"""
import argparse
import json
from pathlib import Path

from backends.sessions import RealSession
from research import run_cache
from rg_platform.connectivity import DiagnosticMixin
from runner import ROOT


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repeat',type=int,default=3)
    parser.add_argument('--ap-iface',required=True)
    parser.add_argument('--sta-iface',required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'results')
    args=parser.parse_args()
    if not 1<=args.repeat<=10:parser.error('repeat must be within 1..10')
    from rg_platform.physical import PhysicalLab
    class EvidenceLab(DiagnosticMixin,PhysicalLab):
        pass
    def session(output,scenario,client):
        return RealSession(output,client,lab_factory=lambda mode,**kw:EvidenceLab(
            mode,ap_iface=args.ap_iface,sta_iface=args.sta_iface,**kw))
    plan=run_cache(args.output,args.repeat,args.ap_iface,args.sta_iface,session_factory=session)
    rows=[]
    for row in plan['runs']:
        result=row['result'];probe=result.get('data_plane',{}).get('recovery',{})
        rows.append({'run_id':result['run_id'],'policy':row['policy'],'auth_path':result['auth_path'],
                     'execution_status':result['execution_status'],
                     'baseline_followup':result.get('data_plane',{}).get('baseline',{}).get('followup'),
                     'connection_recovery':result['connection_recovery'],
                     'primary_ping':probe.get('status'),'followup':probe.get('followup'),
                     'cleanup_status':result['cleanup_status']})
    if rows:
        out=Path(plan['runs'][0]['output']).parent
        (out/'diagnostic-summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    if len(rows)!=args.repeat*4 or any(
            r['execution_status']!='completed' or r['auth_path']=='unknown' or
            r['primary_ping']!='pass' or (r['followup'] or {}).get('status')!='pass' or
            (r['baseline_followup'] or {}).get('status')!='pass' or r['cleanup_status']=='failed' for r in rows):
        raise SystemExit(1)
