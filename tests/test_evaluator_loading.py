import tempfile
import unittest
from unittest.mock import patch
from model.evaluation import CheckResult, load_evaluator
from runner import ROOT, load_case, run_case
from comparison import run_comparison


class CountingOracle:
    def __init__(self):
        self.calls = 0

    def evaluate(self, **kwargs):
        self.calls += 1
        if self.calls != 1:
            raise ValueError('cross-run state leak')
        return [CheckResult('state_consistency', 'insufficient_evidence', reason='test fixture')]


class LoadingTest(unittest.TestCase):
    def test_new_instance_each_batch_run_and_record_identity(self):
        spec = __name__ + ':CountingOracle'
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_comparison(ROOT/'cases.yaml', tmp, repeat=2, evaluator_spec=spec)
            self.assertEqual(report['executed'], 6)
            for row in report['runs']:
                self.assertEqual(row['result']['evaluator'], spec)
                self.assertEqual(row['result']['checks'][0]['status'], 'insufficient_evidence')
                self.assertEqual(row['result']['execution_status'], 'completed')
            self.assertEqual(report['groups'][0]['check_counts']['state_consistency'], {'insufficient_evidence': 2})

    def test_bad_plugin_fails_before_lab_start(self):
        for spec in ('invalid', 'missing_rg_module:Oracle', 'model.evaluation:CheckResult'):
            with self.subTest(spec=spec), patch('runner.RealSession') as factory:
                with self.assertRaises(ValueError):
                    run_case(load_case(ROOT/'cases.yaml', 'ap_deauth'), backend='real', evaluator_spec=spec)
                factory.assert_not_called()

    def test_factory_requires_evaluate_method(self):
        with self.assertRaises(ValueError):
            load_evaluator('builtins:dict')
