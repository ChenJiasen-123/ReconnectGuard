import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backends.sessions import MockSession
from model.contracts import BackendError
from runner import ROOT, load_case, run_case
from comparison import run_comparison


class RetryIntegrationTest(unittest.TestCase):
    def case(self, budget=1):
        case = load_case(ROOT/'cases.yaml', 'client_disconnect_reconnect')
        case['recovery_policy'] = {'max_retries': budget}
        return case

    def test_timeout_then_success_keeps_failed_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, out = run_case(self.case(), tmp, 'reconnect_once_timeout')
            self.assertEqual(result['recovery'], 'pass')
            self.assertEqual(result['retries_used'], 1)
            self.assertEqual([(a['attempt_id'], a['outcome']) for a in result['recovery_attempts']], [(2, 'fail'), (3, 'pass')])
            self.assertEqual(json.loads((out/'recovery-attempts.json').read_text()), result['recovery_attempts'])
            self.assertIsNone(result['timings_ms']['reconnect_request_to_restored']['value'])
            self.assertEqual(result['attempts_ms'][-1]['reconnect_request_to_restored']['value'], 10)
            self.assertIn('FAILURE_HANDLING->RECONNECTING', result['covered_transitions'])

    def test_budget_exhaustion_and_default_no_retry(self):
        for budget in (0, 1, 3):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as tmp:
                result, _ = run_case(self.case(budget), tmp, 'reconnect_timeout')
                self.assertEqual(result['recovery'], 'fail')
                self.assertEqual(result['retries_used'], budget)
                self.assertEqual(len(result['recovery_attempts']), budget + 1)
                self.assertEqual(result['state'], 'FAILED')

    def test_real_and_invalid_policy_rejected_before_creation(self):
        for backend, budget in [('real', 1), ('mock', True), ('mock', -1), ('mock', 4)]:
            with self.subTest(backend=backend, budget=budget), patch('runner.RealSession') as factory:
                with self.assertRaises(ValueError):
                    run_case(self.case(budget), backend=backend)
                factory.assert_not_called()

    def test_collection_and_action_timeouts_do_not_retry(self):
        for failure in ('action', 'snapshot'):
            class Session(MockSession):
                def __init__(self, out, scenario, client):
                    super().__init__(out, scenario)
                    original = self.executor.execute
                    def execute(action, context):
                        if failure == 'action' and action == 'client_reconnect':
                            raise TimeoutError('command delivery unknown')
                        return original(action, context)
                    self.executor.execute = execute
                    snapshot = self.observer.snapshot
                    def capture(name):
                        if failure == 'snapshot' and name == 'recovery_timeout':
                            raise BackendError('collection_error', 'missing evidence')
                        return snapshot(name)
                    self.observer.snapshot = capture
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                result, _ = run_case(self.case(), tmp, 'reconnect_timeout', session_factory=Session)
                self.assertEqual(result['retries_used'], 0)
                self.assertEqual(result['execution_status'], 'failed')

    def test_standard_comparison_refuses_retry(self):
        document = json.loads((ROOT/'cases.yaml').read_text())
        document['cases'][0]['recovery_policy'] = {'max_retries': 1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cases.json'
            path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                run_comparison(path, tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_late_old_attempt_cannot_complete_retry(self):
        from backends.mock import PROFILE
        class Session(MockSession):
            def __init__(self, out, scenario, client):
                super().__init__(out, scenario)
                execute = self.executor.execute
                def wrapped(action, context):
                    receipt = execute(action, context)
                    if action == 'client_reconnect' and context.attempt_id == 3:
                        self.backend.emit('connection_restored', data=PROFILE.copy(), attempt_id=2)
                    return receipt
                self.executor.execute = wrapped
        with tempfile.TemporaryDirectory() as tmp:
            result, out = run_case(self.case(), tmp, 'reconnect_timeout', session_factory=Session)
            self.assertEqual(result['recovery'], 'fail')
            self.assertIn('wrong_context', (out/'observations.json').read_text())

    def test_cleanup_error_keeps_retry_success(self):
        class Session(MockSession):
            def __init__(self, out, scenario, client):
                super().__init__(out, scenario)
            def close(self):
                super().close()
                return {'errors': ['cleanup failed']}
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_case(self.case(), tmp, 'reconnect_once_timeout', session_factory=Session)
            self.assertEqual(result['recovery'], 'pass')
            self.assertEqual(result['execution_status'], 'failed')
            self.assertEqual(result['error_category'], 'cleanup_error')
            self.assertEqual(result['recovery_attempts'][0]['outcome'], 'fail')
