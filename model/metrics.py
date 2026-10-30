"""仅统计模型已接受的证据，不使用原始候选事件计算恢复耗时。"""
import math
from .efsm import RULES


def transition_ids():
    return {f"{source.value}|{kind}|{target.value}" for (source, kind), target in RULES.items()}


def summarize_transitions(transitions):
    by_kind = {t["kind"]: t for t in transitions if "kind" in t}
    def duration(start, end):
        a, b = by_kind.get(start), by_kind.get(end)
        if not a or not b:
            return {"value": None, "evidence": []}
        seconds = b["monotonic_s"] - a["monotonic_s"]
        return {"value": round(seconds * 1000, 3) if math.isfinite(seconds) and seconds >= 0 else None,
                "evidence": [a["event_id"], b["event_id"]]}
    covered = {f'{t["from"]}|{t["kind"]}|{t["to"]}' for t in transitions if "kind" in t}
    eligible = transition_ids()
    return {"timings_ms": {
        "disconnect_request_to_restored": duration("disconnect_requested", "connection_restored"),
        "disconnect_observed_to_restored": duration("sta_disconnected", "connection_restored"),
        "reconnect_request_to_restored": duration("reconnect_requested", "connection_restored")},
        "model_coverage": {"scope": "declared_flow_transitions_v1", "total": len(eligible),
                           "covered": sorted(covered & eligible), "missing": sorted(eligible - covered)}}
