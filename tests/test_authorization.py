import itertools,unittest
from observer.authorization import monitor,parse


def event(symbol,index):
    common={'ns':index,'rc':0,'ref':str(index)}
    if symbol=='install':return dict(common,kind='key',idx=0,alg=3,len=16)
    if symbol=='delete':return dict(common,kind='key',idx=0,alg=0,len=0)
    if symbol=='failed_install':return dict(common,kind='key',idx=0,alg=3,len=16,rc=-1)
    return dict(common,kind='port',authorized=int(symbol=='authorize'))


class AuthorizationTests(unittest.TestCase):
    def test_missing_failed_and_deleted_keys_never_pass(self):
        for symbols in [('authorize',),('failed_install','authorize'),('install','delete','authorize')]:
            self.assertEqual(monitor([event(s,i) for i,s in enumerate(symbols)])['status'],'fail')
        self.assertEqual(monitor([event('install',0),event('authorize',1)])['status'],'pass')
        self.assertEqual(monitor([])['status'],'insufficient_evidence')
        with self.assertRaises(ValueError):monitor([dict(event('install',0),alg=9)])
    def test_monitor_against_prefix_formula(self):
        alphabet=['install','delete','failed_install','authorize','revoke']
        for length in range(6):
            for symbols in itertools.product(alphabet,repeat=length):
                violations=[]
                for index,s in enumerate(symbols):
                    if s!='authorize':continue
                    keys=[k for k in symbols[:index] if k in ('install','delete')]
                    if not keys or keys[-1]!='install':violations.append(index)
                result=monitor([event(s,i) for i,s in enumerate(symbols)])
                self.assertEqual(result['status']=='fail',bool(violations))
    def test_parser_is_peer_scoped(self):
        text='RG_AUTH t=1.000000001 kind=port rc=0 authorized=1 peer=00:11:22:33:44:55'
        self.assertEqual(parse(text,'ap','00:11:22:33:44:55')[0]['ns'],1000000001)
        self.assertEqual(parse(text,'ap','aa:bb:cc:dd:ee:ff'),[])
    def test_report_requires_matching_instrumented_binary_provenance(self):
        import json,tempfile
        from pathlib import Path
        from observer.authorization import verify
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);ap='00:11:22:33:44:55';sta='66:77:88:99:aa:bb'
            (p/'connected_baseline.json').write_text(json.dumps({'sta':{'bssid':ap,'address':sta}}))
            for role,peer in [('ap',sta),('sta',ap)]:
                (p/(role+'.log')).write_text(f'RG_AUTH t=1.000000001 kind=key rc=0 alg=3 idx=0 len=16 peer={peer}\nRG_AUTH t=1.000000002 kind=port rc=0 authorized=1 peer={peer}\nCTRL-EVENT-TERMINATING\n')
            hashes={'ap':'a'*64,'sta':'b'*64}
            (p/'meta.json').write_text(json.dumps({'binary_sha256':hashes}))
            self.assertEqual(verify(p)['status'],'insufficient_evidence')
            (p/'instrumentation-manifest.json').write_text(json.dumps({'binary_sha256':hashes,'driver_source_sha256':'c'*64}))
            self.assertEqual(verify(p)['status'],'pass')
            (p/'instrumentation-manifest.json').write_text(json.dumps({'binary_sha256':{'ap':'bad','sta':'b'}}))
            self.assertEqual(verify(p)['status'],'insufficient_evidence')
