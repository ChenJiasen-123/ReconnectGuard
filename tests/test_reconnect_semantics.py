"""动作拒绝、恢复超时与重复 RECONNECT 的语义回归。

覆盖三件事：
1. ``actions.jsonl`` 里 ``effect_status`` 恒为 ``unverified``（Executor 只记
   ``accepted``），效果必须由 ``backends.receipts`` 从事件补算；
2. 命令被接受但效果未发生（恢复超时）时，回执为 ``not_observed``；
3. 重复 RECONNECT 在 Runner 路径上被状态机拒绝，且不消耗 attempt_id；
   Executor 裸调用没有去重，重不重复是上层的事。
"""
from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path

from backends.mock import MockBackend, PROFILE
from backends.receipts import annotate, annotate_directory, summarize
from backends.sessions import MockSession
from model.contracts import ActionReceipt, Context, Event
from model.efsm import EFSM, InvalidTransition, State
from runner import ROOT, load_case, run_case


def event(event_id, kind, seq, *, run_id="run-test", attempt_id=1, source="sta", data=None):
    return Event(event_id, run_id, attempt_id, source, kind, seq, seq / 100, data or {})


def drive_to(machine, *kinds):
    for index, kind in enumerate(kinds, 1):
        source = "runner" if kind.endswith("requested") else "sta"
        data = PROFILE.copy() if kind in {"baseline_confirmed", "connection_restored"} else {}
        machine.apply(event(f"e{index}", kind, index, source=source, data=data))


class EffectAnnotationTest(unittest.TestCase):
    def case(self, budget=1):
        case = load_case(ROOT/'cases.yaml', 'client_disconnect_reconnect')
        case['recovery_policy'] = {'max_retries': budget}
        return case

    def test_actions_jsonl_keeps_effect_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, out = run_case(self.case(), tmp, 'normal')
            rows = [json.loads(line) for line in (out/'actions.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([r['effect_status'] for r in rows], ['unverified', 'unverified'])

    def test_accepted_without_effect_is_not_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, out = run_case(self.case(), tmp, 'reconnect_once_timeout')
            self.assertEqual(result['retries_used'], 1)
            annotated, rows = annotate_directory(out)
            self.assertEqual([(r['action'], r['attempt_id'], r['effect_status']) for r in rows],
                             [('client_disconnect', 1, 'observed'),
                              ('client_reconnect', 2, 'not_observed'),
                              ('client_reconnect', 3, 'observed')])
            self.assertEqual(rows[1]['note'], '请求后未观测到效果事件')
            self.assertIsNone(rows[1]['effect_event_id'])
            self.assertEqual([r.effect_status for r in annotated],
                             ['observed', 'not_observed', 'observed'])

    def test_effect_on_other_attempt_is_not_claimed_as_same(self):
        receipts = [ActionReceipt('a1', 'client_reconnect', True, run_id='run-test', attempt_id=2)]
        events = [event('e1', 'reconnect_requested', 1, source='runner', attempt_id=2),
                  event('e2', 'connection_restored', 2, attempt_id=3, data=PROFILE.copy())]
        _, rows = annotate(receipts, events, 'run-test')
        self.assertEqual(rows[0]['effect_status'], 'observed')
        self.assertEqual(rows[0]['attribution'], 'attempt_mismatch')

    def test_receipt_without_request_stays_unverified(self):
        receipts = [ActionReceipt('a1', 'client_reconnect', True, run_id='run-test', attempt_id=1)]
        _, rows = annotate(receipts, [], 'run-test')
        self.assertEqual(rows[0]['effect_status'], 'unverified')
        self.assertEqual(rows[0]['note'], '缺少可配对的请求事件')

    def test_rejected_command_is_not_credited_with_effect(self):
        receipts = [ActionReceipt('a1', 'client_disconnect', False, run_id='run-test',
                                  error_category='action_rejected')]
        events = [event('e1', 'disconnect_requested', 1, source='runner'),
                  event('e2', 'sta_disconnected', 2)]
        annotated, rows = annotate(receipts, events, 'run-test')
        self.assertEqual(rows[0]['effect_status'], 'not_observed')
        self.assertEqual(rows[0]['note'], '命令未被接受：action_rejected')
        self.assertEqual(annotated[0].effect_status, 'not_observed')

    def test_cli_writes_annotated_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'meta.json').write_text(json.dumps({'run_id': 'run-test'}), encoding='utf-8')
            (directory/'actions.jsonl').write_text(
                json.dumps({'action_id': 'a1', 'action': 'client_reconnect', 'accepted': True}) + '\n',
                encoding='utf-8')
            (directory/'events.jsonl').write_text(
                json.dumps({'event_id': 'e1', 'run_id': 'run-test', 'attempt_id': 1, 'source': 'runner',
                            'kind': 'reconnect_requested', 'seq': 1, 'monotonic_s': 0.01, 'data': {}}) + '\n',
                encoding='utf-8')
            _, rows = annotate_directory(directory)
            self.assertTrue((directory/'actions-annotated.jsonl').exists())
            self.assertTrue((directory/'actions-effect-report.json').exists())
            self.assertEqual(rows[0]['effect_status'], 'not_observed')
            self.assertEqual(summarize(rows)[0]['count'], 1)


class DuplicateReconnectTest(unittest.TestCase):
    def test_reconnecting_state_refuses_second_reconnect(self):
        context = Context('run-test')
        machine = EFSM(context)
        drive_to(machine, 'baseline_confirmed', 'disconnect_requested', 'sta_disconnected',
                 'reconnect_requested')
        self.assertEqual(machine.state, State.RECONNECTING)
        self.assertEqual(machine.allowed_actions(), set())
        with self.assertRaises(InvalidTransition):
            machine.apply(event('e5', 'reconnect_requested', 5, source='runner', attempt_id=1))
        self.assertEqual(machine.state, State.RECONNECTING)

    def test_executor_alone_does_not_deduplicate(self):
        context = Context('run-test')
        executor = MockBackend(context, 'normal')
        executor.start()
        executor.execute('client_reconnect', context)
        executor.execute('client_reconnect', context)
        self.assertEqual([r.action for r in executor.receipts], ['client_reconnect', 'client_reconnect'])
        self.assertTrue(all(r.accepted for r in executor.receipts))

    def test_runner_rejects_reconnect_outside_allowed_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, out = run_case(load_case(ROOT/'cases.yaml', 'client_disconnect_reconnect'), tmp, 'normal')
            self.assertEqual(result['execution_status'], 'completed')
            rows = [json.loads(line) for line in (out/'actions.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([r['action'] for r in rows], ['client_disconnect', 'client_reconnect'])

    def test_rejected_action_stops_before_retry(self):
        class Session(MockSession):
            def __init__(self, out, scenario, client):
                super().__init__(out, scenario)
                execute = self.executor.execute

                def wrapped(action, context):
                    receipt = execute(action, context)
                    if action == 'client_reconnect':
                        self.executor.receipts[-1] = replace(
                            receipt, accepted=False, raw_response='FAIL',
                            error_category='action_rejected')
                        return self.executor.receipts[-1]
                    return receipt

                self.executor.execute = wrapped

        with tempfile.TemporaryDirectory() as tmp:
            case = load_case(ROOT/'cases.yaml', 'client_disconnect_reconnect')
            case['recovery_policy'] = {'max_retries': 1}
            result, out = run_case(case, tmp, 'normal', session_factory=Session)
            self.assertEqual(result['error_category'], 'action_rejected')
            self.assertEqual(result['retries_used'], 0)
            self.assertNotEqual(result['recovery'], 'pass')
            rows = [json.loads(line) for line in (out/'actions.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([(r['action'], r['accepted']) for r in rows],
                             [('client_disconnect', True), ('client_reconnect', False)])
            _, annotated = annotate_directory(out)
            self.assertEqual(annotated[1]['effect_status'], 'not_observed')


if __name__ == '__main__':
    unittest.main()
