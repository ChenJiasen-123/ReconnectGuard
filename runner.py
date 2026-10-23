"""统一入口：mock 保留原型，real 运行 hwsim 主动断开与显式重连。"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import uuid

from backends.mock import SCENARIOS
from backends.sessions import MockSession, RealSession
from model.contracts import BackendError
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
    if case.get("backend") not in {"mock", "real"} or case.get("reconnect_mode") != "explicit":
        raise ValueError("目前只支持 mock/real 后端及 explicit 重连")
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


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _continuity(snapshots):
    epochs = []
    for snapshot in snapshots:
        epoch = snapshot.get("process_epoch")
        if isinstance(epoch, dict):
            epoch = epoch.get("sta")
        if not epoch:
            return "unknown"
        if snapshot.get("same_processes_alive") is False:
            return "fail"
        epochs.append(epoch)
    return "pass" if len(epochs) >= 3 and len(set(epochs)) == 1 else "unknown"


def run_case(case, output_root=ROOT / "results", scenario="normal", *,
             backend=None, client="stock", session_factory=None):
    chosen = backend or case["backend"]
    if chosen not in {"mock", "real"}:
        raise ValueError("未知 backend")
    if chosen == "real" and scenario != "normal":
        raise ValueError("--scenario 仅用于 mock；真实失败需单独注入")
    if session_factory is not None:
        session = session_factory(output_root, scenario, client)
    else:
        session = MockSession(output_root, scenario) if chosen == "mock" else RealSession(output_root, client)
    context, out = session.context, session.out
    observer, executor = session.observer, session.executor
    machine = EFSM(context)
    snapshots, diagnostics = [], []
    error = category = None
    step_number = 0
    recovered = False
    cleanup = {"errors": []}
    try:
        session.start()
        for step_number, step in enumerate(case["steps"], 1):
            if "observe" in step:
                event = observer.wait_event("baseline_confirmed", 0, 20, context)
                if not machine.apply(event):
                    raise InvalidTransition("基线事件未被接受")
                snapshots.append(observer.snapshot(step["observe"]))
            elif "snapshot" in step:
                snapshots.append(observer.snapshot(step["snapshot"]))
            else:
                action = step["action"]
                if action not in machine.allowed_actions():
                    raise InvalidTransition(f"当前状态禁止动作: {action}")
                cursor = observer.mark()
                if action == "client_reconnect":
                    context.attempt_id += 1
                request = session.record_request("disconnect_requested" if action == "client_disconnect"
                                                 else "reconnect_requested")
                if not machine.apply(request, after=cursor):
                    raise InvalidTransition("请求未被接受")
                receipt = executor.execute(action, context)
                if not receipt.accepted:
                    raise BackendError(receipt.error_category or "action_rejected", "动作未被接受: " + action)
                # 请求与观测属于同一个 seq 空间；请求之前的事件不能完成等待。
                event = observer.wait_event(step["expect"], request.seq, step["timeout_s"], context)
                if not machine.apply(event, after=request.seq):
                    raise InvalidTransition("观测事件未被接受")
        recovered = machine.state == State.RECONNECTED
    except KeyboardInterrupt:
        error, category = "用户中断本轮实验", "cancelled"
        machine.fail(error)
    except Exception as exc:
        error = str(exc)
        category = getattr(exc, "category", "observation_timeout" if isinstance(exc, TimeoutError)
                           else "environment_error" if step_number == 0 else "test_error")
        machine.fail(error)
        try:
            snapshots.append(observer.snapshot("failure"))
        except Exception as snapshot_error:
            diagnostics.append({"category": "failure_snapshot_error", "error": str(snapshot_error)})
    finally:
        try:
            cleanup = session.close() or {"errors": []}
        except Exception as exc:
            cleanup = {"errors": [str(exc)]}
        if cleanup.get("errors"):
            diagnostics.append({"category": "cleanup_error", "errors": cleanup["errors"]})
            if error is None:
                error, category = "实验清理失败", "cleanup_error"
                machine.fail(error)
    last_sta = snapshots[-1].get("sta", {}) if snapshots else {}
    result = {"schema_version": 1, "run_id": context.run_id, "simulated": session.simulated,
              "backend": chosen, "scenario": scenario if session.simulated else None,
              "execution_status": "completed" if recovered and error is None else "failed",
              "state": machine.state.value, "failed_step": step_number if error else None,
              "error_category": category, "error": error, "diagnostics": diagnostics,
              "cleanup_status": "failed" if cleanup.get("errors") else "completed",
              "disconnect_observed": any(t.get("to") == "DISCONNECTED" for t in machine.transitions),
              "recovery": "pass" if recovered else ("fail" if step_number == 4 else "not_evaluated"),
              "security_profile": "pass" if recovered and profile_matches(last_sta) else "not_evaluated",
              "client_process_unchanged": _continuity(snapshots),
              "auth_path": "unknown", "authorization_timing": "insufficient_evidence",
              "security_verdict": "not_evaluated",
              "covered_transitions": sorted({t["from"] + "->" + t["to"] for t in machine.transitions})}
    metadata = session.metadata()
    metadata.update({"case": case, "runner_backend": chosen, "simulated": session.simulated,
                     "scenario": scenario if session.simulated else None})
    artifacts = {"meta.json": metadata, "result.json": result, "snapshots.json": snapshots,
                 "context.json": asdict(context), "observations.json": observer.observations,
                 "runner-cleanup.json": cleanup}
    try:
        for name, value in artifacts.items():
            _save(out / name, value)
        event_file = "events.jsonl" if session.simulated else "runner-events.jsonl"
        for name, rows in {event_file: [asdict(e) for e in session.events()],
                           "actions.jsonl": [asdict(r) for r in executor.receipts],
                           "transitions.jsonl": machine.transitions}.items():
            (out / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    except OSError as exc:
        raise BackendError("artifact_write_error", str(exc)) from exc
    return result, out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="client_disconnect_reconnect")
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.yaml")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal")
    parser.add_argument("--backend", choices=["mock", "real"])
    parser.add_argument("--client", choices=["stock", "framework"], default="stock")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat 必须为正整数")
    try:
        case = load_case(args.cases, args.case)
        if (args.backend or case["backend"]) == "real" and args.scenario != "normal":
            parser.error("真实模式不能使用 mock 的 scenario")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    rows = []
    for _ in range(args.repeat):
        try:
            result, out = run_case(case, args.output, args.scenario, backend=args.backend, client=args.client)
        except (BackendError, OSError, ValueError) as exc:
            print(json.dumps({"error_category": getattr(exc, "category", "environment_error"),
                              "error": str(exc)}, ensure_ascii=False))
            return 1
        rows.append({"result": result, "output": str(out.resolve())})
        print(json.dumps(rows[-1], ensure_ascii=False, indent=2), flush=True)
        # 资源清理失败时阻止下一轮，避免污染或覆盖现场。
        if result["cleanup_status"] == "failed" or result["error_category"] == "cancelled":
            break
    if args.repeat > 1:
        batch = {"requested": args.repeat, "executed": len(rows),
                 "passed": sum(r["result"]["execution_status"] == "completed" for r in rows), "runs": rows}
        _save(args.output / ("batch-" + uuid.uuid4().hex + ".json"), batch)
    return 0 if len(rows) == args.repeat and all(r["result"]["execution_status"] == "completed" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
