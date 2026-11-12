"""王梓权：实际检查器与证据归类的回归测试。

用构造数据覆盖正常、异常、证据不足和迟到旧事件；不需要 Linux / hwsim / sudo。
真实帧级证据仍需在 Kali 上补齐，本文件不替代真实协议验收。
"""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from comparison import run_comparison
from model.evaluation import CheckResult, evaluate, load_evaluator
from observer import evidence
from observer.demo import (RUN_ID, SCENARIOS, build_scenario, frame_evidence,
                           run_scenario)
from observer.oracle import Oracle
from runner import ROOT, load_case, run_case

SPEC = "observer.oracle:Oracle"
VALID_STATUSES = {"pass", "fail", "insufficient_evidence", "not_evaluated"}


class LoadingTest(unittest.TestCase):
    def test_spec_loads_new_instances(self):
        first, second = load_evaluator(SPEC), load_evaluator(SPEC)
        self.assertIsInstance(first, Oracle)
        self.assertIsNot(first, second)
        self.assertEqual(first.last_report, None)

    def test_empty_evidence_is_not_a_pass(self):
        from model.contracts import Context
        checks = Oracle().evaluate(context=Context("x", attempt_id=2), snapshots=[], events=[])
        self.assertEqual([c.status for c in checks],
                         ["insufficient_evidence", "insufficient_evidence"])
        self.assertEqual([c.evidence_refs for c in checks], [[], []])


class ScenarioTest(unittest.TestCase):
    def test_all_scenarios_return_valid_checks(self):
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                payload = run_scenario(name)
                self.assertTrue(payload["synthetic"])
                self.assertEqual(len(payload["checks"]), 2)
                self.assertEqual({c["name"] for c in payload["checks"]},
                                 {"state_consistency", "authorization_timing"})
                for check in payload["checks"]:
                    self.assertIn(check["status"], VALID_STATUSES)
                    if check["status"] in {"pass", "fail"}:
                        self.assertTrue(check["evidence_refs"], name)
                    self.assertTrue(check["reason"], name)

    def test_expected_statuses(self):
        expected = {
            "normal": ("pass", "insufficient_evidence"),
            "frame_ok": ("pass", "pass"),
            "frame_bad": ("pass", "fail"),
            "old_attempt_frame": ("pass", "insufficient_evidence"),
            "persistent_mismatch": ("fail", "insufficient_evidence"),
            "transient_mismatch": ("pass", "insufficient_evidence"),
            "missing_evidence": ("insufficient_evidence", "insufficient_evidence"),
            "pmksa_present_only": ("pass", "insufficient_evidence"),
            "late_old_attempt": ("pass", "insufficient_evidence"),
            "process_restart": ("fail", "insufficient_evidence"),
        }
        for name, want in expected.items():
            with self.subTest(scenario=name):
                got = tuple(c["status"] for c in run_scenario(name)["checks"])
                self.assertEqual(got, want)

    def test_results_are_not_fixed_pass(self):
        statuses = {name: tuple(c["status"] for c in run_scenario(name)["checks"])
                    for name in SCENARIOS}
        self.assertGreater(len(set(statuses.values())), 2)
        self.assertNotEqual(statuses["normal"], statuses["persistent_mismatch"])
        self.assertNotEqual(statuses["frame_ok"], statuses["frame_bad"])

    def test_pass_and_fail_refs_are_resolvable(self):
        for name in SCENARIOS:
            context, snapshots, events = build_scenario(name)
            valid = {str(e.event_id) for e in events}
            valid.update(f"snapshots.json#/{i}" for i in range(len(snapshots)))
            for check in evaluate(Oracle(), context, snapshots, events):
                with self.subTest(scenario=name, check=check.name):
                    self.assertTrue(set(check.evidence_refs) <= valid)

    def test_late_old_attempt_is_excluded(self):
        context, snapshots, events = build_scenario("late_old_attempt")
        oracle = Oracle()
        checks = evaluate(oracle, context, snapshots, events)
        rows = oracle.last_report["attempt_boundary"]["old_or_unattributed"]
        self.assertEqual([row["classification"] for row in rows], ["late_old_attempt"])
        self.assertIn("迟到旧尝试", checks[0].reason)
        self.assertNotIn(f"{RUN_ID}:raw:19:connection_restored", checks[0].evidence_refs)

    def test_transient_mismatch_is_explained(self):
        context, snapshots, events = build_scenario("transient_mismatch")
        check = evaluate(Oracle(), context, snapshots, events)[0]
        self.assertEqual(check.status, "pass")
        self.assertIn("短暂双端不同步", check.reason)
        self.assertIn("snapshots.json#/1", check.evidence_refs)

    def test_cache_present_is_not_cache_reuse(self):
        context, snapshots, events = build_scenario("pmksa_present_only")
        oracle = Oracle()
        evaluate(oracle, context, snapshots, events)
        report = oracle.last_report
        self.assertEqual(report["auth_path"]["value"], "unknown")
        self.assertEqual(report["cache"]["present_count"], 3)
        self.assertFalse(report["cache"]["reuse_claimed"])
        self.assertIn("不采信", report["auth_path"]["reason"])
        self.assertIn("缓存条目存在不等于缓存复用", report["auth_path"]["reason"])

    def test_frame_refs_only_in_reason_not_in_evidence_refs(self):
        payload = run_scenario("frame_ok")
        self.assertIn("trace.pcap#frame=1207", payload["evidence"]["frame_refs"])
        self.assertIn("trace.pcap#frame=", payload["checks"][1]["reason"])
        for check in payload["checks"]:
            self.assertTrue(all(not ref.startswith("trace.pcap") for ref in check["evidence_refs"]))

    def test_equal_key_and_authorization_timestamps_stay_unresolved(self):
        context, snapshots, events = build_scenario("normal")
        frames = frame_evidence()
        for row in frames["timeline"]:
            if row["stage"] in ("port_authorized", "data_frame"):
                row["monotonic_s"] = 101.42
        snapshots[-1]["frame_evidence"] = frames
        checks = evaluate(Oracle(), context, snapshots, events)
        self.assertEqual(checks[1].status, "insufficient_evidence")
        self.assertIn("时间戳相同", checks[1].reason)

    def test_frame_evidence_from_other_run_is_rejected(self):
        context, snapshots, events = build_scenario("normal")
        frames = frame_evidence()
        frames["run_id"] = "another-run"
        snapshots[-1]["frame_evidence"] = frames
        checks = evaluate(Oracle(), context, snapshots, events)
        self.assertEqual(checks[1].status, "insufficient_evidence")
        self.assertIn("run_id 与本轮不一致", checks[1].reason)


class EvidenceCLITest(unittest.TestCase):
    def test_load_run_and_build_report(self):
        context, snapshots, events = build_scenario("frame_ok")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "snapshots.json").write_text(json.dumps(snapshots), encoding="utf-8")
            (folder / "runner-events.jsonl").write_text(
                "".join(json.dumps(asdict(row)) + "\n" for row in events), encoding="utf-8")
            (folder / "context.json").write_text(json.dumps(
                {"run_id": RUN_ID, "attempt_id": 2, "reconnect_mode": "explicit"}), encoding="utf-8")
            loaded_context, loaded_snapshots, loaded_events = evidence.load_run(folder)
            report = evidence.build_report(loaded_context, loaded_snapshots, loaded_events)
            self.assertEqual(report["run_id"], RUN_ID)
            self.assertEqual(report["auth_path"]["value"], "full_sae")
            self.assertIn("trace.pcap#frame=1207", report["frame_refs"])
            self.assertEqual(report["security_verdict"], "not_evaluated")
            target = folder / "evidence-report.json"
            self.assertEqual(evidence.main([str(folder), "--output", str(target)]), 0)
            self.assertTrue(target.exists())


class RunnerIntegrationTest(unittest.TestCase):
    def test_run_case_with_evaluator_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_case(load_case(ROOT / "cases.yaml", "client_disconnect_reconnect"),
                                tmp, evaluator=Oracle())
            self.assertEqual(result["execution_status"], "completed")
            self.assertEqual(result["security_verdict"], "not_evaluated")
            self.assertEqual(len(result["checks"]), 2)
            for check in result["checks"]:
                self.assertIn(check["status"], VALID_STATUSES)

    def test_run_case_with_evaluator_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = run_case(load_case(ROOT / "cases.yaml", "ap_deauth"),
                                tmp, evaluator_spec=SPEC)
            self.assertEqual(result["evaluator"], SPEC)
            self.assertEqual(result["error_category"], None)
            self.assertEqual(result["execution_status"], "completed")

    def test_comparison_batch_records_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_comparison(ROOT / "cases.yaml", tmp, repeat=1, evaluator_spec=SPEC)
            self.assertEqual(report["executed"], 3)
            self.assertIsNone(report["stop_reason"])
            for group in report["groups"]:
                self.assertEqual(sum(group["check_counts"]["state_consistency"].values()), 1)
                self.assertEqual(sum(group["check_counts"]["authorization_timing"].values()), 1)
            self.assertEqual(report["security_verdict"], "not_evaluated")


if __name__ == "__main__":
    unittest.main()
