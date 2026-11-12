"""实际安全检查器：状态一致性 + 授权时序。

加载名 ``observer.oracle:Oracle``。Runner / comparison 每轮新建实例并调用::

    evaluate(*, context, snapshots, events) -> list[CheckResult]

设计底线（对应 docs/phase5-handoff.md 与 wang-midterm-code-handoff.md）：

- 不固定返回 pass。每条判定都由本轮事件或快照驱动，并附合法证据引用。
- pass / fail 只引用本轮 ``event_id`` 或 ``snapshots.json#/i``。帧号不是
  evaluate() 认可的引用，只能写进 reason 或证据报告。
- AP 的 AUTHORIZED flag、STA 的 COMPLETED、恢复成功，都不能单独推出授权
  时序通过。缺少 EAPOL / 密钥安装的帧级时序证据一律 insufficient_evidence。
- 恢复事件之后的失败快照不倒推为通过；迟到旧尝试事件不冒充当前尝试。
"""
from __future__ import annotations

import json
import math

from model.evaluation import CheckResult

from .evidence import (AUTH_STAGES, EAPOL_STAGES, KEY_STAGE, build_report,
                       collect_frame_evidence, event_ref, frame_ref,
                       partition_events, snapshot_ref, validate_frame_evidence)

CONSISTENCY = "state_consistency"
TIMING = "authorization_timing"


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _data(event):
    return _get(event, "data", {}) or {}


def _kind(event):
    return str(_get(event, "kind", ""))


def _sta_state(snapshot):
    if not isinstance(snapshot, dict):
        return "unknown"
    sta = snapshot.get("sta")
    value = sta.get("wpa_state") if isinstance(sta, dict) else None
    return value if isinstance(value, str) and value else "unknown"


def _ap_verdict(snapshot):
    if not isinstance(snapshot, dict):
        return "unknown"
    value = snapshot.get("port_authorized")
    if value in ("authorized", "not_authorized"):
        return value
    ap_state = snapshot.get("ap_state")
    if ap_state == "authorized":
        return "authorized"
    if ap_state == "observed_not_authorized":
        return "not_authorized"
    return "unknown"


def _sequence_problems(events):
    problems, seen, run_ids = [], set(), set()
    last_seq = last_time = None
    for event in events:
        event_id = event_ref(event)
        if not event_id or event_id in seen:
            problems.append("event_id 缺失或重复")
        seen.add(event_id)
        seq = _get(event, "seq", None)
        if isinstance(seq, int) and not isinstance(seq, bool):
            if last_seq is not None and seq <= last_seq:
                problems.append("seq 非严格递增")
            last_seq = seq
        clock = _get(event, "monotonic_s", None)
        if isinstance(clock, (int, float)) and not isinstance(clock, bool) and math.isfinite(clock):
            if last_time is not None and clock < last_time:
                problems.append("monotonic_s 倒序")
            last_time = clock
        run_id = _get(event, "run_id", None)
        if run_id:
            run_ids.add(run_id)
    if len(run_ids) > 1:
        problems.append("run_id 不一致")
    return problems


def _process_continuity(snapshots):
    """返回 (是否失败, 引用, 原因)。没有进程证据时不判失败。"""
    epochs = []
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            continue
        if snapshot.get("same_processes_alive") is False:
            return True, [snapshot_ref(index)], "本轮内客户端或采集进程退出，违反同一进程连续性约定"
        value = snapshot.get("process_epoch")
        if value is not None:
            epochs.append((index, json.dumps(value, sort_keys=True, ensure_ascii=False)))
    distinct = {value for _, value in epochs}
    if len(distinct) > 1:
        return True, [snapshot_ref(index) for index, _ in epochs[:2]], "快照中的进程世代不一致，疑似客户端重启"
    return False, [], ""


def _state_consistency(context, snapshots, events, report):
    main, late, unproven = partition_events(events)
    problems = _sequence_problems(main)
    if problems:
        refs = [event_ref(event) for event in main]
        return CheckResult(CONSISTENCY, "fail", [ref for ref in refs if ref],
                           "事件链不自洽：" + "；".join(sorted(set(problems))))

    failed, refs, reason = _process_continuity(snapshots)
    if failed:
        return CheckResult(CONSISTENCY, "fail", refs, reason)

    baseline = [event for event in main if _kind(event) == "baseline_confirmed"]
    restored = [event for event in main if _kind(event) == "connection_restored"]
    missing = []
    if not baseline:
        missing.append("baseline_confirmed 标准事件")
    if not restored:
        missing.append("connection_restored 标准事件")
    if len(snapshots) < 3:
        missing.append("基线/断开/恢复三时点快照")
    if missing:
        return CheckResult(CONSISTENCY, "insufficient_evidence", [],
                           "证据不足，缺少" + "、".join(missing) + "；不把恢复成功当成一致性通过")

    contradictions, decidable = [], []
    for index, snapshot in enumerate(snapshots):
        sta, ap = _sta_state(snapshot), _ap_verdict(snapshot)
        if sta == "COMPLETED" and ap == "not_authorized":
            contradictions.append(index)
        if sta != "unknown" and ap != "unknown":
            decidable.append(index)
    if not decidable:
        return CheckResult(CONSISTENCY, "insufficient_evidence", [],
                           "所有快照的双端状态字段均为 unknown 或缺失，无法判定一致性")

    if contradictions and (len(contradictions) >= 2 or contradictions[-1] == len(snapshots) - 1):
        refs = [snapshot_ref(index) for index in contradictions[:2]]
        refs += [event_ref(event) for event in restored[:1]]
        return CheckResult(CONSISTENCY, "fail", [ref for ref in refs if ref],
                           "STA 已 COMPLETED 而 AP 明确未授权；不一致持续到最后快照或多次出现，"
                           "不以恢复事件覆盖失败快照")

    final_index = len(snapshots) - 1
    final_sta, final_ap = _sta_state(snapshots[final_index]), _ap_verdict(snapshots[final_index])
    if final_sta == "unknown" or final_ap == "unknown":
        return CheckResult(CONSISTENCY, "insufficient_evidence", [],
                           "最终快照缺少 STA 状态或 AP 授权字段，无法完成双端一致性判定")
    final_refs = [snapshot_ref(final_index)] + [event_ref(event) for event in restored[-1:]]
    if final_sta != "COMPLETED" or final_ap != "authorized":
        return CheckResult(CONSISTENCY, "fail", [ref for ref in final_refs if ref],
                           f"最终快照为 STA={final_sta} / AP={final_ap}，与已接受的恢复证据冲突")

    refs = [event_ref(baseline[0]), event_ref(restored[-1]),
            snapshot_ref(0), snapshot_ref(final_index)]
    if contradictions:
        refs.append(snapshot_ref(contradictions[0]))
    reason = "基线、断开、恢复三时点双端状态自洽，事件均属本轮且 seq 递增"
    if contradictions:
        reason += "；检测到 1 个短暂双端不同步快照，后续快照已消解"
    if unproven:
        reason += "；存在归属未证实事件，结论只覆盖控制事件链"
    if late:
        reason += f"；{len(late)} 个迟到旧尝试事件已排除，不冒充当前尝试"
    return CheckResult(CONSISTENCY, "pass", [ref for ref in dict.fromkeys(refs) if ref], reason)


def _authorization_timing(context, snapshots, events, report):
    frames = collect_frame_evidence(snapshots, events)
    if not frames:
        return CheckResult(TIMING, "insufficient_evidence", [],
                           "缺少本轮 EAPOL / 密钥安装的帧级时序证据；AP AUTHORIZED flag、"
                           "STA COMPLETED 与恢复成功均不能证明授权时序")

    current_attempt = _get(context, "attempt_id", None)
    context_run_id = _get(context, "run_id", None)
    issues, chosen = [], None
    for item in frames:
        evidence = item["evidence"]
        ok, why, timeline = validate_frame_evidence(evidence)
        if not ok:
            issues.append(f"{item['ref']}: {'；'.join(why)}")
            continue
        if evidence.get("attempt_id") is None:
            issues.append(f"{item['ref']}: 未声明 attempt_id，归属未证实")
            continue
        if evidence["attempt_id"] != current_attempt:
            issues.append(f"{item['ref']}: attempt_id={evidence['attempt_id']} 不属于当前尝试 {current_attempt}")
            continue
        if evidence.get("run_id") and context_run_id and evidence["run_id"] != context_run_id:
            issues.append(f"{item['ref']}: run_id 与本轮不一致")
            continue
        chosen = (item, timeline, evidence)
        break
    if chosen is None:
        return CheckResult(TIMING, "insufficient_evidence", [],
                           "帧级证据不可用或归属不明：" + "；".join(issues))

    item, timeline, evidence = chosen
    by_stage = {}
    for row in timeline:
        by_stage.setdefault(row["stage"], row)
    main, _, _ = partition_events(events)
    restored = [event for event in main if _kind(event) == "connection_restored"]
    refs = [item["ref"]] + ([event_ref(restored[-1])] if restored else [])
    refs = [ref for ref in dict.fromkeys(refs) if ref]

    missing = [stage for stage in EAPOL_STAGES + (KEY_STAGE,) if stage not in by_stage]
    auth_rows = [by_stage[stage] for stage in AUTH_STAGES if stage in by_stage]
    if missing or not auth_rows:
        absent = missing if missing else list(AUTH_STAGES)
        return CheckResult(TIMING, "insufficient_evidence", [],
                           f"帧级证据缺少 {absent} 阶段；可能是缓存复用路径或采集缺口，"
                           "不凭部分时序判 pass，也不把缓存存在当成缓存复用")

    eapol_times = [by_stage[stage]["monotonic_s"] for stage in EAPOL_STAGES]
    if any(later <= earlier for earlier, later in zip(eapol_times, eapol_times[1:])):
        return CheckResult(TIMING, "fail", refs,
                           "四次握手时序异常：" + " → ".join(
                               frame_ref(by_stage[stage]["frame"]) for stage in EAPOL_STAGES))

    key_time = by_stage[KEY_STAGE]["monotonic_s"]
    early = [row for row in auth_rows if row["monotonic_s"] < key_time]
    same = [row for row in auth_rows if row["monotonic_s"] == key_time]
    key_frame = frame_ref(by_stage[KEY_STAGE]["frame"])
    if early:
        return CheckResult(TIMING, "fail", refs,
                           "端口授权或数据帧早于密钥安装：" + "、".join(
                               f"{row['stage']}@{frame_ref(row['frame'])}" for row in early)
                           + f"；密钥安装于 {key_frame}")
    if same:
        return CheckResult(TIMING, "insufficient_evidence", [],
                           "授权/数据帧与密钥安装时间戳相同，无法区分先后，保留证据不足")
    return CheckResult(TIMING, "pass", refs,
                       "帧级证据显示密钥安装先于端口授权与数据帧："
                       + " → ".join([key_frame] + [frame_ref(row["frame"]) for row in auth_rows]))


class Oracle:
    """检查器工厂即类本身，无参构造；每轮由 load_evaluator 新建实例。

    ``last_report`` 保存本轮证据归类结果，供联调查看；不写盘、不修改证据。
    """

    def __init__(self):
        self.last_report = None

    def evaluate(self, *, context, snapshots, events):
        snapshots = list(snapshots or [])
        events = list(events or [])
        self.last_report = build_report(context, snapshots, events)
        return [_state_consistency(context, snapshots, events, self.last_report),
                _authorization_timing(context, snapshots, events, self.last_report)]
