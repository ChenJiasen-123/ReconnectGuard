"""隔离 hwsim 中的关联拒绝和阶段报文丢失；复用统一 Runner。"""
import argparse,json,os,re,threading,time,math
from pathlib import Path
from backends.sessions import RealSession
from rg_platform.lab import Lab
from runner import ROOT,load_case,run_case,_save


class FaultLab(Lab):
    def __init__(self,mode,*,output_root=None,fault='accept',duration=.2,policy='guided',seed=0):
        super().__init__(mode,output_root=output_root)
        self.fault,self.duration=fault,duration
        self.injections=[]
        self.armed=False
        from model.protocol import ProtocolMonitor
        import random
        self.monitor=ProtocolMonitor()
        self.policy=policy
        self.trigger_index=2 if policy=='fixed' else random.Random(seed).randint(1,6)
        self.active=policy=='guided'
        self.candidates=0

    def on_radios_created(self):
        from rg_platform.lab import run
        for entry in Path('/sys/class/rfkill').glob('rfkill*'):
            try:
                name=(entry/'name').read_text().strip()
            except FileNotFoundError:
                continue
            if name in self.phys:
                run('rfkill','unblock',entry.name[6:])

    def snapshot(self,checkpoint):
        result=super().snapshot(checkpoint)
        if checkpoint=='connected_baseline':
            self.target_ap=result['sta']['bssid']
            self.target_sta=result['sta']['address']
        if checkpoint=='after_disconnect' and not self.armed:
            self.armed=True
            if self.fault in ('reject_assoc','reject_deauth'):
                self.command('ap','PMKSA_FLUSH','fault_flush')
            setting='ext_eapol_frame_io' if self.fault=='drop_eapol' else 'ext_mgmt_frame_handling'
            self.command('ap','SET '+setting+' 1','fault_arm')
            cursor=self.event_journal.mark()
            t=threading.Thread(target=self.control,args=(cursor,setting),daemon=True)
            t.start();self.threads.append(t)
        return result

    def control(self,cursor,setting):
        first=None
        try:
            while not self.stop_event.is_set():
                for record in self.event_journal.wait_after(cursor,.05):
                    cursor=record.seq;raw=record.raw_event
                    if record.source!='ap':continue
                    status=re.search(r'MGMT-TX-STATUS (.*)',raw)
                    if status:
                        self.command('ap','MGMT_TX_STATUS_PROCESS '+status.group(1),'forward_tx_status')
                        continue
                    if setting=='ext_eapol_frame_io':
                        if 'EAPOL-TX ' not in raw:continue
                        subtype=-1;buf=None
                    else:
                        m=re.search(r'MGMT-RX ([0-9a-fA-F]+)',raw)
                        if not m:continue
                        buf=bytes.fromhex(m.group(1));subtype=(buf[0]>>4)&15
                        if len(buf)<24:continue
                        from model.protocol import owned_request
                        if not owned_request(buf,self.target_ap,self.target_sta):
                            self.command('ap','MGMT_RX_PROCESS freq=2412 datarate=0 ssi_signal=-30 frame='+buf.hex(),'forward_other_mgmt')
                            continue
                    self.monitor.observe(record.seq,subtype,eapol=subtype==-1)
                    self.save('protocol-phases.json',self.monitor.history)
                    if subtype in (0,11):
                        self.candidates+=1
                        if self.candidates==self.trigger_index:self.active=True
                    if not self.active:
                        if buf:self.command('ap','MGMT_RX_PROCESS freq=2412 datarate=0 ssi_signal=-30 frame='+buf.hex(),'unguided_forward')
                        continue
                    if self.fault.startswith('drop_'):
                        relevant=self.monitor.allows(self.fault)
                        if relevant and first is None:first=time.monotonic()
                        if relevant:
                            self.injections.append({'seq':record.seq,'stage':'eapol_tx' if subtype==-1 else 'association','action':'drop','monotonic_s':time.monotonic()})
                            self.save('fault-injection.json',self.injections)
                            if time.monotonic()-first < self.duration:continue
                            self.command('ap','SET '+setting+' 0','fault_release')
                            return
                    elif self.fault=='reject_deauth' and self.monitor.allows(self.fault):
                        mac=self.status()['address'];self.command('ap','DEAUTHENTICATE '+mac+' reason=9','reject_cached_association')
                        self.injections.append({'seq':record.seq,'stage':'association','action':'deauth_reason9'})
                        self.save('fault-injection.json',self.injections);continue
                    elif self.fault=='reject_assoc' and self.monitor.allows(self.fault):
                        from scapy.all import Dot11,Dot11AssoResp
                        incoming=Dot11(buf);response=Dot11(type=0,subtype=1,addr1=incoming.addr2,addr2=incoming.addr1,addr3=incoming.addr3)/Dot11AssoResp(cap=0x431,status=53,AID=0)
                        self.command('ap','MGMT_TX '+bytes(response).hex(),'reject_cached_association')
                        self.injections.append({'seq':record.seq,'stage':'association','action':'assoc_status53'})
                        self.save('fault-injection.json',self.injections);continue
                    if subtype==11 and int.from_bytes(buf[24:26],'little')==3 and self.fault.startswith('reject_'):
                        self.injections.append({'seq':record.seq,'stage':'sae','action':'fallback_seen'})
                        self.save('fault-injection.json',self.injections)
                        self.command('ap','MGMT_RX_PROCESS freq=2412 datarate=0 ssi_signal=-30 frame='+buf.hex(),'forward_sae')
                        self.command('ap','SET '+setting+' 0','allow_sae_fallback')
                        return
                    if buf:
                        self.command('ap','MGMT_RX_PROCESS freq=2412 datarate=0 ssi_signal=-30 frame='+buf.hex(),'forward_mgmt')
                if self.fault.startswith('drop_') and first is not None and time.monotonic()-first>=self.duration:
                    self.command('ap','SET '+setting+' 0','fault_release')
                    return
        except Exception as exc:
            self.collection_errors.append('fault controller: '+str(exc))
        finally:
            self.save('fault-injection.json',self.injections)
            try:self.command('ap','SET '+setting+' 0','fault_reset')
            except Exception:pass


def main():
    p=argparse.ArgumentParser();p.add_argument('--fault',choices=['accept','reject_assoc','reject_deauth','drop_assoc','drop_eapol'],default='accept');p.add_argument('--duration',type=float,default=.2);p.add_argument('--repeat',type=int,default=1);p.add_argument('--output',type=Path,default=ROOT/'results');p.add_argument('--policy',choices=['fixed','unguided','guided'],default='guided');p.add_argument('--seed',type=int,default=0);a=p.parse_args()
    if not math.isfinite(a.duration) or not 0<=a.duration<=10 or a.repeat<1:p.error('duration must be finite within 0..10 seconds; repeat must be positive')
    os.environ.setdefault('RG_STOCK_WPA_SUPPLICANT','/usr/sbin/wpa_supplicant')
    def session(out,scenario,client):return RealSession(out,client,lab_factory=lambda mode,**kw:FaultLab(mode,fault=a.fault,duration=a.duration,policy=a.policy,seed=a.seed,**kw))
    rows=[]
    for _ in range(a.repeat):
        result,out=run_case(load_case(ROOT/'cases.yaml','client_disconnect_reconnect'),a.output,backend='real',session_factory=session,evaluator_spec='observer.oracle:Oracle',analyze=True)
        rows.append({'policy':a.policy,'seed':a.seed,'fault':a.fault,'duration':a.duration,'result':result,'output':str(out.resolve())})
        print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
        if result['cleanup_status']=='failed':break
    return 1 if any(r['result']['execution_status']=='failed' and r['result']['error_category']!='observation_timeout' for r in rows) else 0


if __name__=='__main__':raise SystemExit(main())
