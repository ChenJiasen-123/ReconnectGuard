"""所有策略共用的离线观测指标；分母不来自引导模型。"""
import argparse,json,re
from pathlib import Path
from datetime import datetime
from .frames import read_pcap_frames,classify_frame

STAGES={'sae_commit','sae_confirm','open_system_auth','assoc_req','eapol_m1','eapol_m2','eapol_m3','eapol_m4','deauth','disassoc'}


def inspect(folder):
    folder=Path(folder);j=[json.loads(l) for l in (folder/'events.jsonl').read_text().splitlines()]
    ds=[r for r in j if r['source']=='sta' and 'CTRL-EVENT-DISCONNECTED' in r['raw_event']]
    boundary=datetime.fromisoformat(ds[0]['received_at']).timestamp() if ds else float('inf')
    b=json.loads((folder/'connected_baseline.json').read_text())['sta'];pair={b['bssid'],b['address']}
    frames=[r for r in read_pcap_frames(folder/'trace.pcap') if r['time']>boundary and {r.get('addr1'),r.get('addr2')}==pair]
    stages={label for r in frames for label in classify_frame(r)} & STAGES
    associations=[r for r in frames if r['type']==0 and r['subtype']==0]
    if associations:stages.add('assoc_req')
    injections=json.loads((folder/'fault-injection.json').read_text()) if (folder/'fault-injection.json').exists() else []
    by_seq={r['seq']:r for r in j}
    for injection in injections:
        raw=by_seq.get(injection['seq'],{}).get('raw_event','')
        m=re.search(r'MGMT-RX ([0-9a-fA-F]+)',raw)
        verified=False
        if m:
            buf=bytes.fromhex(m.group(1))
            if len(buf)>=24:
                dst=':'.join(f'{x:02x}' for x in buf[4:10]);src=':'.join(f'{x:02x}' for x in buf[10:16])
                verified=dst==b['bssid'] and src==b['address']
        elif 'EAPOL-TX '+b['address'] in raw:
            verified=True
        injection['target_verified']=verified
    result=json.loads((folder/'result.json').read_text())
    report={'run_id':folder.name,'timeline':[{'frame':r['frame'],'ms_from_disconnect':round((r['time']-boundary)*1000,3),'stages':classify_frame(r)+(['assoc_req'] if r['type']==0 and r['subtype']==0 else []),'status_code':r.get('status_code'),'reason_code':r.get('reason_code')} for r in frames if classify_frame(r) or r['type']==0 and r['subtype']==0],'stages':sorted(stages),'stage_denominator':sorted(STAGES),'association_request_frames':len(associations),'association_sequences':len({(r['addr2'],r.get('sequence_control',r['frame'])) for r in associations}),'first_rejection':next(({'frame':r['frame'],'status_code':r.get('status_code'),'reason_code':r.get('reason_code')} for r in frames if r.get('status_code',0)!=0 or r.get('reason_code')==9),None),'injections':injections,'effective_fault':any(r.get('target_verified') and r['action'] in ('deauth_reason9','assoc_status53','drop') for r in injections),'recovery':result['recovery'],'detected_recovery_failure':result['recovery']=='fail','limitation':'序列号去重是可见报文计数，不保证等于客户端内部连接尝试次数'}
    (folder/'experiment-observations.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('jsonl');a=p.parse_args();rows=[json.loads(l) for l in Path(a.jsonl).read_text().splitlines() if l.strip()];print(json.dumps([dict(policy=r.get('policy'),seed=r.get('seed'),**inspect(r['output'])) for r in rows],ensure_ascii=False,indent=2))
