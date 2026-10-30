import json
import tempfile
import unittest
from pathlib import Path
from comparison import run_comparison, CASE_IDS
from model.contracts import BackendError
from runner import ROOT, run_case, load_case


class ComparisonTest(unittest.TestCase):
    def test_round_rotation_coverage_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, out = run_comparison(ROOT / 'cases.yaml', tmp, repeat=3)
            self.assertEqual(report['executed'], 9)
            self.assertEqual([r['case_id'] for r in report['runs'][::3]], list(CASE_IDS))
            self.assertEqual(report['model_coverage']['total'], 6)
            self.assertEqual(report['model_coverage']['missing'], [])
            self.assertTrue(report['simulated'])
            self.assertEqual(json.loads((out/'summary.json').read_text()), report)
            self.assertEqual(len((out/'comparison.csv').read_text().splitlines()), 4)
            for group in report['groups']:
                self.assertEqual(group['completed'], 3)
                self.assertEqual(group['timing_samples'], 3)
            for row in report['runs']:
                timing = row['result']['timings_ms']['disconnect_observed_to_restored']
                self.assertEqual(len(timing['evidence']), 2)
                expected = 20 if row['case_id'] == CASE_IDS[0] else 10
                self.assertEqual(timing['value'], expected)

    def test_timeout_continues_but_has_no_timing_sample(self):
        def failing(case, out, **kwargs):
            return run_case(case, out, scenario='reconnect_timeout', **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_comparison(ROOT/'cases.yaml', tmp, 1, run=failing)
            self.assertEqual(report['executed'], 3)
            self.assertIsNone(report['stop_reason'])
            for group in report['groups']:
                self.assertEqual(group['recovered'], 0)
                self.assertEqual(group['timing_samples'], 0)
                self.assertIsNone(group['observed_recovery_median_ms'])
                self.assertEqual(group['errors'], {'observation_timeout': 1})

    def test_cleanup_failure_stops_and_preserves_completed_recovery(self):
        def failing(case, out, **kwargs):
            result, path = run_case(case, out, **kwargs)
            result.update(cleanup_status='failed', error_category='cleanup_error', execution_status='failed')
            return result, path
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_comparison(ROOT/'cases.yaml', tmp, 10, run=failing)
            self.assertEqual(report['executed'], 1)
            self.assertEqual(report['stop_reason'], 'cleanup_error')
            self.assertEqual(report['groups'][0]['completed'], 0)
            self.assertEqual(report['groups'][0]['recovered'], 1)

    def test_setup_failure_and_interrupt_preserve_summary(self):
        for error in (BackendError('environment_error', 'no lab'), KeyboardInterrupt()):
            def failing(*args, **kwargs):
                raise error
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                report, out = run_comparison(ROOT/'cases.yaml', tmp, 2, run=failing)
                self.assertEqual(report['executed'], 1)
                self.assertIsNotNone(report['stop_reason'])
                self.assertEqual(json.loads((out/'summary.json').read_text()), report)

    def test_inconsistent_budgets_rejected_before_session(self):
        document = json.loads((ROOT/'cases.yaml').read_text())
        document['cases'][1]['steps'][3]['timeout_s'] = 99
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cases.json'
            path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                run_comparison(path, tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [path])
