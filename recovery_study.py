"""持续进程中撤销关联拒绝，分别观察自动恢复与显式恢复。仅新建 hwsim。"""
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from backends.interventions import entries
from backends.sessions import RealSession
from observer.frames import classify_frame, read_pcap_frames
from runner import ROOT, _save
from sequence_study import SequenceLab

WINDOW = 20
CONDITIONS = ('before_keep_ap', 'before_clear_ap', 'after_clear_ap')


def outcome(phases):
    """不因后续成功覆盖初始失败；超时只是固定窗口内的观察。"""
    initial = phases['rejection']['status']
    if initial == 'connected':
        return 'connected_under_policy'
    if phases['release']['status'] == 'connected':
        return 'recovered_after_release'
    if phases.get('explicit', {}).get('status') == 'connected':
        return 'recovered_after_explicit_reconnect'
    return 'not_recovered_within_observed_windows'


def await_recovery(session, cursor, marker, timeout=WINDOW):
    began = next(r.monotonic_s for r in session.lab.event_journal.records_after(0) if r.raw_event==marker)
    try:
        event = session.observer.wait_event('connection_restored', cursor, timeout, session.context)
    except TimeoutError:
        return {'status':'timeout','budget_s':timeout,'started_event':marker,
                'elapsed_s':time.monotonic()-began,'status_after':session.lab.status()}
    probe = session.lab.probe_data_plane()
    if probe['status'] != 'pass':
        raise RuntimeError('restored event but data probe failed')
    return {'status':'connected','budget_s':timeout,'started_event':marker,
            'elapsed_s':event.monotonic_s-began,'event':event.data.get('evidence_ref'),
            'event_seq':event.seq,'probe':probe,'status_after':session.lab.status()}


def analyze_phases(folder, phases):
    """每阶段只统计其真实目标帧；不将多周期输入交给单次认证分类器。"""
    folder=Path(folder)
    journal=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
    marks={r['raw_event']:datetime.fromisoformat(r['received_at']).timestamp()
           for r in journal if r['source']=='runner' and r['raw_event'].startswith('study_')}
    baseline=json.loads((folder/'connected_baseline.json').read_text())['sta']
    pair={baseline['bssid'],baseline['address']}
    frames=read_pcap_frames(folder/'trace.pcap')
    result={}
    for name, phase in phases.items():
        start=marks[phase['started_event']];end=marks[phase['end_event']]
        selected=[r for r in frames if start<r['time']<=end and {r.get('addr1'),r.get('addr2')}==pair]
        result[name]={'stages':sorted({s for r in selected for s in classify_frame(r)}),
                      'reason9_frames':[r['frame'] for r in selected if r.get('reason_code')==9],
                      'association_requests':[{'frame':r['frame'],'pmkids':r.get('rsn_pmkids',[])}
                                              for r in selected if r['type']==0 and r['subtype']==0],
                      'sae_frames':[r['frame'] for r in selected if r.get('auth_algo')==3],
                      'window':{'start':start,'end':end}}
    return {'phases':result,'scope':'frame observations by intervention phase; not full authentication or authorization proof',
            'input_sha256':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest()
                            for name in ('trace.pcap','events.jsonl','connected_baseline.json')}}


def run(condition, output):
    if condition not in CONDITIONS:
        raise ValueError('unknown condition')
    variant='after' if condition.startswith('after') else 'before'
    os.environ['RG_STOCK_WPA_SUPPLICANT']=str(ROOT/'work'/('wpa-'+variant))
    session=RealSession(output,'stock',lab_factory=SequenceLab)
    lab=session.lab;observer=session.observer;ctx=session.context
    phases={};error=None;receipts=[];snapshots=[];release_record=None
    def mark(name):
        session.record_request(name)
        return name
    def command(action):
        receipt=session.executor.execute(action,ctx);receipts.append(asdict(receipt))
        if not receipt.accepted:
            raise RuntimeError('command rejected: '+action)
    def caches(checkpoint):
        data={role:lab.command(role,'PMKSA','recovery_cache_'+checkpoint) for role in ('ap','sta')}
        _save(lab.out/(checkpoint+'-cache.json'),data)
        return data
    try:
        session.start();observer.wait_event('baseline_confirmed',0,WINDOW,ctx)
        snapshots.append(observer.snapshot('connected_baseline'))
        if lab.probe_data_plane()['status']!='pass':
            raise RuntimeError('baseline data probe failed')
        if not all(entries(raw) for raw in caches('baseline').values()):
            raise RuntimeError('baseline cache absent')
        original_processes=[(role,p.pid) for role,p,_ in lab.processes if role in ('ap','sta')]
        cursor=observer.mark();session.record_request('disconnect_requested');command('client_disconnect')
        observer.wait_event('sta_disconnected',cursor,WINDOW,ctx)
        if condition.endswith('clear_ap'):
            if lab.command('ap','PMKSA_FLUSH','recovery_clear_ap')!='OK':
                raise RuntimeError('AP cache clear rejected')
        current=caches('before_rejection')
        if (condition.endswith('clear_ap') and entries(current['ap'])) or not entries(current['sta']):
            raise RuntimeError('cache condition unverified')
        lab.arm();ctx.attempt_id=2
        cursor=observer.mark();marker=mark('study_rejection_start')
        session.record_request('reconnect_requested');command('client_reconnect')
        phases['rejection']=await_recovery(session,cursor,marker)
        phases['rejection']['end_event']=mark('study_rejection_end')
        snapshots.append(observer.snapshot('after_rejection'));caches('after_rejection')
        # 记录控制器停止区间。已连接者不伪称为“撤销干预后恢复”。
        cursor=observer.mark();marker=mark('study_release_start')
        release_record={'started_ns':time.monotonic_ns()}
        lab.allow();release_record['finished_ns']=time.monotonic_ns()
        if phases['rejection']['status']=='timeout':
            # cursor 之后不发 RECONNECT、不清缓存、不重启进程。
            phases['release']=await_recovery(session,cursor,marker)
            phases['release']['end_event']=mark('study_release_end')
            snapshots.append(observer.snapshot('after_release'));caches('after_release')
            if phases['release']['status']=='timeout':
                cursor=observer.mark();marker=mark('study_explicit_start')
                session.record_request('reconnect_requested');command('client_reconnect')
                phases['explicit']=await_recovery(session,cursor,marker)
                phases['explicit']['end_event']=mark('study_explicit_end')
                snapshots.append(observer.snapshot('after_explicit'));caches('after_explicit')
        processes=[(role,p.pid) for role,p,_ in lab.processes if role in ('ap','sta')]
        if processes!=original_processes or any(p.poll() is not None for role,p,_ in lab.processes if role in ('ap','sta')):
            raise RuntimeError('protocol process changed or exited')
        if lab.collection_errors:
            raise RuntimeError('collection errors: '+str(lab.collection_errors))
    except Exception as exc:
        error={'type':type(exc).__name__,'message':str(exc)}
    finally:
        try:cleanup=session.close()
        except Exception as exc:cleanup={'errors':[str(exc)]}
    result={'run_id':ctx.run_id,'condition':condition,'phases':phases,'error':error,'cleanup':cleanup,
            'release_operation':release_record,'outcome':outcome(phases) if error is None else 'not_evaluated',
            'protocol_processes_unchanged':error is None,'observation_budget_s':WINDOW,
            'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                            for name in ('recovery_study.py','sequence_study.py','rejection.py')},
            'security_verdict':'not_evaluated'}
    for name,value in [('result.json',result),('snapshots.json',snapshots),('context.json',asdict(ctx)),
                       ('meta.json',lab.meta),('recovery-actions.json',receipts)]:
        _save(lab.out/name,value)
    if not error:
        try:
            observed=analyze_phases(lab.out,phases);_save(lab.out/'recovery-observations.json',observed)
            if phases['rejection']['status']=='timeout' and not observed['phases']['rejection']['reason9_frames']:
                result['error']={'type':'EvidenceError','message':'timeout without captured reason9 rejection'}
                result['outcome']='not_evaluated'
        except Exception as exc:
            result['error']={'type':'AnalysisError','message':str(exc)};result['outcome']='not_evaluated'
    _save(lab.out/'result.json',result)
    return result,lab.out


def study(output, repeat):
    out=Path(output)/('recovery-'+uuid.uuid4().hex);out.mkdir(parents=True)
    report={'conditions':CONDITIONS,'repeat':repeat,'phase_budget_s':WINDOW,'runs':[]}
    for n in range(repeat):
        for offset in range(len(CONDITIONS)):
            condition=CONDITIONS[(n+offset)%len(CONDITIONS)]
            result,folder=run(condition,out)
            report['runs'].append({'round':n+1,'condition':condition,'output':str(folder),'result':result})
            _save(out/'summary.json',report)
            print(json.dumps({'condition':condition,'run_id':result['run_id'],'outcome':result['outcome'],'error':result['error']},ensure_ascii=False),flush=True)
            if result['error'] or result['cleanup']['errors']:
                raise RuntimeError('invalid experiment; inspect '+str(folder))
    print('OUTPUT='+str(out),flush=True)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repeat',type=int,default=3)
    parser.add_argument('--output',type=Path,default=ROOT/'results')
    args=parser.parse_args()
    if not 1<=args.repeat<=10:parser.error('repeat must be within 1..10')
    study(args.output,args.repeat)
