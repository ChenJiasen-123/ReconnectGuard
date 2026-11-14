import unittest
from observer.postprocess import infer_path

AP='00:11:22:33:44:55'
STA='66:77:88:99:aa:bb'
PMK='01'*16
LOG='''CTRL-EVENT-DISCONNECTED
PMKSA cache entry found - PMKID - hexdump(len=16): %s
matched PMKID - hexdump(len=16): %s
CTRL-EVENT-CONNECTED
''' % ('01 '*16, '01 '*16)


def frames():
    rows=[dict(frame=1,time=1.1,type=0,subtype=0,addr1=AP,addr2=STA,rsn_pmkids=[PMK])]
    for i,(info,source,replay,nonce) in enumerate([(0x88,AP,1,'aa'*32),(0x108,STA,1,'bb'*32),(0x13c8,AP,2,'aa'*32),(0x308,STA,2,'0'*64)],2):
        rows.append(dict(frame=i,time=1+i*.1,type=2,subtype=8,addr1=STA if source==AP else AP,addr2=source,eapol_key_info=info,replay_counter=replay,nonce=nonce))
    return rows


def infer(rows, **kw):
    options=dict(ap=AP,sta=STA,start=1,end=2,log_text=LOG,no_kernel_drops=True)
    options.update(kw)
    return infer_path(rows,**options)


class PostprocessTest(unittest.TestCase):
    def test_cache_reuse_uses_exact_id_and_current_log_window(self):
        r=infer(frames())
        self.assertEqual(r['value'],'pmksa_reuse')
        self.assertEqual(len(r['frame_refs']),5)
        self.assertEqual(len(r['log_refs']),2)
        # 旧连接的匹配日志不能证明本次缓存复用。
        old=LOG.replace('CTRL-EVENT-DISCONNECTED\n','')+'CTRL-EVENT-DISCONNECTED\nCTRL-EVENT-CONNECTED'
        self.assertEqual(infer(frames(),log_text=old)['value'],'unknown')
        self.assertEqual(infer(frames(),log_text=LOG.replace('01 ','02 '))['value'],'unknown')

    def test_wrong_link_window_and_drops_are_not_reuse(self):
        for options in ({'ap':'aa:aa:aa:aa:aa:aa'},{'start':None},{'start':2},{'no_kernel_drops':False}):
            self.assertEqual(infer(frames(),**options)['value'],'unknown')

    def test_handshake_nonce_replay_direction_and_missing_stage(self):
        for field,value in [('nonce','cc'*32),('replay_counter',1),('addr2',STA)]:
            rows=frames();rows[3][field]=value
            self.assertEqual(infer(rows)['value'],'unknown')
        self.assertEqual(infer(frames()[:-1])['value'],'unknown')

    def test_full_sae_requires_both_sides_commit_and_confirm(self):
        rows=frames()
        for n,(source,seq) in enumerate([(AP,1),(STA,1),(AP,2),(STA,2)],10):
            rows.append(dict(frame=n-10,time=1.01+(n-10)*.01,type=0,subtype=11,addr1=STA if source==AP else AP,addr2=source,auth_algo=3,auth_seq=seq))
        self.assertEqual(infer(rows)['value'],'full_sae')
        self.assertEqual(infer(rows[:-1])['value'],'unknown')

    def test_other_network_sae_cannot_override_cache_reuse(self):
        rows=frames()+[dict(frame=100,time=1.11,type=0,subtype=11,addr1='aa',addr2='bb',auth_algo=3,auth_seq=1)]
        self.assertEqual(infer(rows)['value'],'pmksa_reuse')

    def test_analysis_hashes_and_preserves_raw_inputs(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from unittest.mock import patch
        from observer.postprocess import analyze_run
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for name,obj in {'meta.json':{'run_id':'test','simulated':False,'radio':'physical'},'context.json':{'run_id':'test','attempt_id':2},'connected_baseline.json':{'sta':{'bssid':AP,'address':STA}}}.items():
                (p/name).write_text(json.dumps(obj))
            (p/'transitions.jsonl').write_text(json.dumps({'seq':10,'kind':'sta_disconnected','attempt_id':1})+'\n'+json.dumps({'seq':20,'kind':'connection_restored','attempt_id':2})+'\n')
            (p/'events.jsonl').write_text(json.dumps({'seq':10,'source':'sta','received_at':'1970-01-01T00:00:01+00:00'})+'\n'+json.dumps({'seq':20,'source':'sta','received_at':'1970-01-01T00:00:02+00:00'})+'\n')
            (p/'sta.log').write_text(LOG)
            (p/'capture.log').write_text('0 packets dropped by kernel')
            (p/'trace.pcap').write_bytes(b'fixture')
            before={f.name:f.read_bytes() for f in p.iterdir()}
            with patch('observer.postprocess.read_pcap_frames',return_value=frames()):
                report=analyze_run(p)
            self.assertEqual(report['auth_path']['value'],'pmksa_reuse')
            self.assertEqual(report['authorization_timing']['status'],'insufficient_evidence')
            for name,content in before.items():
                self.assertEqual((p/name).read_bytes(),content)
                self.assertEqual(report['source_sha256'][name],hashlib.sha256(content).hexdigest())
