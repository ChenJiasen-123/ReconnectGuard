"""三路径对照调度；一轮每个案例运行一次，逐轮轮换起始案例。"""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import statistics
import uuid

from model.metrics import transition_ids
from runner import ROOT, load_case, run_case, _save

CASE_IDS = ("client_disconnect_reconnect", "ap_deauth", "ap_disassoc")


def summarize(rows, case_ids):
    groups = []
    for case_id in case_ids:
        entries = [r for r in rows if r["case_id"] == case_id]
        results = [r["result"] for r in entries if r.get("result")]
        values = [r["timings_ms"]["disconnect_observed_to_restored"]["value"]
                  for r in results if r["recovery"] == "pass"]
        values = [v for v in values if v is not None]
        covered = {t for r in results for t in r["model_coverage"]["covered"]}
        groups.append({"case_id": case_id, "executed": len(entries),
                       "completed": sum(r["execution_status"] == "completed" for r in results),
                       "recovered": sum(r["recovery"] == "pass" for r in results),
                       "timing_samples": len(values),
                       "observed_recovery_median_ms": statistics.median(values) if values else None,
                       "errors": dict(Counter(r.get("error_category") or r.get("result", {}).get("error_category")
                                              for r in entries if r.get("error_category") or r.get("result", {}).get("error_category"))),
                       "covered": sorted(covered), "auth_path": "unknown"})
    return groups


def run_comparison(cases_path, output_root, repeat=10, backend="mock", client="stock", *, run=run_case):
    if repeat < 1 or backend not in {"mock", "real"}:
        raise ValueError("repeat 必须为正数，backend 必须为 mock/real")
    cases = [load_case(cases_path, name) for name in CASE_IDS]
    # 三组使用相同的等待预算。模式差异保留为实验因素。
    budgets = {(c["steps"][1]["timeout_s"], c["steps"][3]["timeout_s"]) for c in cases}
    if len(budgets) != 1:
        raise ValueError("对照案例的断开和恢复等待预算必须相同")
    out = Path(output_root) / ("comparison-" + uuid.uuid4().hex)
    out.mkdir(parents=True, exist_ok=False)
    plan = [{"round": n + 1, "case_id": cases[(n + offset) % len(cases)]["id"]}
            for n in range(repeat) for offset in range(len(cases))]
    config = {"schema_version": 1, "backend": backend, "client": client,
              "simulated": backend == "mock", "repeats_per_case": repeat,
              "cases": cases, "schedule": plan,
              "case_file_sha256": hashlib.sha256(Path(cases_path).read_bytes()).hexdigest(),
              "interpretation": "descriptive_comparison_modes_differ"}
    _save(out / "plan.json", config)
    rows, stop_reason = [], None
    def save_progress():
        groups = summarize(rows, CASE_IDS)
        covered = {t for group in groups for t in group["covered"]}
        report = {**config, "requested": len(plan), "executed": len(rows),
                  "stop_reason": stop_reason, "groups": groups, "runs": rows,
                  "model_coverage": {"scope": "declared_flow_transitions_v1",
                                     "total": len(transition_ids()), "covered": sorted(covered),
                                     "missing": sorted(transition_ids() - covered)},
                  "security_verdict": "not_evaluated"}
        _save(out / "summary.json", report)
        stream = io.StringIO()
        fields = ["case_id", "executed", "completed", "recovered", "timing_samples",
                  "observed_recovery_median_ms", "auth_path"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(groups)
        with (out / "comparison.csv").open("w", encoding="utf-8", newline="") as csv_file:
            csv_file.write(stream.getvalue())
        return report
    report = save_progress()
    by_id = {c["id"]: c for c in cases}
    for item in plan:
        try:
            result, run_out = run(by_id[item["case_id"]], out, backend=backend, client=client)
            rows.append({**item, "result": result, "output": str(Path(run_out).resolve())})
            if result["cleanup_status"] == "failed" or result["error_category"] == "cancelled":
                stop_reason = result["error_category"] or "cleanup_error"
        except KeyboardInterrupt:
            rows.append({**item, "error_category": "cancelled", "error": "用户中断"})
            stop_reason = "cancelled"
        except Exception as exc:
            rows.append({**item, "error_category": getattr(exc, "category", "environment_error"), "error": str(exc)})
            # run_case 未返回时无法证明资源已清理，停止剩余案例。
            stop_reason = rows[-1]["error_category"]
        report = save_progress()
        if stop_reason:
            break
    return report, out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--backend", choices=["mock", "real"], default="mock")
    parser.add_argument("--client", choices=["stock", "framework"], default="stock")
    args = parser.parse_args()
    try:
        report, out = run_comparison(args.cases, args.output, args.repeat, args.backend, args.client)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(out.resolve()), "executed": report["executed"],
                      "requested": report["requested"], "stop_reason": report["stop_reason"],
                      "groups": report["groups"]}, ensure_ascii=False, indent=2))
    return 0 if report["executed"] == report["requested"] and all(g["completed"] == args.repeat for g in report["groups"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
