"""流程状态只表示已有证据，不表示安全属性已通过。"""
from enum import Enum
from .contracts import Context, Event


class State(str, Enum):
    INIT = "INIT"
    CONNECTED = "CONNECTED"
    DISCONNECTING = "DISCONNECTING"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    RECONNECTED = "RECONNECTED"
    FAILED = "FAILED"


class InvalidTransition(ValueError):
    pass


RULES = {
    (State.INIT, "baseline_confirmed"): State.CONNECTED,
    (State.CONNECTED, "disconnect_requested"): State.DISCONNECTING,
    (State.DISCONNECTING, "sta_disconnected"): State.DISCONNECTED,
    (State.DISCONNECTED, "reconnect_requested"): State.RECONNECTING,
    (State.RECONNECTING, "connection_restored"): State.RECONNECTED,
}


class EFSM:
    def __init__(self, context: Context):
        self.context = context
        self.state = State.INIT
        self.transitions = []
        self.seen = set()

    def allowed_actions(self) -> set[str]:
        return {State.CONNECTED: {"client_disconnect"},
                State.DISCONNECTED: {"client_reconnect"}}.get(self.state, set())

    def apply(self, event: Event, after: int = -1) -> bool:
        if (event.run_id != self.context.run_id
                or event.attempt_id != self.context.attempt_id
                or event.seq <= after or event.event_id in self.seen):
            return False
        target = RULES.get((self.state, event.kind))
        if target is None:
            raise InvalidTransition(f"{self.state.value}: {event.kind}")
        expected_source = "runner" if event.kind.endswith("requested") else "sta"
        if event.source != expected_source:
            return False
        if event.kind in {"baseline_confirmed", "connection_restored"}:
            if not profile_matches(event.data):
                raise InvalidTransition("连接证据或安全配置不符合要求")
        self.seen.add(event.event_id)
        self.context.evidence_refs.append(event.event_id)
        self.transitions.append({"from": self.state.value, "to": target.value,
                                 "event_id": event.event_id, "kind": event.kind,
                                 "attempt_id": event.attempt_id})
        self.state = target
        return True

    def fail(self, reason: str):
        self.transitions.append({"from": self.state.value, "to": State.FAILED.value,
                                 "reason": reason, "attempt_id": self.context.attempt_id})
        self.state = State.FAILED


def profile_matches(data: dict) -> bool:
    return (data.get("wpa_state") == "COMPLETED" and data.get("key_mgmt") == "SAE"
            and data.get("pairwise_cipher") == "CCMP" and data.get("pmf") == "2")
