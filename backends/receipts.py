"""回执效果归属：把「命令被接受」与「动作效果已发生」拆成两件事。

Executor 只能记录 ``accepted`` 和平台原始回执；动作是否真的造成了断开或
恢复，只能由 Observer 之后观测到的事件证明。``ActionReceipt.effect_status``
在契约里存在，但 PlatformExecutor 与 MockBackend 都不写它，``actions.jsonl``
里每条回执恒为 ``unverified``。

本模块按发送顺序把每条回执与它自己的请求事件配对
（``disconnect_requested`` / ``reconnect_requested``），再在「本请求之后、
下一个请求之前」的窗口里找对应效果事件（``sta_disconnected`` /
``connection_restored``）。纯函数，既不依赖 Lab、hwsim 和 sudo，也不需要
重跑实验，可以直接对已有结果目录事后补算。

效果归属只说明「这条命令在自己的窗口内观测到了对应效果事件」，
不等于安全属性通过，也不把 Observer 接收时的 attempt_id 当作协议尝试归属。
"""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

from model.contracts import ActionReceipt, Event

REQUEST_KIND = {"client_disconnect": "disconnect_requested",
                "ap_deauth": "disconnect_requested",
                "ap_disassoc": "disconnect_requested",
                "client_reconnect": "reconnect_requested"}
EFFECT_KIND = {"client_disconnect": "sta_disconnected",
               "ap_deauth": "sta_disconnected",
               "ap_disassoc": "sta_disconnected",
               "client_reconnect": "connection_restored"}
REQUEST_KINDS = frozenset(REQUEST_KIND.values())
EVENT_FILES = ("runner-events.jsonl", "events.jsonl")
RECEIPT_FIELDS = tuple(ActionReceipt.__dataclass_fields__)
EVENT_FIELDS = tuple(Event.__dataclass_fields__)


def as_receipt(row):
    return row if isinstance(row, ActionReceipt) else ActionReceipt(**{k: row.get(k) for k in RECEIPT_FIELDS})


def as_event(row):
    return row if isinstance(row, Event) else Event(**{k: row.get(k) for k in EVENT_FIELDS})


def annotate(receipts, events, run_id=None):
    """返回 ``(回执列表, 归属明细)``。

    回执按动作在列表中的出现顺序与同类型请求事件一一配对；配不上的回执
    保留 ``unverified``，不猜归属。
    """
    receipts = [as_receipt(receipt) for receipt in receipts]
    events = sorted((as_event(event) for event in events), key=lambda event: event.seq)
    if run_id is not None:
        events = [event for event in events if event.run_id == run_id]
    requests = {}
    for event in events:
        if event.kind in REQUEST_KINDS:
            requests.setdefault(event.kind, []).append(event)
    cursor = {kind: 0 for kind in requests}
    request_seqs = sorted(event.seq for rows in requests.values() for event in rows)

    annotated, rows = [], []
    for receipt in receipts:
        row = {"action_id": receipt.action_id, "action": receipt.action, "accepted": receipt.accepted,
               "attempt_id": receipt.attempt_id, "request_event_id": None, "effect_event_id": None,
               "effect_status": "unverified", "attribution": "none", "note": ""}
        kind = REQUEST_KIND.get(receipt.action)
        pending = requests.get(kind, []) if kind else []
        position = cursor.get(kind, 0)
        if position >= len(pending):
            row["note"] = "缺少可配对的请求事件"
            annotated.append(receipt)
            rows.append(row)
            continue
        request = pending[position]
        cursor[kind] = position + 1
        row["request_event_id"], row["attempt_id"] = request.event_id, request.attempt_id
        window_end = next((seq for seq in request_seqs if seq > request.seq), None)
        candidates = [event for event in events
                      if event.kind == EFFECT_KIND.get(receipt.action)
                      and event.seq > request.seq
                      and (window_end is None or event.seq < window_end)]
        if not receipt.accepted:
            row["effect_status"] = "not_observed"
            status = "not_observed"
            row["note"] = "命令未被接受：" + str(receipt.error_category or "unknown")
        elif not candidates:
            row["effect_status"] = "not_observed"
            status = "not_observed"
            row["note"] = "请求后未观测到效果事件"
        else:
            effect = candidates[0]
            row["effect_status"] = "observed"
            status = "observed"
            row["effect_event_id"] = effect.event_id
            same = effect.attempt_id == request.attempt_id
            row["attribution"] = "same_attempt" if same else "attempt_mismatch"
            row["note"] = ("效果事件与请求同属一个 attempt_id" if same
                           else "效果事件的 attempt_id 与请求不同，归属仅到观测时点")
        annotated.append(replace(receipt, effect_status=status))
        rows.append(row)
    return annotated, rows


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def annotate_directory(directory, output_directory=None):
    """对已有结果目录补算效果归属，写 ``actions-annotated.jsonl`` 与归属明细。"""
    directory = Path(directory)
    output = Path(output_directory) if output_directory else directory
    meta_path = directory / 'meta.json'
    run_id = json.loads(meta_path.read_text(encoding="utf-8")).get("run_id") if meta_path.exists() else None
    receipts = [as_receipt(row) for row in load_jsonl(directory / 'actions.jsonl')]
    events = []
    for name in EVENT_FILES:
        if (directory / name).exists():
            events = load_jsonl(directory / name)
            break
    annotated, rows = annotate(receipts, events, run_id)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'actions-annotated.jsonl').write_text(
        "".join(json.dumps(asdict(receipt), ensure_ascii=False) + "\n" for receipt in annotated),
        encoding="utf-8")
    (output / 'actions-effect-report.json').write_text(
        json.dumps({"run_id": run_id, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return annotated, rows


def summarize(rows):
    counts = {}
    for row in rows:
        key = (row["action"], row["effect_status"], row["attribution"])
        counts[key] = counts.get(key, 0) + 1
    return [{"action": action, "effect_status": status, "attribution": attribution, "count": count}
            for (action, status, attribution), count in sorted(counts.items())]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("用法: python3 -m backends.receipts <结果目录> [--output 目录]")
        return 2
    directory, output = argv[0], None
    if "--output" in argv:
        output = argv[argv.index("--output") + 1]
    _, rows = annotate_directory(directory, output)
    for row in rows:
        print(f'{row["action_id"]:<24} {row["action"]:<18} accepted={str(row["accepted"]):<5} '
              f'effect={row["effect_status"]:<12} attribution={row["attribution"]:<16} '
              f'request={row["request_event_id"]} effect_event={row["effect_event_id"]}')
    for block in summarize(rows):
        print(json.dumps(block, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
