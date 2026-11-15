"""目标链路、恢复窗口内的派生认证证据；不修改原始快照或事件。

支持单轮目录或 comparison 批次目录。报告保留输入哈希、帧号和日志行号。
"""
import argparse
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
from collections import Counter

from .frames import read_pcap_frames, classify_frame
from .handshake import log_window, match_handshake


def _stamp(value):
    return datetime.fromisoformat(value).timestamp()


def _log_window(text):
    return log_window(text, 'CTRL-EVENT-DISCONNECTED', 'CTRL-EVENT-CONNECTED')


def infer_path(rows, *, ap, sta, start, end, log_text, no_kernel_drops, ap_log_text=""):
    """只接受目标 MAC 对和唯一的恢复窗口；缺阶段/边界时保留 unknown。"""
    verdict = {'value':'unknown', 'reason':'缺少可核实的恢复窗口或目标身份',
               'frame_refs':[], 'log_refs':[]}
    if not ap or not sta or start is None or end is None or start >= end:
        return verdict
    ap, sta = ap.lower(), sta.lower()
    selected = [r for r in rows if start < r['time'] <= end and
                {str(r.get('addr1')).lower(), str(r.get('addr2')).lower()} == {ap, sta}]
    selected.sort(key=lambda r: (r['time'], r['frame']))
    verdict['target_window_frames'] = len(selected)
    handshake, diagnostic = match_handshake(selected, ap, sta, log_text, ap_log_text, no_kernel_drops)
    verdict['handshake'] = diagnostic
    if handshake is None:
        verdict['reason'] = '四次握手证据未满足匹配条件，详见 handshake.reason'
        return verdict
    corroboration = diagnostic.get('endpoint_corroboration', {})
    verdict['log_refs'] = list(corroboration.get('log_refs', []))
    verdict['evidence_basis'] = ('pcap_identity_and_endpoint_logs' if not diagnostic['strict_capture_order']
                                 else 'pcap_identity_and_capture_order')
    sae=[r for r in selected if 'sae_commit' in classify_frame(r) or 'sae_confirm' in classify_frame(r)]
    complete_sae=[]
    for source in (ap,sta):
        commit=next((r for r in sae if r.get('addr2','').lower()==source and r.get('auth_seq')==1 and r.get('auth_status')==0 and not r.get('bad_fcs') and r['time']<handshake[0]['time']),None)
        confirm=next((r for r in sae if commit and r.get('addr2','').lower()==source and r.get('auth_seq')==2 and r.get('auth_status')==0 and not r.get('bad_fcs') and commit['time']<r['time']<handshake[0]['time']),None)
        if commit and confirm:
            complete_sae.extend([commit,confirm])
    refs=lambda frames: ['trace.pcap#frame='+str(r['frame']) for r in frames]
    verdict['frame_refs']=refs(handshake)
    if len(complete_sae)==4:
        verdict.update(value='full_sae',reason='恢复窗口内捕获双方 SAE commit/confirm，匹配同一四次握手；抓包顺序质量另见 handshake',frame_refs=refs(complete_sae+handshake))
        return verdict
    window=_log_window(log_text)
    cache_ids, matched_ids = {}, {}
    for number,line in window:
        match=re.search(r'hexdump\(len=16\):\s*((?:[0-9a-fA-F]{2}\s*){16})\s*$',line)
        if not match:
            continue
        identifier=''.join(match.group(1).split()).lower()
        if 'PMKSA cache entry found' in line:
            cache_ids[identifier]=number
        if 'matched PMKID' in line:
            matched_ids[identifier]=number
    for assoc in selected:
        if assoc.get('type')!=0 or assoc.get('subtype')!=0 or assoc.get('addr2','').lower()!=sta or assoc['time']>=handshake[0]['time']:
            continue
        shared=set(assoc.get('rsn_pmkids',[])) & cache_ids.keys() & matched_ids.keys()
        if shared and not sae and no_kernel_drops:
            identifier=sorted(shared)[0]
            verdict.update(value='pmksa_reuse', reason='本次关联 PMKID 与恢复窗口内缓存命中及匹配日志一致，四次握手完整，窗口内未见 SAE；仍受固件采集可见性限制',
                           frame_refs=refs([assoc]+handshake),
                           log_refs=verdict['log_refs']+['sta.log#line='+str(n) for n in [cache_ids[identifier], matched_ids[identifier]]])
            return verdict
    verdict['reason']='已见恢复四次握手，但 SAE 或 PMKID/缓存日志互证不足，或抓包报告丢包'
    return verdict


def analyze_run(folder, *, output=None):
    folder=Path(folder)
    inputs=['meta.json','context.json','connected_baseline.json','events.jsonl','transitions.jsonl','sta.log','capture.log','trace.pcap']
    hashes={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in inputs}
    if (folder/'ap.log').exists():
        hashes['ap.log']=hashlib.sha256((folder/'ap.log').read_bytes()).hexdigest()
    meta=json.loads((folder/'meta.json').read_text())
    context=json.loads((folder/'context.json').read_text())
    if meta.get('simulated') is not False or meta.get('run_id') != context.get('run_id'):
        raise ValueError('仅接受同一 run_id 的真实协议证据')
    transitions=[json.loads(line) for line in (folder/'transitions.jsonl').read_text().splitlines()]
    journal=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
    boundaries={}
    for kind in ('sta_disconnected','connection_restored'):
        accepted=[t for t in transitions if t.get('kind')==kind]
        raw=[r for r in journal if len(accepted)==1 and r['seq']==accepted[0]['seq'] and r.get('source')=='sta']
        boundaries[kind]=_stamp(raw[0]['received_at']) if len(raw)==1 else None
    restored=[t for t in transitions if t.get('kind')=='connection_restored']
    if (len(restored)!=1 or restored[0].get('attempt_id')!=context.get('attempt_id') or
            sum(t.get('kind')=='reconnect_requested' for t in transitions)>1):
        boundaries={key:None for key in boundaries}
    baseline=json.loads((folder/'connected_baseline.json').read_text())['sta']
    rows=read_pcap_frames(folder/'trace.pcap')
    path=infer_path(rows,ap=baseline.get('bssid'),sta=baseline.get('address'),
                    start=boundaries['sta_disconnected'],end=boundaries['connection_restored'],
                    log_text=(folder/'sta.log').read_text(errors='replace'),
                    ap_log_text=(folder/'ap.log').read_text(errors='replace') if (folder/'ap.log').exists() else '',
                    no_kernel_drops=bool(re.search(r'\b0 packets dropped by kernel', (folder/'capture.log').read_text())))
    report={'schema_version':2,'kind':'derived_authentication_evidence','run_id':context['run_id'],
            'attempt_id':context['attempt_id'],'radio':meta.get('radio','hwsim'),
            'source_sha256':hashes,
            'analyzer_sha256':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ('postprocess.py','frames.py','handshake.py')},
            'window':boundaries,'auth_path':path,
            'authorization_timing':{'status':'insufficient_evidence','reason':'未获取可信的密钥安装与端口授权先后证据；握手和通信恢复不能证明不存在提前授权'},
            'security_verdict':'not_evaluated',
            'limitations':['时间窗口使用本机控制事件接收时间，边界附近的空口帧可能被保守排除',
                           'TX 状态回报和 RX 记录混合，pcap 时间不保证是空口顺序；倒序保留，不重写时间戳',
                           '未验证握手 MIC；分类是观测路径证据，不是密码学安全证明',
                           '内核丢包为零不能证明固件交付了全部空口报文']}
    target=Path(output) if output is not None else folder/'authentication-analysis.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def analyze(folder):
    folder=Path(folder)
    if not (folder/'summary.json').exists():
        return analyze_run(folder)
    summary=json.loads((folder/'summary.json').read_text())
    rows=[]
    for item in summary['runs']:
        try:
            report=analyze_run(item['output'])
            rows.append({'case_id':item['case_id'],'run_id':report['run_id'],'auth_path':report['auth_path']['value'],'output':str(Path(item['output'])/'authentication-analysis.json')})
        except Exception as exc:
            rows.append({'case_id':item['case_id'],'auth_path':'unknown','error':str(exc)})
    report={'schema_version':1,'batch':str(folder),'runs':rows,
            'groups':{name:dict(Counter(r['auth_path'] for r in rows if r['case_id']==name)) for name in {r['case_id'] for r in rows}},
            'errors':sum('error' in r for r in rows),'security_verdict':'not_evaluated'}
    (folder/'authentication-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=['case_id','run_id','auth_path','output','error'])
    writer.writeheader();writer.writerows(rows)
    (folder/'authentication-summary.csv').write_text(stream.getvalue())
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    report=analyze(args.directory)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 1 if report.get('errors') else 0


if __name__=='__main__':
    raise SystemExit(main())
