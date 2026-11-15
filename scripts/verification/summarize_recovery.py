"""只读汇总连续进程恢复实验，保留阶段结果、缓存表、日志引用和输入哈希。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def summarize(batch):
    batch=Path(batch)
    summary=json.loads((batch/'summary.json').read_text())
    rows=[]
    for item in summary['runs']:
        folder=batch/Path(item['output']).name
        result=json.loads((folder/'result.json').read_text())
        observed=json.loads((folder/'recovery-observations.json').read_text())
        caches={p.stem:json.loads(p.read_text()) for p in sorted(folder.glob('*-cache.json'))}
        excerpts=[]
        for number,line in enumerate((folder/'sta.log').read_text().splitlines(),1):
            if any(k in line for k in ('CTRL-EVENT-SSID-TEMP-DISABLED','CTRL-EVENT-SSID-REENABLED')):
                excerpts.append({'ref':'sta.log#line='+str(number),'text':line})
        release=result['release_operation']
        phase_rows={}
        journal=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
        for name,phase in result['phases'].items():
            compact={key:phase[key] for key in ('status','elapsed_s','budget_s','event','event_seq','status_after') if key in phase}
            compact['probe']=phase.get('probe',{}).get('status')
            if name=='release' and phase.get('event_seq'):
                event=next(r for r in journal if r['seq']==phase['event_seq'])
                compact['connected_ms_after_release_confirmed']=(event['monotonic_ns']-release['finished_ns'])/1e6
            compact['observed']=observed['phases'][name]
            phase_rows[name]=compact
        inputs=['result.json','meta.json','events.jsonl','ap.log','sta.log','trace.pcap','recovery-actions.json','recovery-observations.json']
        rows.append({'run_id':folder.name,'condition':result['condition'],'outcome':result['outcome'],
                     'phases':phase_rows,'caches':caches,'temporary_disable_log_refs':excerpts,
                     'protocol_processes_unchanged':result['protocol_processes_unchanged'],
                     'cleanup':result['cleanup'],'error':result['error'],
                     'binary_sha256':json.loads((folder/'meta.json').read_text())['binary_sha256'],
                     'source_sha256':result['source_sha256'],
                     'input_sha256':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in inputs}})
    return {'batch':str(batch),'phase_budget_s':summary['phase_budget_s'],'runs':rows,
            'groups':{name:dict(Counter(r['outcome'] for r in rows if r['condition']==name)) for name in summary['conditions']},
            'scope':'one derived client baseline and a local scoped experimental backport; one AP, isolated hwsim; finite observation',
            'security_verdict':'not_evaluated'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('batch',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(summarize(args.batch),ensure_ascii=False,indent=2)+'\n')
