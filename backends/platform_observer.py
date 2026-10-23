"""Observer adapter over Lab's shared event journal and snapshot API."""
from dataclasses import asdict
import json
import re
import time

from model.contracts import Context, Event, BackendError


SUPPORTED_EVENTS = {"baseline_confirmed", "sta_disconnected", "connection_restored"}
PROFILE_FIELDS = ("wpa_state", "key_mgmt", "pairwise_cipher", "pmf")


class PlatformObserver:
    """Normalize real Lab evidence without opening another control socket."""

    def __init__(self, lab):
        self.lab = lab
        self.active = False
        self.delivered: set[tuple] = set()
        self.events: list[Event] = []
        self.observations: list[dict] = []

    def start(self) -> None:
        if not hasattr(self.lab, "event_journal"):
            raise RuntimeError("Lab does not expose the shared event journal")
        self.active = True

    def mark(self) -> int:
        self._require_active()
        return self.lab.event_journal.mark()

    def wait_event(self, expected: str, after: int, timeout: float,
                   context: Context) -> Event:
        self._require_active()
        if expected not in SUPPORTED_EVENTS:
            raise ValueError(f"unsupported observed event: {expected}")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        cursor = after
        while True:
            if hasattr(self.lab, "check_health"):
                self.lab.check_health()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{expected}: no matching event within {timeout}s")
            records = self.lab.event_journal.wait_after(cursor, min(remaining, .2))
            if not records:
                continue
            for record in records:
                cursor = max(cursor, record.seq)
                event, reason = self._normalize(record, expected, after, context)
                if event is None:
                    self._ignore(record, expected, reason)
                    continue
                fingerprint = (event.run_id, event.attempt_id, event.source, event.kind)
                if fingerprint in self.delivered:
                    self._ignore(record, expected, "semantic_duplicate")
                    continue
                self.delivered.add(fingerprint)
                self.events.append(event)
                self._append_jsonl("observer-events.jsonl", asdict(event))
                return event

    def snapshot(self, checkpoint: str) -> dict:
        self._require_active()
        if hasattr(self.lab, "check_health"):
            self.lab.check_health()
        try:
            raw = self.lab.snapshot(checkpoint)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError("collection_error", "快照采集失败: " + str(exc)) from exc
        ap_raw = raw.get("ap_raw", "unknown")
        ap_available = bool(raw.get("ap_query_available"))
        flags = _ap_flags(ap_raw) if ap_available else set()
        if "AUTHORIZED" in flags:
            ap_state = "authorized"
            port_authorized = "authorized"
        elif ap_available:
            ap_state = "observed_not_authorized"
            port_authorized = "not_authorized"
        else:
            ap_state = "unknown"
            port_authorized = "unknown"

        processes = []
        for role, process, epoch in getattr(self.lab, "processes", []):
            processes.append({"role": role, "pid": process.pid,
                              "process_epoch": epoch, "alive": process.poll() is None})
        normalized = {
            "run_id": self.lab.run_id,
            "checkpoint": checkpoint,
            "simulated": False,
            "sta": raw.get("sta", {}),
            "ap_state": ap_state,
            "ap_flags": sorted(flags),
            "pmksa_presence": _pmksa_presence(raw.get("sta_pmksa_raw", "unknown")),
            "auth_path": raw.get("auth_path", "unknown"),
            "key_state": raw.get("key_lifecycle", "unknown"),
            "port_authorized": port_authorized,
            "process_epoch": {item["role"]: item["process_epoch"] for item in processes},
            "same_processes_alive": all(item["alive"] for item in processes) if processes else "unknown",
            "processes": processes,
            "raw_snapshot_ref": f"{checkpoint}.json",
        }
        try:
            self.lab.save(checkpoint + "-observer.json", normalized)
        except OSError as exc:
            raise BackendError("artifact_write_error", str(exc)) from exc
        return normalized

    def close(self) -> None:
        self.active = False

    def _normalize(self, record, expected: str, after: int,
                   context: Context) -> tuple[Event | None, str | None]:
        if record.seq <= after:
            return None, "before_cursor"
        if record.run_id != context.run_id:
            return None, "wrong_run"
        if record.attempt_id is not None and record.attempt_id != context.attempt_id:
            return None, "wrong_attempt"
        if record.source != "sta":
            return None, "wrong_source"
        raw = record.raw_event
        if expected == "sta_disconnected":
            if "CTRL-EVENT-DISCONNECTED" not in raw:
                return None, "unexpected_kind"
            data = self._base_data(record)
        else:
            if "CTRL-EVENT-CONNECTED" not in raw:
                return None, "unexpected_kind"
            try:
                status = self.lab.status()
            except Exception as exc:
                self._ignore(record, expected, "status_error:" + str(exc))
                raise BackendError("collection_error", "STATUS 查询失败: " + str(exc)) from exc
            if status.get("wpa_state") != "COMPLETED":
                return None, "status_not_connected"
            data = self._base_data(record)
            data.update({name: status.get(name) for name in PROFILE_FIELDS})
            data["status"] = status
        event = Event(
            event_id=f"{record.run_id}:raw:{record.seq}:{expected}",
            run_id=record.run_id,
            attempt_id=context.attempt_id,
            source="sta",
            kind=expected,
            seq=record.seq,
            monotonic_s=record.monotonic_s,
            data=data,
        )
        return event, None

    @staticmethod
    def _base_data(record) -> dict:
        return {
            "raw_event": record.raw_event,
            "received_at": record.received_at,
            "evidence_ref": record.evidence_ref,
            "attempt_attribution": ("recorder" if record.attempt_id is not None
                                    else "received_after_cursor"),
        }

    def _ignore(self, record, expected: str, reason: str | None) -> None:
        value = {"run_id": record.run_id, "seq": record.seq,
                 "evidence_ref": record.evidence_ref, "expected": expected,
                 "ignored": reason or "not_matched"}
        self.observations.append(value)
        self._append_jsonl("observer-observations.jsonl", value)

    def _append_jsonl(self, name: str, value: dict) -> None:
        try:
            with open(self.lab.out / name, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise BackendError("artifact_write_error", name + ": " + str(exc)) from exc

    def _require_active(self) -> None:
        if not self.active:
            raise RuntimeError("Observer.start() must be called first")


def _ap_flags(raw: str) -> set[str]:
    match = re.search(r"^flags=(.*)$", raw, re.MULTILINE)
    return set(re.findall(r"\[([^]]+)]", match.group(1))) if match else set()


def _pmksa_presence(raw: str) -> str:
    if not raw or raw == "unknown" or raw.startswith("FAIL") or "UNKNOWN COMMAND" in raw:
        return "unknown"
    rows = [line for line in raw.splitlines() if line.strip()]
    return "present" if any(re.match(r"^\d+\s+", line) for line in rows) else "absent"
