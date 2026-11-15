"""多动作真实重放、有限模型最短反例及相同提案预算的策略比较。"""
import argparse,json,os,random,time,uuid,threading,hashlib
from pathlib import Path
from dataclasses import asdict
from backends.sessions import RealSession
from model.sequences import ACTIONS,validate,enumerate_plans,shortest_certificate
from rejection import FaultLab
from rg_platform.lab import Lab
from runner import ROOT,_save


class SequenceLab(FaultLab):
    def snapshot(self,name):
        value=Lab.snapshot(self,name)
        if name=='connected_baseline':self.target_ap=value['sta']['bssid'];self.target_sta=value['sta']['address']
        return value
    def allow(self):
        self.controller_stop.set()
        if hasattr(self,'controller'):
            self.controller.join(5)
            if self.controller.is_alive():raise RuntimeError('controller shutdown timeout')
        self.command('ap','SET ext_mgmt_frame_handling 0','sequence_allow')
    def arm(self):
        self.allow();self.controller_stop.clear();self.fault='reject_deauth';self.active=True
        self.command('ap','SET ext_mgmt_frame_handling 1','sequence_arm')
        self.controller=threading.Thread(target=self.control,args=(self.event_journal.mark(),'ext_mgmt_frame_handling'),daemon=True)
        self.controller.start();self.threads.append(self.controller)


def replay(plan,output):
    validate(plan)
    session=RealSession(output,'stock',lab_factory=SequenceLab);lab=session.lab;ctx=session.context
    observer=session.observer;snapshots=[];ops=[];failure=None;data=[];faultarmed=False;began=time.monotonic()
    try:
        session.start();observer.wait_event('baseline_confirmed',0,20,ctx)
        snapshots.append(observer.snapshot('connected_baseline'))
        baseline=lab.probe_data_plane()
        if baseline['status']!='pass':raise RuntimeError('baseline communication failure')
        # 初始缓存必须存在，不能把未知当成已缓存。
        from backends.interventions import entries
        for target in ('ap','sta'):
            if not entries(lab.command(target,'PMKSA','sequence_baseline_cache')):raise RuntimeError('baseline cache missing')
        for index,action in enumerate(plan):
            cursor=observer.mark();record={'index':index,'action':action,'started_s':time.monotonic()};ops.append(record)
            if action in ('disconnect','reconnect'):
                if action=='reconnect':
                    ctx.attempt_id+=1
                    if faultarmed and not getattr(lab,'controller',threading.Thread()).is_alive():lab.arm()
                session.record_request('disconnect_requested' if action=='disconnect' else 'reconnect_requested')
                receipt=session.executor.execute('client_'+action,ctx)
                if not receipt.accepted:raise RuntimeError('action rejected')
                event=observer.wait_event('sta_disconnected' if action=='disconnect' else 'connection_restored',cursor,20,ctx)
                record['event_id']=event.event_id
                if action=='reconnect':
                    probe=lab.probe_data_plane();data.append(probe);lab.save(f'probe-{index}.json',probe)
                    if probe['status']!='pass':raise RuntimeError('data probe failed')
            elif action.startswith('flush_'):
                target='ap' if action=='flush_ap' else 'sta'
                record['before']=lab.command(target,'PMKSA','sequence_cache_before')
                lab.command(target,'PMKSA_FLUSH','sequence_cache_flush')
                record['after']=lab.command(target,'PMKSA','sequence_cache_after')
                if entries(record['after']):raise RuntimeError('cache flush unverified')
            elif action=='arm_reject':
                lab.arm();faultarmed=True
            elif action=='allow':
                lab.allow();faultarmed=False
            record['finished_s']=time.monotonic();snapshots.append(observer.snapshot('sequence_'+str(index)))
    except Exception as exc:
        failure={'category':'recovery_timeout' if isinstance(exc,TimeoutError) else 'environment_or_action_error','message':str(exc)}
    finally:
        try:cleanup=session.close()
        except Exception as exc:cleanup={'errors':['session close: '+str(exc)]}
    result={'run_id':ctx.run_id,'plan':plan,'failure':failure,'recovery':'fail' if failure and failure['category']=='recovery_timeout' else 'not_evaluated' if failure else 'pass',
            'cleanup':cleanup,'elapsed_s':time.monotonic()-began,'operations':ops,'data_probes_pass':sum(p['status']=='pass' for p in data)}
    for name,value in [('result.json',result),('snapshots.json',snapshots),('context.json',asdict(ctx)),('meta.json',lab.meta)]:_save(lab.out/name,value)
    # 公用离线观测器读取真实抓包，而非生成器的边。
    try:
        from observer.experiment import inspect
        result['independent_observation']=inspect(lab.out)
    except Exception as exc:result['observation_error']=str(exc)
    _save(lab.out/'result.json',result)
    return result,lab.out


def metrics(rows):
    valid=[r for r in rows if r.get('valid')];stages=set();elapsed=0;first=None
    def confirmed(result):
        observed=result.get('independent_observation',{})
        reasons=observed.get('rejection_reason_codes',[r.get('reason_code') for r in observed.get('timeline',[])])
        return bool(observed.get('detected_recovery_failure') and observed.get('effective_fault') and
                    9 in reasons)
    for r in valid:stages.update(r['result'].get('independent_observation',{}).get('stages') or [])
    for proposal,r in enumerate(rows,1):
        if not r.get('valid'):continue
        elapsed+=r['result']['elapsed_s']
        if first is None and confirmed(r['result']):
            first={'proposal':proposal,'sum_run_elapsed_s':elapsed}
    return {'proposals':len(rows),'legal':len(valid),'recovery_failures':sum(r['result']['recovery']=='fail' for r in valid),
            'confirmed_rejection_failure_sequences':sum(confirmed(r['result']) for r in valid),
            'first_detection':first,
            'attempted_operations':sum(len(r['result']['operations']) for r in valid),
            'completed_operations':sum(sum('finished_s' in op for op in r['result']['operations']) for r in valid),
            'independent_stages':sorted(stages),'elapsed_s':sum(r['result']['elapsed_s'] for r in valid),
            'environment_or_action_errors':sum(bool(r['result']['failure'] and r['result']['failure']['category']!='recovery_timeout') for r in valid),
            'observation_errors':sum(bool(r['result'].get('observation_error')) for r in valid),
            'cleanup_errors':sum(bool(r['result']['cleanup']['errors']) for r in valid)}


def study(mode,output,budget=6,depth=6,seed=1):
    if mode not in ('shortest','fixed','random','random-valid','guided'):raise ValueError('unknown mode')
    if not 2<=depth<=6 or not 1<=budget<=30:raise ValueError('depth 2..6, budget 1..30')
    if mode=='shortest' and depth<3:raise ValueError('shortest candidate requires depth >= 3')
    out=Path(output)/('sequences-'+mode+'-'+uuid.uuid4().hex);out.mkdir(parents=True)
    certificate=shortest_certificate(max(3,depth));_save(out/'domain.json',certificate)
    settings={'mode':mode,'budget':budget,'max_depth':depth,'seed':seed,'budget_unit':'proposed sequences; actual operations and wall time reported separately',
              'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('sequence_study.py','model/sequences.py','rejection.py','observer/experiment.py')}}
    rows=[];seen=set();rng=random.Random(seed)
    catalog=[['disconnect','reconnect'],['disconnect','flush_ap','reconnect'],['disconnect','flush_sta','reconnect'],['disconnect','flush_ap','flush_sta','reconnect'],['disconnect','arm_reject','reconnect'],['disconnect','flush_ap','arm_reject','reconnect']]
    pool=list(enumerate_plans(depth))
    if mode=='shortest':plans=certificate['strictly_shorter_terminal_plans']+[certificate['shortest']]*2
    else:plans=[None]*budget
    settings['budget']=len(plans)
    def save():_save(out/'summary.json',dict(settings,runs=rows,metrics=metrics(rows)))
    for trial,provided in enumerate(plans):
        if provided is not None:plan=provided
        elif mode=='fixed':plan=catalog[trial%len(catalog)]
        elif mode=='random':
            plan=list(catalog[rng.randrange(len(catalog))]);position=rng.randrange(len(plan));plan[position]=rng.choice(ACTIONS)
        elif mode=='random-valid':plan=list(rng.choice(pool))
        else:
            unused=[p for p in pool if p not in [r.get('plan') for r in rows]]
            if not unused:settings['stop_reason']='legal_pool_exhausted';break
            plan=max(unused,key=lambda p:(len(set(validate(p))-seen),-len(p),tuple(p)))
        row={'trial':trial,'plan':plan,'mode':mode}
        try:edges=validate(plan,depth)
        except ValueError as exc:row.update(valid=False,error=str(exc));rows.append(row);save();continue
        result,folder=replay(plan,out);row.update(valid=True,result=result,output=str(folder));rows.append(row);seen.update(edges[:sum('finished_s' in op for op in result['operations'])])
        save()
        print(json.dumps({'mode':mode,'plan':plan,'recovery':result['recovery'],'error':result['failure']},ensure_ascii=False),flush=True)
        if result['cleanup']['errors'] or result['failure'] and result['failure']['category']!='recovery_timeout':break
    if mode=='shortest':
        certificate['real_validation']=[{'plan':r['plan'],'recovery':r.get('result',{}).get('recovery'),'output':r.get('output')} for r in rows]
        expected=certificate['strictly_shorter_terminal_plans']
        certificate['all_shorter_observed_pass']=all(any(r['plan']==plan and r.get('result',{}).get('recovery')=='pass' for r in rows) for plan in expected)
        certificate['candidate_reproduced']=sum(r.get('result',{}).get('recovery')=='fail' for r in rows if r['plan']==certificate['shortest'])==2
        _save(out/'certificate.json',certificate)
    save()
    print('OUTPUT='+str(out),flush=True)
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['shortest','fixed','random','random-valid','guided'],required=True);p.add_argument('--budget',type=int,default=6);p.add_argument('--depth',type=int,default=6);p.add_argument('--seed',type=int,default=1);p.add_argument('--output',type=Path,default=ROOT/'results');a=p.parse_args()
    if not 2<=a.depth<=6 or not 1<=a.budget<=30:p.error('depth 2..6, budget 1..30')
    if a.mode=='shortest' and a.depth<3:p.error('shortest candidate requires depth >= 3')
    os.environ.setdefault('RG_STOCK_WPA_SUPPLICANT',str(ROOT/'work/wpa-before'))
    out=study(a.mode,a.output,a.budget,a.depth,a.seed)
    measured=json.loads((out/'summary.json').read_text())['metrics']
    if measured['environment_or_action_errors'] or measured['cleanup_errors'] or measured['observation_errors']:raise SystemExit(1)
