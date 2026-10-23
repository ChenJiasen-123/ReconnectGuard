import json
from pathlib import Path
import tempfile
import unittest

from backends.platform_observer import PlatformObserver
from model.contracts import Context
from rg_platform.journal import EventJournal


PROFILE = {"wpa_state": "COMPLETED", "key_mgmt": "SAE",
           "pairwise_cipher": "CCMP", "pmf": "2", "address": "02:00:00:00:01:00"}


class FakeProcess:
    def __init__(self, pid=101, alive=True):
        self.pid = pid
        self.alive = alive

    def poll(self):
        return None if self.alive else 1


class FakeLab:
    def __init__(self, out):
        self.run_id = "run"
        self.out = Path(out)
        self.event_journal = EventJournal(self.run_id)
        self.status_value = PROFILE.copy()
        self.processes = [("sta", FakeProcess(), "epoch-1")]
        self.snapshot_value = {
            "sta": PROFILE.copy(),
            "ap_raw": "flags=[AUTH][ASSOC][AUTHORIZED][MFP]",
            "ap_query_available": True,
            "sta_pmksa_raw": "Index / AA / PMKID\n1 02:00:00:00:00:00 abc",
            "auth_path": "unknown",
            "key_lifecycle": "unknown",
        }

    def status(self):
        return self.status_value.copy()

    def snapshot(self, checkpoint):
        raw = dict(self.snapshot_value, checkpoint=checkpoint)
        self.save(checkpoint + ".json", raw)
        return raw

    def save(self, name, value):
        (self.out / name).write_text(json.dumps(value) + "\n", encoding="utf-8")


class JournalTest(unittest.TestCase):
    def test_sequence_and_negative_cursor(self):
        journal = EventJournal("run")
        first = journal.append("sta", "one", monotonic_ns=1)
        second = journal.append("ap", "two", monotonic_ns=2)
        self.assertEqual((first.seq, second.seq), (1, 2))
        self.assertEqual([row.seq for row in journal.records_after(-1)], [1, 2])
        self.assertEqual([row.seq for row in journal.wait_after(1, .01)], [2])
        self.assertEqual(second.evidence_ref, "events.jsonl#seq=2")


class PlatformObserverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = FakeLab(self.tmp.name)
        self.context = Context("run")
        self.observer = PlatformObserver(self.lab)
        self.observer.start()

    def tearDown(self):
        self.tmp.cleanup()

    def test_normalizes_baseline_disconnect_and_restore(self):
        self.lab.event_journal.append("sta", "CTRL-EVENT-SCAN-STARTED")
        self.lab.event_journal.append("ap", "AP-STA-CONNECTED 02:00:00:00:01:00")
        connected = self.lab.event_journal.append(
            "sta", "CTRL-EVENT-CONNECTED - Connection completed")
        baseline = self.observer.wait_event("baseline_confirmed", 0, .1, self.context)
        self.assertEqual(baseline.seq, connected.seq)
        self.assertEqual(baseline.data["key_mgmt"], "SAE")
        self.assertEqual(baseline.data["attempt_attribution"], "received_after_cursor")

        cursor = self.observer.mark()
        disconnected = self.lab.event_journal.append(
            "sta", "CTRL-EVENT-DISCONNECTED reason=3")
        event = self.observer.wait_event("sta_disconnected", cursor, .1, self.context)
        self.assertEqual(event.seq, disconnected.seq)

        self.context.attempt_id = 2
        cursor = self.observer.mark()
        restored_raw = self.lab.event_journal.append(
            "sta", "CTRL-EVENT-CONNECTED - Connection completed")
        restored = self.observer.wait_event("connection_restored", cursor, .1, self.context)
        self.assertEqual(restored.attempt_id, 2)
        self.assertEqual(restored.seq, restored_raw.seq)
        rows = (self.lab.out / "observer-events.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 3)

    def test_filters_wrong_source_noise_and_semantic_duplicates(self):
        self.lab.event_journal.append("ap", "CTRL-EVENT-DISCONNECTED reason=3")
        self.lab.event_journal.append("sta", "CTRL-EVENT-SCAN-RESULTS")
        self.lab.event_journal.append("sta", "CTRL-EVENT-DISCONNECTED reason=3",
                                      attempt_id=2)
        with self.assertRaises(TimeoutError):
            self.observer.wait_event("sta_disconnected", 0, .01, self.context)
        self.assertEqual({row["ignored"] for row in self.observer.observations},
                         {"wrong_source", "unexpected_kind", "wrong_attempt"})

        cursor = self.observer.mark()
        first = self.lab.event_journal.append("sta", "CTRL-EVENT-DISCONNECTED reason=3")
        self.lab.event_journal.append("sta", "CTRL-EVENT-DISCONNECTED reason=3")
        self.assertEqual(self.observer.wait_event(
            "sta_disconnected", cursor, .1, self.context).seq, first.seq)
        with self.assertRaises(TimeoutError):
            self.observer.wait_event("sta_disconnected", cursor, .01, self.context)
        self.assertIn("semantic_duplicate",
                      {row["ignored"] for row in self.observer.observations})

    def test_snapshot_maps_known_values_and_preserves_unknowns(self):
        snapshot = self.observer.snapshot("connected")
        self.assertEqual(snapshot["ap_state"], "authorized")
        self.assertEqual(snapshot["port_authorized"], "authorized")
        self.assertEqual(snapshot["pmksa_presence"], "present")
        self.assertEqual(snapshot["auth_path"], "unknown")
        self.assertEqual(snapshot["key_state"], "unknown")
        self.assertTrue(snapshot["same_processes_alive"])
        self.assertTrue((self.lab.out / "connected.json").exists())
        self.assertTrue((self.lab.out / "connected-observer.json").exists())

        self.lab.snapshot_value.update({
            "ap_raw": "FAIL",
            "ap_query_available": False,
            "sta_pmksa_raw": "Index / AA / PMKID",
        })
        disconnected = self.observer.snapshot("disconnected")
        self.assertEqual(disconnected["ap_state"], "unknown")
        self.assertEqual(disconnected["port_authorized"], "unknown")
        self.assertEqual(disconnected["pmksa_presence"], "absent")

    def test_requires_start_and_does_not_close_lab(self):
        self.observer.close()
        with self.assertRaises(RuntimeError):
            self.observer.mark()
        self.assertIsNotNone(self.lab.event_journal)


if __name__ == "__main__":
    unittest.main()
