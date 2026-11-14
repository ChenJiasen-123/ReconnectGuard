import json
import tempfile
import unittest
from unittest.mock import patch
from backends.sessions import MockSession
from runner import ROOT,run_case,load_case


class Session(MockSession):
    def __init__(self,out,scenario,client):
        super().__init__(out,scenario)


class AnalysisIntegrationTest(unittest.TestCase):
    def test_derived_auth_path_preserves_original_snapshots(self):
        report={'auth_path':{'value':'pmksa_reuse','frame_refs':['trace.pcap#frame=9'],'log_refs':['sta.log#line=8']}}
        with tempfile.TemporaryDirectory() as tmp, patch('observer.postprocess.analyze_run',return_value=report):
            r,out=run_case(load_case(ROOT/'cases.yaml','ap_deauth'),tmp,backend='real',session_factory=Session,analyze=True)
            self.assertEqual(r['auth_path'],'pmksa_reuse')
            self.assertEqual(r['analysis_status'],'completed')
            self.assertEqual(r['security_verdict'],'not_evaluated')
            self.assertTrue(all(x['auth_path']=='unknown' for x in json.loads((out/'snapshots.json').read_text())))

    def test_analysis_failure_keeps_recovery_and_fails_execution(self):
        with tempfile.TemporaryDirectory() as tmp, patch('observer.postprocess.analyze_run',side_effect=RuntimeError('unreadable capture')):
            r,_=run_case(load_case(ROOT/'cases.yaml','ap_deauth'),tmp,backend='real',session_factory=Session,analyze=True)
            self.assertEqual(r['recovery'],'pass')
            self.assertEqual(r['execution_status'],'failed')
            self.assertEqual(r['error_category'],'analysis_error')

    def test_comparison_csv_matches_derived_auth_path(self):
        from comparison import run_comparison
        def run(case,out,**kwargs):
            return run_case(case,out,session_factory=Session,**kwargs)
        report={'auth_path':{'value':'pmksa_reuse','frame_refs':['trace.pcap#frame=9'],'log_refs':['sta.log#line=8']}}
        with tempfile.TemporaryDirectory() as tmp, patch('observer.postprocess.analyze_run',return_value=report):
            batch,out=run_comparison(ROOT/'cases.yaml',tmp,repeat=1,backend='real',analyze=True,run=run)
            self.assertTrue(all(g['auth_path']=='pmksa_reuse' for g in batch['groups']))
            self.assertEqual((out/'comparison.csv').read_text().count('pmksa_reuse'),3)
