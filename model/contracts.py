"""上层数据契约；不依赖 Linux 或平台实现。"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Event:
    event_id: str
    run_id: str
    attempt_id: int | None
    source: str
    kind: str
    seq: int
    monotonic_s: float
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ActionReceipt:
    action_id: str
    action: str
    accepted: bool
    effect_status: str = "unverified"


@dataclass
class Context:
    run_id: str
    attempt_id: int = 1
    disconnect_method: str = "client_disconnect"
    reconnect_mode: str = "explicit"
    pmksa_presence: str = "unknown"
    auth_path: str = "unknown"
    key_state: str = "unknown"
    port_authorized: str = "unknown"
    evidence_refs: list[str] = field(default_factory=list)


class Executor(Protocol):
    def execute(self, action: str, context: Context) -> ActionReceipt: ...


class Observer(Protocol):
    def start(self) -> None: ...
    def mark(self) -> int: ...
    def wait_event(self, expected: str, after: int, timeout: float,
                   context: Context) -> Event: ...
    def snapshot(self, checkpoint: str) -> dict: ...
    def close(self) -> None: ...
