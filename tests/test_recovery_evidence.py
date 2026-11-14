import tempfile
from pathlib import Path
import unittest
from threading import Event, Thread
from rg_platform.epochs import ConnectionEpochs
from rg_platform.journal import EventJournal
from model.contracts import Context
from backends.platform_observer import PlatformObserver
from backends.sessions import RealSession
from runner import run_case, load_case, ROOT
from test_real_session import LabDouble

class RecoveryEvidenceTest(unittest.TestCase):
    def test_delayed_old_event_keeps_receive_epoch(self):
        epochs = ConnectionEpochs()
        journal = EventJournal("run")
        received, release = Event(), Event()
        def listener():
            attempt = epochs.observe("CTRL-EVENT-CONNECTED")
            received.set()
            release.wait(2)
            journal.append("sta", "CTRL-EVENT-CONNECTED", attempt)
        worker = Thread(target=listener)
        worker.start()
        self.assertTrue(received.wait(1))
        epochs.observe("CTRL-EVENT-DISCONNECTED")
        self.assertEqual(epochs.observe("CTRL-EVENT-CONNECTED"), 2)
        release.set(); worker.join(2)
        with tempfile.TemporaryDirectory() as tmp:
            class Lab:
                run_id = "run"
                out = Path(tmp)
                event_journal = journal
                def status(self):
                    raise AssertionError("old event must be rejected before STATUS")
            observer = PlatformObserver(Lab()); observer.start()
            with self.assertRaises(TimeoutError):
                observer.wait_event("connection_restored", 0, .02, Context("run", attempt_id=2))
            self.assertEqual(observer.observations[0]["ignored"], "wrong_attempt")

    def test_unattributed_connected_is_not_relabelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Lab:
                run_id = "run"
                out = Path(tmp)
                event_journal = EventJournal("run")
            lab = Lab();lab.event_journal.append("sta", "CTRL-EVENT-CONNECTED")
            observer = PlatformObserver(lab);observer.start()
            with self.assertRaises(TimeoutError):
                observer.wait_event("connection_restored", 0, .02, Context("run", attempt_id=2))
            self.assertEqual(observer.observations[0]["ignored"], "unattributed_attempt")

    def test_connection_success_does_not_hide_data_failure(self):
        for status in ("pass", "fail"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                class Lab(LabDouble):
                    calls = 0
                    def probe_data_plane(self):
                        self.calls += 1
                        return {"status": "pass" if self.calls == 1 else status}
                def factory(output, scenario, client):
                    return RealSession(output, client, lab_factory=Lab)
                result, out = run_case(load_case(ROOT / "cases.yaml", "client_disconnect_reconnect"),
                                       Path(tmp), backend="real", session_factory=factory)
                self.assertEqual(result["connection_recovery"], "pass")
                self.assertEqual(result["data_plane_recovery"], status)
                self.assertEqual(result["execution_status"], "completed" if status == "pass" else "failed")
                if status == "fail":
                    self.assertEqual(result["error_category"], "data_plane_failed")
                self.assertTrue((out / "after_reconnect-data-plane.json").exists())
