"""可运行的构造样例：正常、异常、证据不足。

所有数据是合成样例，``synthetic=true``。帧号 ``trace.pcap#frame=N`` 只演示
引用格式，**不是真实抓包帧号**；真实帧号需在 Kali hwsim 上补齐。

用法::

    python3 -m observer.demo --list
    python3 -m observer.demo --scenario normal
    python3 -m observer.demo --scenario all
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from model.contracts import Context, Event
from model.evaluation import evaluate

from .oracle import Oracle

RUN_ID = "20261110-demo-0001"
PROFILE = {"wpa_state": "COMPLETED", "key_mgmt": "SAE",
           "pairwise_cipher": "CCMP", "pmf": "2", "address": "02:00:00:00:01:00"}
AP_PORT = {"authorized": "authorized",
           "observed_not_authorized": "not_authorized",
           "unknown": "unknown"}


def snapshot(checkpoint, *, sta="COMPLETED", ap="authorized", pmksa="present",
             epoch="epoch-1", alive=True, frame_evidence=None, auth_path="unknown"):
    value = {
        "checkpoint": checkpoint,
        "simulated": False,
        "sta": dict(PROFILE, wpa_state=sta) if sta != "unknown" else {},
        "ap_state": ap,
        "port_authorized": AP_PORT[ap],
        "pmksa_presence": pmksa,
        "auth_path": auth_path,
        "key_state": "unknown",
        "process_epoch": {"ap": epoch, "sta": epoch},
        "same_processes_alive": alive,
        "raw_snapshot_ref": checkpoint + ".json",
    }
    if frame_evidence is not None:
        value["frame_evidence"] = frame_evidence
    return value


def event(kind, seq, clock, attempt, *, data=None):
    source = "runner" if kind.endswith("requested") else "sta"
    return Event(event_id=f"{RUN_ID}:raw:{seq}:{kind}", run_id=RUN_ID,
                 attempt_id=attempt, source=source, kind=kind, seq=seq,
                 monotonic_s=clock, data=data or {})


def events(extra=None):
    rows = [
        event("baseline_confirmed", 11, 101.00, 1),
        event("disconnect_requested", 12, 101.10, 1),
        event("sta_disconnected", 13, 101.20, 1),
        event("reconnect_requested", 14, 101.30, 2),
        event("connection_restored", 15, 101.50, 2),
    ]
    return rows + list(extra or [])


def frame_evidence(*, attempt=2, bad_order=False):
    timeline = [
        {"frame": 1201, "stage": "sae_commit", "monotonic_s": 101.20},
        {"frame": 1202, "stage": "sae_confirm", "monotonic_s": 101.24},
        {"frame": 1203, "stage": "eapol_m1", "monotonic_s": 101.30},
        {"frame": 1204, "stage": "eapol_m2", "monotonic_s": 101.33},
        {"frame": 1205, "stage": "eapol_m3", "monotonic_s": 101.36},
        {"frame": 1206, "stage": "eapol_m4", "monotonic_s": 101.39},
        {"frame": 1207, "stage": "key_install", "monotonic_s": 101.42},
        {"frame": 1208, "stage": "port_authorized", "monotonic_s": 101.43},
        {"frame": 1209, "stage": "data_frame", "monotonic_s": 101.45},
    ]
    if bad_order:
        timeline = [row for row in timeline if row["stage"] != "port_authorized"]
        timeline.insert(6, {"frame": 1208, "stage": "port_authorized", "monotonic_s": 101.41})
    return {"source": "trace.pcap", "run_id": RUN_ID, "attempt_id": attempt,
            "sae_observed": True, "pmkid_match": None, "timeline": timeline}


def baseline_snapshots():
    return [
        snapshot("connected_baseline"),
        snapshot("after_disconnect", sta="DISCONNECTED", ap="observed_not_authorized"),
        snapshot("after_reconnect"),
    ]


def build_scenario(name):
    """返回 (context, snapshots, events)。"""
    context = Context(RUN_ID, attempt_id=2, reconnect_mode="explicit")
    snapshots, rows = baseline_snapshots(), events()
    if name == "normal":
        pass
    elif name == "frame_ok":
        snapshots[-1]["frame_evidence"] = frame_evidence()
    elif name == "frame_bad":
        snapshots[-1]["frame_evidence"] = frame_evidence(bad_order=True)
    elif name == "old_attempt_frame":
        snapshots[-1]["frame_evidence"] = frame_evidence(attempt=1)
    elif name == "persistent_mismatch":
        snapshots[1] = snapshot("after_disconnect", sta="COMPLETED", ap="observed_not_authorized")
        snapshots[2] = snapshot("after_reconnect", sta="COMPLETED", ap="observed_not_authorized")
    elif name == "transient_mismatch":
        snapshots[1] = snapshot("after_disconnect", sta="COMPLETED", ap="observed_not_authorized")
    elif name == "missing_evidence":
        snapshots = [snapshot("connected_baseline", ap="unknown"),
                     snapshot("after_disconnect", ap="unknown"),
                     snapshot("after_reconnect", ap="unknown")]
    elif name == "pmksa_present_only":
        for item in snapshots:
            item["pmksa_presence"] = "present"
        snapshots[-1]["auth_path"] = "full_sae"
    elif name == "late_old_attempt":
        rows = events([event("connection_restored", 19, 101.90, 1,
                             data={"attempt_attribution": "received_after_cursor"})])
    elif name == "process_restart":
        snapshots[-1]["process_epoch"] = {"ap": "epoch-2", "sta": "epoch-2"}
    else:
        raise ValueError(f"未知场景: {name}")
    return context, snapshots, rows


SCENARIOS = {
    "normal": "正常路径；无帧级证据，授权时序应保留证据不足",
    "frame_ok": "完整帧级时序，密钥安装先于授权，两项检查均 pass",
    "frame_bad": "端口授权早于密钥安装，授权时序 fail",
    "old_attempt_frame": "帧级证据属于旧尝试，归属不明，保留证据不足",
    "persistent_mismatch": "双端持续不一致，状态一致性 fail",
    "transient_mismatch": "单个短暂不同步且后续消解，状态一致性 pass 并注明",
    "missing_evidence": "AP 侧字段全 unknown，状态一致性证据不足",
    "pmksa_present_only": "缓存 present 且快照自称 full_sae，仍判 unknown",
    "late_old_attempt": "迟到旧尝试事件被排除，不冒充当前尝试",
    "process_restart": "进程世代变化，状态一致性 fail",
}


def run_scenario(name):
    context, snapshots, rows = build_scenario(name)
    oracle = Oracle()
    checks = evaluate(oracle, context, snapshots, rows)
    report = oracle.last_report
    return {
        "scenario": name,
        "description": SCENARIOS[name],
        "synthetic": True,
        "evaluator": "observer.oracle:Oracle",
        "run_id": RUN_ID,
        "checks": [asdict(check) for check in checks],
        "evidence": {
            "auth_path": report["auth_path"]["value"],
            "auth_path_reason": report["auth_path"]["reason"],
            "cache_present_count": report["cache"]["present_count"],
            "cache_reuse_claimed": report["cache"]["reuse_claimed"],
            "old_or_unattributed": report["attempt_boundary"]["old_or_unattributed"],
            "frame_refs": report["frame_refs"],
        },
        "note": "构造数据；trace.pcap#frame=N 仅演示引用格式，真实帧号需在 Kali 补齐",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="normal", help="场景名，或 all 运行全部")
    parser.add_argument("--list", action="store_true", help="列出场景及说明")
    args = parser.parse_args(argv)
    if args.list:
        for name, description in SCENARIOS.items():
            print(f"{name:24s} {description}")
        return 0
    names = sorted(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        print(json.dumps(run_scenario(name), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
