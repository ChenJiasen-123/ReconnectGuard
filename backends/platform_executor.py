"""真实动作的最小适配器；后续动作由江天豪在此扩展。"""
from datetime import datetime, timezone
import time
from model.contracts import ActionReceipt, BackendError

COMMANDS = {"client_disconnect": ("sta", "DISCONNECT"),
            "client_reconnect": ("sta", "RECONNECT")}


def now():
    return datetime.now(timezone.utc).isoformat()


class PlatformExecutor:
    def __init__(self, lab):
        self.lab = lab
        self.receipts = []

    def execute(self, action, context):
        if context.run_id != self.lab.run_id:
            raise BackendError("invalid_context", "Executor 与 Lab 的 run_id 不一致")
        if action not in COMMANDS:
            raise BackendError("unsupported_action", action)
        self.lab.check_health()
        target, command = COMMANDS[action]
        began, stamp = time.monotonic(), now()
        response, category = None, None
        try:
            response = self.lab.command(target, command, action, allow_rejected=True)
            accepted = response == "OK"
            if not accepted:
                category = "action_rejected"
        except TimeoutError:
            accepted, category = False, "action_timeout"
        except OSError:
            accepted, category = False, "control_unavailable"
        receipt = ActionReceipt(
            action_id=f"{context.run_id}:action:{len(self.receipts)+1}",
            action=action, accepted=accepted, run_id=context.run_id,
            attempt_id=context.attempt_id, target=target, command=command,
            raw_response=response, started_at_utc=stamp, finished_at_utc=now(),
            duration_ms=round((time.monotonic()-began)*1000, 3), error_category=category)
        self.receipts.append(receipt)
        return receipt
