"""真实 Wi-Fi 断开与重连动作适配器。

Executor 只负责向实验平台发送控制命令并返回回执。命令返回 ``OK``
只表示控制接口接受了请求；动作是否真的造成断开或恢复，必须由
Observer 根据后续事件确认。
"""
from datetime import datetime, timezone
import re
import time
from model.contracts import ActionReceipt, BackendError

COMMANDS = {"client_disconnect": ("sta", "DISCONNECT"),
            "client_reconnect": ("sta", "RECONNECT")}
AP_COMMANDS = {"ap_deauth": "DEAUTHENTICATE",
                "ap_disassoc": "DISASSOCIATE"}
SUPPORTED_ACTIONS = frozenset(COMMANDS) | frozenset(AP_COMMANDS)
MAC_ADDRESS = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
DEFAULT_REASON = 3


def now():
    return datetime.now(timezone.utc).isoformat()


class PlatformExecutor:
    def __init__(self, lab):
        self.lab = lab
        self.receipts = []

    def execute(self, action, context):
        if context.run_id != self.lab.run_id:
            raise BackendError("invalid_context", "Executor 与 Lab 的 run_id 不一致")
        if action not in SUPPORTED_ACTIONS:
            raise BackendError("unsupported_action", action)
        self.lab.check_health()
        target = "ap" if action in AP_COMMANDS else COMMANDS[action][0]
        began, stamp = time.monotonic(), now()
        command, response, category = None, None, None
        try:
            command = self._command_for(action)
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

    def _command_for(self, action):
        """Build one validated platform command for ``action``.

        AP controls target a specific station. The stock wpa_supplicant
        STATUS response is the platform-owned source of that station address;
        callers do not concatenate an unvalidated value into a control
        command.
        """
        if action in COMMANDS:
            return COMMANDS[action][1]

        status = self.lab.status()
        address = status.get("address") if isinstance(status, dict) else None
        if not isinstance(address, str) or not MAC_ADDRESS.fullmatch(address.strip()):
            raise BackendError("invalid_target", "无法从客户端 STATUS 得到有效 STA MAC 地址")
        return f"{AP_COMMANDS[action]} {address.strip().lower()} reason={DEFAULT_REASON}"
