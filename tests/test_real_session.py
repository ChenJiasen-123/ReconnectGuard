"""不依赖 Linux 的真实装配回归：使用事件 journal，替身仅代替 Lab。"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from backends.sessions import RealSession
from model.contracts import BackendError
from rg_platform.journal import EventJournal
from runner import ROOT, load_case, run_case

PROFILE = {"wpa_state": "COMPLETED", "key_mgmt": "SAE", "pairwise_cipher": "CCMP", "pmf": "2"}


class Process:
    pid = 42
    def poll(self):
        return None


class LabDouble:
    def __init__(self, client, *, output_root, failure=None):
        self.run_id = "fake-" + uuid.uuid4().hex
        self.out = Path(output_root) / self.run_id
        self.out.mkdir()
        self.event_journal = EventJournal(self.run_id)
        self.processes = [("sta", Process(), "one-sta-epoch")]
        self.meta = {"run_id": self.run_id, "platform_marker": "preserve"}
        self.failure = failure
        self.connected = True
        self.dead = False
        self.stops = 0

    def save(self, name, value):
        (self.out / name).write_text(json.dumps(value), encoding="utf-8")

    def record_event(self, source, raw_event, attempt_id=None):
        record = self.event_journal.append(source, raw_event, attempt_id)
        with (self.out / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record.to_dict()) + "\n")
        return record

    def start(self):
        if self.failure == "cancel":
            raise KeyboardInterrupt()
        if self.failure == "startup":
            raise BackendError("environment_error", "partial start")
        self.record_event("sta", "CTRL-EVENT-CONNECTED")

    def check_health(self):
        if self.dead:
            raise BackendError("process_exit", "STA exited")

    def command(self, target, command, semantic, allow_rejected=False):
        if command == "DISCONNECT":
            if self.failure == "reject":
                return "FAIL"
            if self.failure == "process":
                self.dead = True
                return "OK"
            if self.failure != "timeout":
                self.connected = False
                self.record_event("sta", "CTRL-EVENT-DISCONNECTED")
        elif command.startswith("DEAUTHENTICATE ") or command.startswith("DISASSOCIATE "):
            if self.failure == "reject":
                return "FAIL"
            if self.failure == "process":
                self.dead = True
                return "OK"
            if self.failure != "timeout":
                self.connected = False
                self.record_event("sta", "CTRL-EVENT-DISCONNECTED")
                self.connected = True
                self.record_event("sta", "CTRL-EVENT-CONNECTED", attempt_id=2)
        elif command == "RECONNECT":
            self.connected = True
            self.record_event("sta", "CTRL-EVENT-CONNECTED")
        return "OK"

    def status(self):
        if self.failure == "collection":
            raise OSError("STATUS unavailable")
        return (dict(PROFILE, address="02:00:00:00:00:01") if self.connected
                else {"wpa_state": "DISCONNECTED", "address": "02:00:00:00:00:01"})

    def snapshot(self, checkpoint):
        if checkpoint == "failure" and self.failure == "startup":
            raise RuntimeError("snapshot unavailable during partial start")
        record = {"sta": self.status(), "ap_query_available": self.connected,
                  "ap_raw": "flags=[AUTHORIZED][MFP]" if self.connected else "FAIL",
                  "sta_pmksa_raw": "unknown", "auth_path": "unknown", "key_lifecycle": "unknown"}
        self.save(checkpoint + ".json", record)
        return record

    def stop(self):
        self.stops += 1
        return {"errors": ["leftover namespace"] if self.failure == "cleanup" else []}


class RealSessionTest(unittest.TestCase):
    def exercise(self, failure=None):
        case = deepcopy(load_case(ROOT / "cases.yaml", "client_disconnect_reconnect"))
        for step in case["steps"]:
            if "timeout_s" in step:
                step["timeout_s"] = .03
        with tempfile.TemporaryDirectory() as tmp:
            instances = []
            def factory(output_root, scenario, client):
                def create(mode, **kwargs):
                    lab = LabDouble(mode, failure=failure, **kwargs)
                    instances.append(lab)
                    return lab
                return RealSession(output_root, client, lab_factory=create)
            result, out = run_case(case, tmp, backend="real", session_factory=factory)
            data = {name: json.loads((out / name).read_text(encoding="utf-8"))
                    for name in ("meta.json", "result.json", "runner-cleanup.json")}
            raw = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()] if (out / "events.jsonl").exists() else []
            standard = [json.loads(line) for line in (out / "runner-events.jsonl").read_text().splitlines()]
            actions = [json.loads(line) for line in (out / "actions.jsonl").read_text().splitlines()]
            self.assertEqual(instances[0].stops, 1)
            self.assertEqual(result["run_id"], instances[0].run_id)
            self.assertFalse(result["simulated"])
            self.assertEqual(data["meta.json"]["platform_marker"], "preserve")
            self.assertEqual(data["result.json"], result)
            return result, raw, standard, actions

    def test_real_composition_and_shared_evidence(self):
        result, raw, events, actions = self.exercise()
        self.assertEqual(result["state"], "RECONNECTED")
        self.assertEqual(result["client_process_unchanged"], "pass")
        self.assertEqual([r["seq"] for r in raw], [1, 2, 3, 4, 5])
        self.assertEqual([e["seq"] for e in events], [1, 2, 3, 4, 5])
        self.assertEqual([e["attempt_id"] for e in events], [1, 1, 1, 2, 2])
        self.assertEqual([a["command"] for a in actions], ["DISCONNECT", "RECONNECT"])
        self.assertTrue(all(a["effect_status"] == "unverified" for a in actions))

    def test_failures_keep_evidence_and_close(self):
        for failure, category in (("reject", "action_rejected"), ("timeout", "observation_timeout"),
                                  ("process", "process_exit"), ("collection", "collection_error"),
                                  ("startup", "environment_error"), ("cleanup", "cleanup_error"), ("cancel", "cancelled")):
            with self.subTest(failure=failure):
                result, _, _, _ = self.exercise(failure)
                self.assertEqual(result["execution_status"], "failed")
                self.assertEqual(result["error_category"], category)
                self.assertEqual(result["security_verdict"], "not_evaluated")

    def test_real_automatic_ap_cases_use_executor_and_observer(self):
        for case_id, command_prefix in (("ap_deauth", "DEAUTHENTICATE "),
                                        ("ap_disassoc", "DISASSOCIATE ")):
            with self.subTest(case_id=case_id):
                case = deepcopy(load_case(ROOT / "cases.yaml", case_id))
                with tempfile.TemporaryDirectory() as tmp:
                    instances = []

                    def factory(output_root, scenario, client):
                        def create(mode, **kwargs):
                            lab = LabDouble(mode, **kwargs)
                            instances.append(lab)
                            return lab
                        return RealSession(output_root, client, lab_factory=create)

                    result, out = run_case(case, tmp, backend="real", session_factory=factory)
                    actions = [json.loads(line) for line in
                               (out / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
                    events = [json.loads(line) for line in
                              (out / "runner-events.jsonl").read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(result["execution_status"], "completed")
                    self.assertEqual(result["state"], "RECONNECTED")
                    self.assertEqual(result["recovery"], "pass")
                    self.assertEqual(len(actions), 1)
                    self.assertTrue(actions[0]["command"].startswith(command_prefix))
                    self.assertEqual([event["attempt_id"] for event in events], [1, 1, 1, 2])
                    self.assertEqual(instances[0].stops, 1)


class BatchTest(unittest.TestCase):
    def test_failed_cleanup_stops_next_round(self):
        from contextlib import redirect_stdout
        import io
        from unittest.mock import patch
        from runner import main
        with tempfile.TemporaryDirectory() as tmp:
            result = {"execution_status": "failed", "cleanup_status": "failed", "error_category": "cleanup_error"}
            with patch("sys.argv", ["runner.py", "--repeat", "3", "--output", tmp]), \
                 patch("runner.run_case", return_value=(result, Path(tmp))) as execute, \
                 redirect_stdout(io.StringIO()):
                code = main()
            self.assertEqual(code, 1)
            self.assertEqual(execute.call_count, 1)
            batch = json.loads(next(Path(tmp).glob("batch-*.json")).read_text(encoding="utf-8"))
            self.assertEqual(batch["executed"], 1)
            self.assertEqual(batch["requested"], 3)
