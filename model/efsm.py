"""流程状态只表示已有证据，不表示安全属性已通过。"""
from enum import Enum
import math
from .contracts import Context, Event


class State(str, Enum):
    INIT = "INIT"
    CONNECTED = "CONNECTED"
    DISCONNECTING = "DISCONNECTING"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    RECONNECTED = "RECONNECTED"
    FAILURE_HANDLING = "FAILURE_HANDLING"
    FAILED = "FAILED"


class InvalidTransition(ValueError):
    pass


DISCONNECT_ACTIONS = frozenset({"client_disconnect", "ap_deauth", "ap_disassoc"})
RECONNECT_ACTIONS = frozenset({"client_reconnect"})


RULES = {
    (State.INIT, "baseline_confirmed"): State.CONNECTED,
    (State.CONNECTED, "disconnect_requested"): State.DISCONNECTING,
    (State.DISCONNECTING, "sta_disconnected"): State.DISCONNECTED,
    (State.DISCONNECTED, "reconnect_requested"): State.RECONNECTING,
    (State.RECONNECTING, "connection_restored"): State.RECONNECTED,
    (State.DISCONNECTED, "connection_restored"): State.RECONNECTED,
}


class EFSM:
    def __init__(self, context: Context):
        self.context = context
        self.state = State.INIT
        self.transitions = []
        self.seen = set()
        self.last_seq = -1
        self.last_time = float("-inf")

    def allowed_actions(self) -> set[str]:
        return {State.CONNECTED: set(DISCONNECT_ACTIONS),
                State.DISCONNECTED: set(RECONNECT_ACTIONS),
                State.FAILURE_HANDLING: set(RECONNECT_ACTIONS)}.get(self.state, set())

    def apply(self, event: Event, after: int = -1) -> bool:
        if (event.run_id != self.context.run_id
                or event.attempt_id != self.context.attempt_id
                or event.seq <= max(after, self.last_seq) or event.event_id in self.seen):
            return False
        expected_source = "runner" if event.kind.endswith("requested") else "sta"
        if event.source != expected_source:
            return False
        if not math.isfinite(event.monotonic_s) or event.monotonic_s < self.last_time:
            raise InvalidTransition("事件时间倒序")
        if (self.state == State.DISCONNECTED and event.kind == "connection_restored"
                and self.context.reconnect_mode != "automatic"):
            raise InvalidTransition("显式重连模式缺少重连请求")
        target = RULES.get((self.state, event.kind))
        if self.state == State.FAILURE_HANDLING and event.kind == "reconnect_requested":
            target = State.RECONNECTING
        if target is None:
            raise InvalidTransition(f"{self.state.value}: {event.kind}")
        if event.kind in {"baseline_confirmed", "connection_restored"}:
            if not profile_matches(event.data):
                raise InvalidTransition("连接证据或安全配置不符合要求")
        self.last_seq, self.last_time = event.seq, event.monotonic_s
        self.seen.add(event.event_id)
        self.context.evidence_refs.append(event.event_id)
        self.transitions.append({"from": self.state.value, "to": target.value,
                                 "event_id": event.event_id, "kind": event.kind,
                                 "attempt_id": event.attempt_id, "seq": event.seq,
                                 "monotonic_s": event.monotonic_s})
        self.state = target
        return True

    def prepare_retry(self, reason):
        if self.state != State.RECONNECTING or self.context.reconnect_mode != "explicit":
            raise InvalidTransition("仅显式重连等待超时后允许失败恢复")
        self.transitions.append({"from": self.state.value, "to": State.FAILURE_HANDLING.value,
                                 "reason": reason, "attempt_id": self.context.attempt_id})
        self.state = State.FAILURE_HANDLING

    def fail(self, reason: str):
        self.transitions.append({"from": self.state.value, "to": State.FAILED.value,
                                 "reason": reason, "attempt_id": self.context.attempt_id})
        self.state = State.FAILED


def profile_matches(data: dict) -> bool:
    return (data.get("wpa_state") == "COMPLETED" and data.get("key_mgmt") == "SAE"
            and data.get("pairwise_cipher") == "CCMP" and data.get("pmf") == "2")
