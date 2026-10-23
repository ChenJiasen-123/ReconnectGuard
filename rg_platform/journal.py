"""Thread-safe, single-run journal for platform and runner events."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import threading
import time


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RawEvent:
    run_id: str
    seq: int
    source: str
    raw_event: str
    received_at: str
    monotonic_ns: int
    attempt_id: int | None = None

    @property
    def monotonic_s(self) -> float:
        return self.monotonic_ns / 1_000_000_000

    @property
    def evidence_ref(self) -> str:
        return f"events.jsonl#seq={self.seq}"

    def to_dict(self) -> dict:
        value = asdict(self)
        value["monotonic_s"] = self.monotonic_s
        return value


class EventJournal:
    """Assign one sequence in receive order and allow independent readers."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._records: list[RawEvent] = []
        self._condition = threading.Condition()

    def append(self, source: str, raw_event: str, attempt_id: int | None = None,
               received_at: str | None = None,
               monotonic_ns: int | None = None) -> RawEvent:
        with self._condition:
            record = RawEvent(
                run_id=self.run_id,
                seq=len(self._records) + 1,
                source=source,
                raw_event=raw_event,
                received_at=received_at or utc_now(),
                monotonic_ns=monotonic_ns if monotonic_ns is not None else time.monotonic_ns(),
                attempt_id=attempt_id,
            )
            self._records.append(record)
            self._condition.notify_all()
            return record

    def mark(self) -> int:
        with self._condition:
            return len(self._records)

    def records_after(self, after: int) -> list[RawEvent]:
        with self._condition:
            return [record for record in self._records if record.seq > after]

    def wait_after(self, after: int, timeout: float) -> list[RawEvent]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not any(record.seq > after for record in self._records):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)
            return [record for record in self._records if record.seq > after]
