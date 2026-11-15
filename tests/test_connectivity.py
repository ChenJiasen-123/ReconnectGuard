import subprocess
import unittest
from unittest.mock import patch

from rg_platform.connectivity import DiagnosticMixin,followup


class Lab:
    namespaces=['ap-test','sta-test']
    sta_if='wlan2'
    def status(self):
        return {'address':'00:11:22:33:44:55','bssid':'66:77:88:99:aa:bb','wpa_state':'COMPLETED'}
    def command(self,*args,**kwargs):return '[AUTHORIZED]'
    def probe_data_plane(self):return {'status':'fail'}


class ConnectivityTests(unittest.TestCase):
    def test_fixed_budget_reports_partial_loss_and_keeps_primary_failure(self):
        def run(argv,**kwargs):
            return subprocess.CompletedProcess(argv,0,'5 packets transmitted, 4 received\n' if 'ping' in argv else '[]','')
        with patch('rg_platform.connectivity.subprocess.run',side_effect=run) as call:
            result=followup(Lab())
        self.assertEqual(result['status'],'fail')
        self.assertEqual(result['reachability'],'pass')
        self.assertEqual(result['packets_received'],4)
        ping=call.call_args_list[1].args[0]
        self.assertEqual(ping[ping.index('-c')+1],'5')
        self.assertNotIn('-w',ping)
        class Instrumented(DiagnosticMixin,Lab):pass
        with patch('rg_platform.connectivity.followup',return_value={'status':'pass'}):
            self.assertEqual(Instrumented().probe_data_plane()['status'],'fail')

    def test_timeout_missing_summary_and_unreachable_are_distinct(self):
        with patch('rg_platform.connectivity.subprocess.run',side_effect=subprocess.TimeoutExpired('ping',5)):
            self.assertEqual(followup(Lab())['status'],'collection_error')
        for summary,expected in [('unparseable','collection_error'),('5 packets transmitted, 0 received','fail'),
                                 ('6 packets transmitted, 6 received','invalid_budget')]:
            with patch('rg_platform.connectivity.subprocess.run',return_value=subprocess.CompletedProcess([],1,summary,'')):
                self.assertEqual(followup(Lab())['status'],expected)
