import unittest
from types import SimpleNamespace
from unittest.mock import patch

from recovery_study import await_recovery, outcome


class RecoveryStudyTests(unittest.TestCase):
    def test_phase_analysis_excludes_old_frames_and_other_peers(self):
        import json,tempfile
        from pathlib import Path
        from recovery_study import analyze_phases
        ap='00:11:22:33:44:55';sta='66:77:88:99:aa:bb'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'connected_baseline.json').write_text(json.dumps({'sta':{'bssid':ap,'address':sta}}))
            (p/'trace.pcap').write_bytes(b'fixture')
            (p/'events.jsonl').write_text('\n'.join(json.dumps({'source':'runner','raw_event':name,'received_at':stamp})
                for name,stamp in [('study_start','1970-01-01T00:00:01+00:00'),('study_end','1970-01-01T00:00:02+00:00')]))
            frames=[{'frame':i,'time':stamp,'type':0,'subtype':12,'addr1':peer,'addr2':ap,'reason_code':9}
                    for i,stamp,peer in [(1,.5,sta),(2,1.5,sta),(3,1.5,'ff:ff:ff:ff:ff:ff'),(4,2.5,sta)]]
            with patch('recovery_study.read_pcap_frames',return_value=frames):
                report=analyze_phases(p,{'rejection':{'started_event':'study_start','end_event':'study_end'}})
            self.assertEqual(report['phases']['rejection']['reason9_frames'],[2])

    def test_later_recovery_does_not_erase_initial_timeout(self):
        phases={'rejection':{'status':'timeout'},'release':{'status':'connected'}}
        self.assertEqual(outcome(phases),'recovered_after_release')
        self.assertEqual(phases['rejection']['status'],'timeout')
        phases['release']['status']='timeout'
        self.assertEqual(outcome(phases),'not_recovered_within_observed_windows')
        phases['explicit']={'status':'connected'}
        self.assertEqual(outcome(phases),'recovered_after_explicit_reconnect')
        self.assertEqual(outcome({'rejection':{'status':'connected'}}),'connected_under_policy')

    def test_release_wait_never_sends_commands_or_resets_cache(self):
        marker=SimpleNamespace(monotonic_s=10,raw_event='release')
        event=SimpleNamespace(monotonic_s=11,seq=2,data={'evidence_ref':'events#2'})
        class Lab:
            event_journal=SimpleNamespace(records_after=lambda seq:[marker])
            def status(self):return {'wpa_state':'COMPLETED'}
            def probe_data_plane(self):return {'status':'pass'}
            def command(self,*args):raise AssertionError('no control command allowed')
        session=SimpleNamespace(lab=Lab(),context=object(),observer=SimpleNamespace(wait_event=lambda *a:event))
        self.assertEqual(await_recovery(session,1,'release')['elapsed_s'],1)
        session.lab.probe_data_plane=lambda:{'status':'fail'}
        with self.assertRaises(RuntimeError):await_recovery(session,1,'release')
        session.observer.wait_event=lambda *a:(_ for _ in ()).throw(TimeoutError())
        self.assertEqual(await_recovery(session,1,'release')['status'],'timeout')
        session.observer.wait_event=lambda *a:(_ for _ in ()).throw(RuntimeError('collection error'))
        with self.assertRaises(RuntimeError):await_recovery(session,1,'release')
