import unittest
from model.contracts import Context, Event
from model.efsm import EFSM, State, InvalidTransition
from backends.mock import PROFILE


class EFSMTest(unittest.TestCase):
    def setUp(self):
        self.model = EFSM(Context("run"))

    def event(self, **updates):
        values = dict(event_id="one", run_id="run", attempt_id=1, source="sta",
                      kind="baseline_confirmed", seq=1, monotonic_s=0.1, data=PROFILE.copy())
        values.update(updates)
        return Event(**values)

    def test_rejects_unrelated_evidence(self):
        for update in ({"run_id": "other"}, {"attempt_id": 0}, {"attempt_id": None}, {"source": "ap"}):
            self.assertFalse(self.model.apply(self.event(**update)))
        self.assertFalse(self.model.apply(self.event(), after=1))
        self.assertEqual(self.model.state, State.INIT)

    def test_duplicate_does_not_advance(self):
        event = self.event()
        self.assertTrue(self.model.apply(event))
        self.assertFalse(self.model.apply(event))
        self.assertEqual(len(self.model.transitions), 1)

    def test_illegal_transition(self):
        with self.assertRaises(InvalidTransition):
            self.model.apply(self.event(kind="connection_restored"))
        self.assertEqual(self.model.state, State.INIT)

    def test_security_mismatch(self):
        data = dict(PROFILE, key_mgmt="WPA2-PSK")
        with self.assertRaises(InvalidTransition):
            self.model.apply(self.event(data=data))

    def test_command_receipt_is_not_disconnect_evidence(self):
        self.model.apply(self.event())
        self.model.apply(self.event(event_id="two", seq=2, source="runner", kind="disconnect_requested"))
        self.assertEqual(self.model.state, State.DISCONNECTING)
        self.assertEqual(self.model.allowed_actions(), set())
