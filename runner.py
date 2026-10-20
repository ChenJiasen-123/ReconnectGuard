"""第一阶段模拟入口：python runner.py --case client_disconnect_reconnect。"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import uuid

from backends.mock import MockBackend, SCENARIOS
from model.contracts import Context
from model.efsm import EFSM, State, InvalidTransition, profile_matches

ROOT = Path(__file__).resolve().parent


def load_case(path, case_id):
    # JSON 是 YAML 1.2 的子集；第一版只接受此格式，避免隐式类型转换和额外依赖。
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("不支持的案例格式版本")
    matches = [c for c in document["cases"] if c.get("id") == case_id]
    if len(matches) != 1:
        raise ValueError("案例不存在或编号重复")
    case = matches[0]
    if case.get("backend") != "mock" or case.get("reconnect_mode") != "explicit":
        raise ValueError("第一版只支持 mock 和 explicit")
    expected = [("observe", "connected_baseline"), ("action", "client_disconnect"),
                ("snapshot", "after_disconnect"), ("action", "client_reconnect"),
                ("snapshot", "after_reconnect")]
    steps = case.get("steps", [])
    if len(steps) != len(expected):
        raise ValueError("第一版要求完整的五步案例")
    for step, (key, value) in zip(steps, expected):
        if step.get(key) != value or set(step) != ({"action", "expect", "timeout_s"} if key == "action" else {key}):
            raise ValueError("不支持的案例步骤")
        if key == "action":
            wanted = "sta_disconnected" if value == "client_disconnect" else "connection_restored"
            timeout = step.get("timeout_s")
            if step.get("expect") != wanted or isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("等待事件或超时配置无效")
    return case


def run_case(case, output_root=ROOT / "results", scenario="normal"):
    context = Context(uuid.uuid4().hex)
    machine = EFSM(context)
    backend = MockBackend(context, scenario)
    out = Path(output_root) / context.run_id
    out.mkdir(parents=True, exist_ok=False)
    snapshots = []
    error = None
    category = None
    step_number = 0
    try:
        backend.start()
        for step_number, step in enumerate(case["steps"], 1):
            if "observe" in step:
                event = backend.wait_event("baseline_confirmed", 0, 20, context)
                if not machine.apply(event):
                    raise InvalidTransition("基线事件未被接受")
                snapshots.append(backend.snapshot(step["observe"]))
            elif "snapshot" in step:
                snapshots.append(backend.snapshot(step["snapshot"]))
            else:
                action = step["action"]
                if action not in machine.allowed_actions():
                    raise InvalidTransition(f"当前状态禁止动作: {action}")
                cursor = backend.mark()
                if action == "client_reconnect":
                    context.attempt_id += 1
                request = backend.emit("disconnect_requested" if action == "client_disconnect"
                                       else "reconnect_requested", source="runner")
                if not machine.apply(request):
                    raise InvalidTransition("请求未被接受")
                receipt = backend.execute(action, context)
                if not receipt.accepted:
                    raise RuntimeError("动作被拒绝")
                event = backend.wait_event(step["expect"], cursor, step["timeout_s"], context)
                if not machine.apply(event, after=cursor):
                    raise InvalidTransition("观测事件未被接受")
    except Exception as exc:
        error = str(exc)
        category = "observation_timeout" if isinstance(exc, TimeoutError) else "test_error"
        machine.fail(error)
        snapshots.append(backend.snapshot("failure"))
    finally:
        backend.close()
    restored = machine.state == State.RECONNECTED
    result = {"run_id": context.run_id, "simulated": True, "scenario": scenario,
              "execution_status": "completed" if restored else "failed",
              "state": machine.state.value, "failed_step": step_number if error else None,
              "error_category": category, "error": error,
              "disconnect_observed": any(t.get("to") == "DISCONNECTED" for t in machine.transitions),
              "recovery": "pass" if restored else ("fail" if step_number == 4 else "not_evaluated"),
              "security_profile": "pass" if restored and profile_matches(snapshots[-1]["sta"]) else "not_evaluated",
              "client_process_unchanged": "pass" if len({s["process_epoch"] for s in snapshots}) == 1 else "unknown",
              "auth_path": "unknown", "authorization_timing": "insufficient_evidence",
              "security_verdict": "not_evaluated",
              "covered_transitions": sorted({t["from"] + "->" + t["to"] for t in machine.transitions})}
    artifacts = {"meta.json": {"run_id": context.run_id, "simulated": True,
                               "clock": "virtual", "case": case, "scenario": scenario},
                 "result.json": result, "snapshots.json": snapshots,
                 "context.json": asdict(context), "observations.json": backend.observations}
    for name, value in artifacts.items():
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, rows in {"events.jsonl": [asdict(e) for e in backend.events],
                       "actions.jsonl": [asdict(r) for r in backend.receipts],
                       "transitions.jsonl": machine.transitions}.items():
        (out / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return result, out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="client_disconnect_reconnect")
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.yaml")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    args = parser.parse_args()
    try:
        case = load_case(args.cases, args.case)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    result, out = run_case(case, args.output, args.scenario)
    print(json.dumps({"result": result, "output": str(out.resolve())}, ensure_ascii=False, indent=2))
    return 0 if result["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
