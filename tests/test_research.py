import unittest
from backends.interventions import apply_cache_policy,retention_check
from model.protocol import ProtocolMonitor,Phase


class CacheLab:
    def __init__(self):self.cache={'ap':True,'sta':True};self.records={};self.ops=[]
    def status(self):return {'wpa_state':'DISCONNECTED'}
    def save(self,name,obj):self.records[name]=obj
    def command(self,target,cmd,*args,**kw):
        self.ops.append((target,cmd))
        if cmd=='PMKSA_FLUSH':self.cache[target]=False;return 'OK'
        return 'Index / MAC\n'+('0 00:11:22:33:44:55 '+('ab'*16)+' 43199 0' if self.cache[target] else '')


class ResearchTest(unittest.TestCase):
    def test_retained_cache_countdown_is_not_an_identity_change(self):
        a='0 00:11:22:33:44:55 '+('ab'*16)+' 43199 0'
        b=a.replace('43199','43198')
        self.assertTrue(retention_check(a,b,.02)['verified'])
        for changed in (b.replace('ab'*16,'cd'*16),b.replace('43198','43200'),
                        b.replace('43198','43190'),b.replace('43198','0'),b[:-1]+'1',b+' extra'):
            self.assertFalse(retention_check(a,changed,.02)['verified'])
    def test_verified_cache_interventions_and_retention(self):
        for policy in 'ABCD':
            lab=CacheLab();r=apply_cache_policy(lab,policy)
            self.assertTrue(r['verified'])
            self.assertEqual(lab.cache,{'ap':policy not in 'BD','sta':policy not in 'CD'})
    def test_reject_connected_intervention(self):
        lab=CacheLab();lab.status=lambda:{'wpa_state':'COMPLETED'}
        with self.assertRaises(RuntimeError):apply_cache_policy(lab,'D')
        self.assertEqual(lab.ops,[])
    def test_unverified_clear_is_not_success(self):
        lab=CacheLab();original=lab.command
        lab.command=lambda target,cmd,*a,**k:'OK' if cmd=='PMKSA_FLUSH' else original(target,cmd,*a,**k)
        with self.assertRaises(RuntimeError):apply_cache_policy(lab,'B')
        self.assertNotIn('verified',lab.records['cache-intervention.json'])
    def test_protocol_context_guards(self):
        m=ProtocolMonitor();self.assertFalse(m.allows('reject_deauth'))
        m.observe(1,11);self.assertEqual(m.phase,Phase.AUTHENTICATING)
        m.observe(2,0);self.assertTrue(m.allows('reject_deauth'));self.assertFalse(m.allows('drop_eapol'))
        m.observe(3,eapol=True);self.assertTrue(m.allows('drop_eapol'))
    def test_cache_command_rejected_is_preserved(self):
        lab=CacheLab();original=lab.command
        lab.command=lambda target,cmd,*a,**k:'FAIL' if cmd=='PMKSA_FLUSH' else original(target,cmd,*a,**k)
        with self.assertRaises(RuntimeError):apply_cache_policy(lab,'D')
        self.assertEqual(lab.records['cache-intervention.json']['operations'][0]['response'],'FAIL')
    def test_injection_rejects_broadcast_and_other_station(self):
        from model.protocol import owned_request
        ap='00:11:22:33:44:55';sta='66:77:88:99:aa:bb'
        frame=bytes.fromhex('00000000'+ap.replace(':','')+sta.replace(':','')+ap.replace(':','')+'0000')
        self.assertTrue(owned_request(frame,ap,sta))
        self.assertFalse(owned_request(frame[:4]+b'\xff'*6+frame[10:],ap,sta))
        self.assertFalse(owned_request(frame[:10]+b'\x00'*6+frame[16:],ap,sta))
        self.assertFalse(owned_request(b'\x00',ap,sta))
