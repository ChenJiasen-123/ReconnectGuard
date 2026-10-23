import json
from pathlib import Path
import tempfile
import unittest
from runner import ROOT, load_case, run_case


class RunnerTest(unittest.TestCase):
    def test_all_scenarios_and_saved_evidence(self):
        case = load_case(ROOT / "cases.yaml", "client_disconnect_reconnect")
        for scenario, state, failed_step in [("normal", "RECONNECTED", None),
                ("noisy_events", "RECONNECTED", None),
                ("disconnect_timeout", "FAILED", 2), ("reconnect_timeout", "FAILED", 4)]:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                result, out = run_case(case, tmp, scenario)
                self.assertEqual(result["state"], state)
                self.assertEqual(result["failed_step"], failed_step)
                self.assertEqual(result["security_verdict"], "not_evaluated")
                self.assertTrue(result["simulated"])
                self.assertEqual(json.loads((out / "result.json").read_text(encoding="utf-8")), result)
                transitions = [json.loads(line) for line in (out / "transitions.jsonl").read_text(encoding="utf-8").splitlines()]
                actions = [json.loads(line) for line in (out / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(transitions[-1]["to"], state)
                if scenario == "disconnect_timeout":
                    self.assertEqual(len(actions), 1)
                    self.assertEqual(result["recovery"], "not_evaluated")
                if scenario == "reconnect_timeout":
                    self.assertEqual(result["recovery"], "fail")
                    self.assertIn("failure", (out / "snapshots.json").read_text(encoding="utf-8"))
                if scenario == "noisy_events":
                    ignored = json.loads((out / "observations.json").read_text(encoding="utf-8"))
                    self.assertEqual({v["ignored"] for v in ignored}, {"wrong_context", "wrong_source"})
                    self.assertEqual(len(transitions), 5)

    def test_invalid_timeout_rejected_before_run(self):
        document = json.loads((ROOT / "cases.yaml").read_text(encoding="utf-8"))
        for timeout in (-1, 0, True, float("inf"), "5"):
            document["cases"][0]["steps"][1]["timeout_s"] = timeout
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "cases.yaml"
                path.write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    load_case(path, "client_disconnect_reconnect")
