import tempfile
import unittest
from backends.sessions import MockSession
from model.contracts import BackendError, Context
from model.evaluation import CheckResult, evaluate, record_snapshot
from model.metrics import summarize_transitions
from runner import ROOT, load_case, run_case


class EvaluationTest(unittest.TestCase):
    def test_snapshot_failure_keeps_recovery(self):
        for case_id in ('client_disconnect_reconnect', 'ap_deauth'):
            class Session(MockSession):
                def __init__(self, out, scenario, client):
                    super().__init__(out, scenario)
                    original = self.observer.snapshot
                    def snapshot(name):
                        if name in ('after_reconnect', 'connection_restored'):
                            raise BackendError('collection_error', 'missing snapshot')
                        return original(name)
                    self.observer.snapshot = snapshot
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as tmp:
                result, _ = run_case(load_case(ROOT/'cases.yaml', case_id), tmp, session_factory=Session)
                self.assertEqual(result['recovery'], 'pass')
                self.assertEqual(result['security_profile'], 'pass')
                self.assertEqual(result['execution_status'], 'failed')
                self.assertEqual(result['collection_status'], 'failed')

    def test_context_missing_data_does_not_inherit(self):
        context = Context('test')
        record_snapshot(context, {'pmksa_presence': 'present'}, 0)
        record_snapshot(context, {}, 1)
        self.assertEqual(context.pmksa_presence, 'unknown')
        self.assertEqual(context.observations[0]['values']['pmksa_presence'], 'present')
        self.assertIsNone(context.observations[0]['attempt_id'])

    def test_external_checks_and_invalid_evidence(self):
        class Oracle:
            def evaluate(self, **kwargs):
                return [CheckResult('state_consistency', 'pass', ['snapshots.json#/0'])]
        self.assertEqual(evaluate(Oracle(), Context('x'), [{}], [])[0].status, 'pass')
        with self.assertRaises(ValueError):
            evaluate(Oracle(), Context('x'), [], [])
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_case(load_case(ROOT/'cases.yaml', 'ap_deauth'), tmp, evaluator=Oracle())
            self.assertEqual(result['checks'][0]['status'], 'pass')
            self.assertEqual(result['security_verdict'], 'not_evaluated')

    def test_oracle_error_does_not_destroy_results(self):
        class Oracle:
            def evaluate(self, **kwargs):
                raise ValueError('broken oracle')
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_case(load_case(ROOT/'cases.yaml', 'ap_deauth'), tmp, evaluator=Oracle())
            self.assertEqual(result['recovery'], 'pass')
            self.assertEqual(result['error_category'], 'evaluation_error')
            self.assertEqual(result['checks'][0]['status'], 'not_evaluated')

    def test_attempt_durations_do_not_pair_different_retries(self):
        rows = []
        for attempt, kind, time in [(2, 'reconnect_requested', 1), (3, 'reconnect_requested', 5),
                                    (3, 'connection_restored', 6)]:
            rows.append(dict(attempt_id=attempt, kind=kind, monotonic_s=time, event_id=str(time),
                             **{'from': 'RECONNECTING', 'to': 'RECONNECTED'}))
        result = summarize_transitions(rows)
        self.assertIsNone(result['attempts_ms'][0]['reconnect_request_to_restored']['value'])
        self.assertEqual(result['attempts_ms'][1]['reconnect_request_to_restored']['value'], 1000)
        self.assertIsNone(result['timings_ms']['reconnect_request_to_restored']['value'])
