"""确定性虚拟时钟；仅验证软件流程，不执行无线操作。"""
from dataclasses import asdict
from model.contracts import ActionReceipt, Context, Event
from model.efsm import DISCONNECT_ACTIONS

SCENARIOS = {"normal", "disconnect_timeout", "reconnect_timeout", "noisy_events"}
PROFILE = {"wpa_state": "COMPLETED", "key_mgmt": "SAE",
           "pairwise_cipher": "CCMP", "pmf": "2"}


class MockBackend:
    def __init__(self, context: Context, scenario: str):
        if scenario not in SCENARIOS:
            raise ValueError(f"未知场景: {scenario}")
        self.context, self.scenario = context, scenario
        self.events, self.receipts, self.observations = [], [], []
        self.clock = 0.0
        self.listening = False
        self.connected = True

    def start(self):
        self.listening = True
        self.emit("baseline_confirmed", data=PROFILE.copy())

    def emit(self, kind, source="sta", data=None, attempt_id=None):
        self.clock += 0.01
        event = Event(f"e{len(self.events)+1}", self.context.run_id,
                      self.context.attempt_id if attempt_id is None else attempt_id,
                      source, kind, len(self.events)+1, self.clock, data or {})
        self.events.append(event)
        return event

    def mark(self):
        return len(self.events)

    def execute(self, action, context):
        if not self.listening:
            raise RuntimeError("必须先启动监听")
        if action not in DISCONNECT_ACTIONS | {"client_reconnect"}:
            raise ValueError(action)
        receipt = ActionReceipt(f"a{len(self.receipts)+1}", action, True)
        self.receipts.append(receipt)
        if action in DISCONNECT_ACTIONS and self.scenario != "disconnect_timeout":
            self.connected = False
            self.emit("sta_disconnected")
            if (action in {"ap_deauth", "ap_disassoc"}
                    and context.reconnect_mode == "automatic"
                    and self.scenario != "reconnect_timeout"):
                self.connected = True
                self.emit("connection_restored", data=PROFILE.copy(),
                          attempt_id=context.attempt_id + 1)
        elif action == "client_reconnect" and self.scenario != "reconnect_timeout":
            if self.scenario == "noisy_events":
                self.emit("connection_restored", data=PROFILE.copy(), attempt_id=1)
                self.emit("connection_restored", source="ap", data=PROFILE.copy())
            self.connected = True
            event = self.emit("connection_restored", data=PROFILE.copy())
            if self.scenario == "noisy_events":
                self.events.append(event)  # 同一消息重复交付，保留原始编号。
        return receipt

    def wait_event(self, expected, after, timeout, context):
        deadline = self.clock + timeout
        for event in self.events:
            if event.kind != expected:
                continue
            reason = None
            if event.seq <= after:
                reason = "before_cursor"
            elif event.run_id != context.run_id or event.attempt_id != context.attempt_id:
                reason = "wrong_context"
            elif event.source != "sta":
                reason = "wrong_source"
            if reason:
                self.observations.append({"event_id": event.event_id, "ignored": reason})
                continue
            return event
        self.clock = deadline
        raise TimeoutError(f"{expected}: {timeout}s 内无匹配事件（虚拟时钟）")

    def snapshot(self, checkpoint):
        return {"checkpoint": checkpoint, "simulated": True,
                "sta": PROFILE.copy() if self.connected else {"wpa_state": "DISCONNECTED"},
                "ap_state": "unknown", "pmksa_presence": "unknown",
                "auth_path": "unknown", "key_state": "unknown",
                "port_authorized": "unknown", "process_epoch": "mock-process-1"}

    def close(self):
        self.listening = False
