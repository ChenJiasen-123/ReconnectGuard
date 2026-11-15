import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from observer.reanalysis import review_batch


class ReanalysisTests(unittest.TestCase):
    def test_new_report_preserves_old_outputs_and_compares_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);batch=base/'batch';run=batch/'run';run.mkdir(parents=True)
            (batch/'summary.json').write_text(json.dumps({'runs':[{'policy':'A','output':'/old/host/run'}]}))
            (run/'authentication-analysis.json').write_text(json.dumps({'auth_path':{'value':'unknown'}}))
            (run/'result.json').write_text(json.dumps({'connection_recovery':'pass','data_plane_recovery':'fail'}))
            (run/'trace.pcap').write_bytes(b'unchanged-pcap')
            before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in run.iterdir()}
            def analyze(source,output):
                self.assertEqual(source,run.resolve())
                report={'auth_path':{'value':'pmksa_reuse','reason':'bound evidence','handshake':{'status':'matched_with_capture_inversion'}}}
                output.parent.mkdir(parents=True);output.write_text(json.dumps(report))
                return report
            with patch('observer.reanalysis.analyze_run',side_effect=analyze):
                report=review_batch(batch,base/'review')
            self.assertEqual(report['old_counts'],{'unknown':1})
            self.assertEqual(report['new_counts'],{'pmksa_reuse':1})
            self.assertEqual(report['runs'][0]['data_plane_recovery'],'fail')
            self.assertTrue(report['original_files_unchanged'])
            self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in run.iterdir()})
            with self.assertRaises(ValueError):review_batch(batch,batch/'review')
            with self.assertRaises(FileExistsError):review_batch(batch,base/'review')
