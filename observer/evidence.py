"""认证路径、缓存行为与尝试边界的证据归类。

只读模块。输入是 Runner 交付的 context / snapshots / events，输出是可解释的
结构化报告。它不下发动作、不改事件、不判定漏洞，也不把「缓存条目存在」当成
「本次复用」，不把「恢复成功」当成「安全通过」。

帧级证据契约（新提案，当前 platform_observer.py 尚不产出该字段）::

    {
      "source": "trace.pcap",
      "run_id": "<本轮 run_id>",
      "attempt_id": 2,
      "sae_observed": true,          # 或 false，配合 pmkid_match 判缓存复用
      "pmkid_match": null,           # true/false/null，无证据一律 null
      "timeline": [
        {"frame": 1201, "stage": "sae_commit",      "monotonic_s": 100.10},
        {"frame": 1202, "stage": "eapol_m1",        "monotonic_s": 100.21},
        {"frame": 1203, "stage": "key_install",     "monotonic_s": 100.42},
        {"frame": 1204, "stage": "port_authorized", "monotonic_s": 100.43},
        {"frame": 1205, "stage": "data_frame",      "monotonic_s": 100.45}
      ]
    }

``stage`` 取值：sae_commit / sae_confirm / eapol_m1..m4 / key_install /
port_authorized / data_frame。时间使用本机单调时钟秒；跨主机不可直接比较。

用法：``python3 -m observer.evidence results/<run_id> [--output report.json]``
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace

EAPOL_STAGES = ("eapol_m1", "eapol_m2", "eapol_m3", "eapol_m4")
KEY_STAGE = "key_install"
AUTH_STAGES = ("port_authorized", "data_frame")
SAE_STAGES = ("sae_commit", "sae_confirm")
ALL_STAGES = frozenset(SAE_STAGES + EAPOL_STAGES + (KEY_STAGE,) + AUTH_STAGES)
CACHE_FIELDS = "pmksa_presence"

EVIDENCE_REF_FORMATS = {
    "event": "events.jsonl#seq=N；标准事件 event_id 形如 <run_id>:raw:<seq>:<kind>",
    "snapshot": "snapshots.json#/i；第 i 个标准快照（0 起）",
    "frame": "trace.pcap#frame=N；只写进 reason 或本报告，不能作为 CheckResult.evidence_refs",
}

LIMITATIONS = (
    "pmksa_presence=present 只证明缓存表有条目，不证明本次重连复用了该缓存。",
    "AP 的 AUTHORIZED flag 只表示读取快照那一刻的标志，不证明授权时序。",
    "STA 的 COMPLETED 与恢复成功是流程结果，不是安全检查通过。",
    "seq 是本机收到控制事件的顺序，不等于空口帧真实发生顺序。",
    "attempt_id 是测试器有证据区分的连接尝试，不等于每一次协议重传。",
    "未标注 attempt_id 的原始事件只能按接收游标归属，不能据此证明协议尝试归属。",
    "帧号引用不属于 model.evaluation.evaluate 认可的 evidence_refs，只能写入 reason。",
)


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _data(event):
    return _get(event, "data", {}) or {}


def snapshot_ref(index):
    return f"snapshots.json#/{index}"


def frame_ref(frame):
    return f"trace.pcap#frame={frame}"


def event_ref(event):
    return str(_get(event, "event_id", ""))


def collect_frame_evidence(snapshots, events):
    """找出本轮出现的帧级证据，并附上其在 evaluate 中的合法引用位置。"""
    items = []
    for index, snapshot in enumerate(snapshots):
        value = snapshot.get("frame_evidence") if isinstance(snapshot, dict) else None
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, dict):
                items.append({"ref": snapshot_ref(index), "carrier": "snapshot",
                              "evidence": candidate})
    for event in events:
        value = _data(event).get("frame_evidence")
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, dict):
                items.append({"ref": event_ref(event), "carrier": "event",
                              "evidence": candidate})
    return items


def validate_frame_evidence(evidence):
    """结构校验。返回 (结构是否可用, 问题描述, 时间线)。

    缺少某个阶段不算结构错误：那可能是缓存复用路径或采集缺口，由调用方
    保留 insufficient_evidence，而不是直接判失败。
    """
    issues = []
    timeline = evidence.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        return False, ["timeline 缺失或为空"], []
    previous = None
    for row in timeline:
        if not isinstance(row, dict):
            return False, ["timeline 条目不是对象"], []
        frame, stage, clock = row.get("frame"), row.get("stage"), row.get("monotonic_s")
        if isinstance(frame, bool) or not isinstance(frame, (int, float)) or frame <= 0:
            issues.append("frame 编号无效")
        if stage not in ALL_STAGES:
            issues.append(f"未知 stage: {stage}")
        if isinstance(clock, bool) or not isinstance(clock, (int, float)) or not math.isfinite(clock):
            issues.append(f"{stage} 的 monotonic_s 无效")
        elif previous is not None and clock < previous:
            issues.append(f"{stage} 的时间早于前一项")
        else:
            previous = clock
    if issues:
        return False, sorted(set(issues)), []
    return True, [], timeline


def summarize_cache(snapshots):
    """三时点缓存观测。present 永远不等于 reused。"""
    checkpoints = []
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            continue
        value = snapshot.get(CACHE_FIELDS, "unknown")
        checkpoints.append({
            "ref": snapshot_ref(index),
            "checkpoint": snapshot.get("checkpoint"),
            "pmksa_presence": value if value in ("present", "absent", "unknown") else "unknown",
            "attempt_id": snapshot.get("attempt_id"),
        })
    return {
        "checkpoints": checkpoints,
        "present_count": sum(row["pmksa_presence"] == "present" for row in checkpoints),
        "reuse_claimed": False,
        "note": "present 仅表示缓存表有条目；是否复用必须有本次 attempt 的 PMKID / SAE 帧级证据。",
    }


def classify_auth_path(context, snapshots, events):
    """分类 full_sae / pmksa_reuse / unknown。无帧级证据一律 unknown。"""
    reasons = []
    frames = collect_frame_evidence(snapshots, events)
    current_attempt = _get(context, "attempt_id", None)
    value, matched = "unknown", None
    for item in frames:
        evidence = item["evidence"]
        ok, issues, timeline = validate_frame_evidence(evidence)
        if not ok:
            reasons.append(f"{item['ref']}: 帧级证据结构不完整 ({'；'.join(issues)})")
            continue
        if evidence.get("attempt_id") is not None and evidence["attempt_id"] != current_attempt:
            reasons.append(f"{item['ref']}: 帧级证据 attempt_id={evidence['attempt_id']} 不属于当前尝试 {current_attempt}")
            continue
        context_run_id = _get(context, "run_id", None)
        if evidence.get("run_id") and context_run_id and evidence["run_id"] != context_run_id:
            reasons.append(f"{item['ref']}: 帧级证据 run_id 与本轮不一致")
            continue
        stages = [row["stage"] for row in timeline]
        has_eapol = all(stage in stages for stage in EAPOL_STAGES)
        has_sae = any(stage in stages for stage in SAE_STAGES) or evidence.get("sae_observed") is True
        if has_eapol and has_sae:
            value, matched = "full_sae", item
            reasons.append(f"{item['ref']}: 本次尝试有 SAE 与完整四次握手帧级证据")
            break
        if has_eapol and evidence.get("pmkid_match") is True and evidence.get("sae_observed") is False:
            value, matched = "pmksa_reuse", item
            reasons.append(f"{item['ref']}: 本次尝试有 PMKID 匹配且明确无 SAE")
            break
        reasons.append(f"{item['ref']}: 握手段存在，但缺少区分完整 SAE 与缓存复用的证据")
    declared = [snapshot.get("auth_path") for snapshot in snapshots
                if isinstance(snapshot, dict) and snapshot.get("auth_path") not in (None, "unknown")]
    if declared:
        reasons.append(f"快照声明 auth_path={sorted(set(map(str, declared)))} 但缺少帧级佐证，不采信")
    if value == "unknown":
        reasons.append("缓存条目存在不等于缓存复用；恢复成功不等于认证路径已判定")
        reasons.append("缺少本轮 attempt 的 SAE / PMKID / 握手段帧级证据，保留 unknown")
    return {
        "value": value,
        "reason": "；".join(reasons) if reasons else "无可用证据",
        "matched_evidence_ref": matched["ref"] if matched else None,
        "frame_evidence_count": len(frames),
        "cache": summarize_cache(snapshots),
    }


def partition_events(events):
    """按 seq 排序，找出「尝试编号倒退」的迟到旧事件。

    基线与断开阶段 attempt_id=1、恢复阶段为 2，是同一轮的合法序列；只有
    在更大的 attempt_id 已经出现之后又出现更小值，才是迟到的旧尝试事件。
    """
    ordered = sorted(events, key=lambda event: _get(event, "seq", 0) or 0)
    main, late = [], []
    max_attempt = None
    for event in ordered:
        attempt = _get(event, "attempt_id", None)
        if isinstance(attempt, int) and max_attempt is not None and attempt < max_attempt:
            late.append(event)
            continue
        main.append(event)
        if isinstance(attempt, int) and (max_attempt is None or attempt > max_attempt):
            max_attempt = attempt
    unproven = [event for event in main
                if _data(event).get("attempt_attribution") == "received_after_cursor"]
    return main, late, unproven


def summarize_attempts(context, events):
    """区分本轮请求、本轮观测、迟到旧尝试与归属未证实的事件。"""
    current = _get(context, "attempt_id", None)
    main, late, unproven = partition_events(events)
    requests, observations, others = [], [], []

    def row_for(event, classification=None):
        row = {
            "event_id": event_ref(event),
            "kind": str(_get(event, "kind", "")),
            "seq": _get(event, "seq", None),
            "attempt_id": _get(event, "attempt_id", None),
            "monotonic_s": _get(event, "monotonic_s", None),
            "evidence_ref": _data(event).get("evidence_ref"),
            "attempt_attribution": _data(event).get("attempt_attribution"),
        }
        if classification:
            row["classification"] = classification
        return row

    for event in late:
        others.append(row_for(event, "late_old_attempt"))
    for event in unproven:
        others.append(row_for(event, "attribution_unproven"))
    unproven_ids = {id(event) for event in unproven}
    for event in main:
        if id(event) in unproven_ids:
            continue
        if str(_get(event, "kind", "")).endswith("requested"):
            requests.append(row_for(event))
        else:
            observations.append(row_for(event))
    return {
        "run_id": _get(context, "run_id", None),
        "current_attempt": current,
        "requests": requests,
        "observations": observations,
        "old_or_unattributed": others,
        "rule": "迟到的旧尝试事件不参与当前判定；received_after_cursor 只表示接收窗口，不证明协议归属。",
    }


def collect_frame_refs(snapshots, events):
    refs = []
    for item in collect_frame_evidence(snapshots, events):
        timeline = item["evidence"].get("timeline")
        if not isinstance(timeline, list):
            continue
        for row in timeline:
            if isinstance(row, dict) and isinstance(row.get("frame"), (int, float)) and not isinstance(row.get("frame"), bool):
                refs.append(frame_ref(row["frame"]))
    return sorted(set(refs), key=lambda value: float(value.rsplit("=", 1)[1]))


def build_report(context, snapshots, events):
    """汇总证据报告。纯函数，可被 Oracle 与命令行共用。"""
    return {
        "schema_version": 1,
        "run_id": _get(context, "run_id", None),
        "scope": "evidence classification; not a security verdict",
        "auth_path": classify_auth_path(context, snapshots, events),
        "cache": summarize_cache(snapshots),
        "attempt_boundary": summarize_attempts(context, events),
        "frame_refs": collect_frame_refs(snapshots, events),
        "evidence_ref_formats": EVIDENCE_REF_FORMATS,
        "limitations": list(LIMITATIONS),
        "security_verdict": "not_evaluated",
    }


def load_run(directory):
    """从一轮结果目录读取产物；事件与快照允许是 dict 或 dataclass 风格对象。"""
    path = Path(directory)
    snapshots = json.loads((path / "snapshots.json").read_text(encoding="utf-8"))
    events_file = path / "runner-events.jsonl"
    if not events_file.exists():
        events_file = path / "events.jsonl"
    events = []
    if events_file.exists():
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    context_file = path / "context.json"
    raw = json.loads(context_file.read_text(encoding="utf-8")) if context_file.exists() else {}
    if not raw and (path / "result.json").exists():
        raw = json.loads((path / "result.json").read_text(encoding="utf-8"))
    context = SimpleNamespace(
        run_id=raw.get("run_id"), attempt_id=raw.get("attempt_id"),
        reconnect_mode=raw.get("reconnect_mode"), observations=raw.get("observations", []))
    return context, snapshots, events


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="一轮结果目录，含 snapshots.json 与 runner-events.jsonl")
    parser.add_argument("--output", type=Path, help="另存报告路径；默认打印到标准输出")
    args = parser.parse_args(argv)
    context, snapshots, events = load_run(args.run_dir)
    text = json.dumps(build_report(context, snapshots, events), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"证据报告已写入 {args.output}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
