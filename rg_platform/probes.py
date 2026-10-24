"""底层平台能力验证样例；不替代团队 Executor / Oracle。"""
from dataclasses import asdict
import time

from backends.platform_observer import PlatformObserver
from model.contracts import Context


def connect(lab):
    lab.snapshot("connected_baseline")


def _ap_disconnect(lab, command, action, scope):
    """Smoke-test one AP-side management-frame disconnect action."""
    before = lab.snapshot("connected_baseline")
    mac = before["sta"]["address"]
    began = time.monotonic()
    lab.command("ap", f"{command} {mac} reason=3", action)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        seen_sta = any(r == "sta" and "CTRL-EVENT-DISCONNECTED" in e and t >= began for r,e,t in lab.events)
        seen_ap = any(r == "ap" and "AP-STA-DISCONNECTED" in e and mac in e and t >= began for r,e,t in lab.events)
        if seen_sta and seen_ap:
            break
        time.sleep(.05)
    else:
        raise TimeoutError(f"{action} response received but bilateral disconnect events missing")
    # 快速自动重连可能先于快照；保留实际状态，不强行标为断开。
    lab.snapshot("post_" + action.removeprefix("ap_") + "_observed")
    lab.wait_connected()
    after = lab.snapshot("post_reconnect")
    epochs = all(p.poll() is None for _, p, _ in lab.processes)
    lab.save("platform-check.json", {"scope": scope, "simulated": False,
              "same_processes_alive": epochs, "elapsed_ms": round((time.monotonic()-began)*1000,2),
              "final_sta_state": after["sta"].get("wpa_state"),
              "disconnect_action": action, "disconnect_effect": "bilateral_disconnect_events_observed",
              "auth_path": "unknown",
              "authorization_timing": "unknown", "security_verdict": "not_evaluated"})


def deauth(lab):
    # 仅验证陈家森的平台 AP 控制能力；不实现其他成员完整 PoC / Oracle。
    _ap_disconnect(lab, "DEAUTHENTICATE", "ap_deauth", "AP deauth control smoke")


def disassoc(lab):
    # 与 deauth 使用相同的双端事件和恢复检查，动作语义单独保留。
    _ap_disconnect(lab, "DISASSOCIATE", "ap_disassoc", "AP disassoc control smoke")


def reconnect(lab):
    lab.snapshot("connected_baseline")
    lab.command("sta", "DISCONNECT", "client_disconnect")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if lab.status().get("wpa_state") == "DISCONNECTED":
            break
        time.sleep(.1)
    else:
        raise TimeoutError("client did not enter DISCONNECTED")
    lab.snapshot("post_disconnect")
    lab.command("sta", "RECONNECT", "request_reconnect")
    lab.wait_connected()
    lab.snapshot("post_reconnect")
    lab.save("platform-check.json", {"scope": "client control smoke", "simulated": False,
              "same_processes_alive": all(p.poll() is None for _, p, _ in lab.processes),
              "final_sta_state": "COMPLETED", "auth_path": "unknown", "security_verdict": "not_evaluated"})


def observer_reconnect(lab):
    """Exercise the real Observer while keeping actions outside the adapter."""
    context = Context(lab.run_id)
    observer = PlatformObserver(lab)
    observer.start()
    try:
        baseline = observer.wait_event("baseline_confirmed", 0, 5, context)
        observer.snapshot("connected_baseline")

        cursor = observer.mark()
        lab.command("sta", "DISCONNECT", "client_disconnect")
        disconnected = observer.wait_event("sta_disconnected", cursor, 5, context)
        observer.snapshot("post_disconnect")

        context.attempt_id += 1
        cursor = observer.mark()
        lab.command("sta", "RECONNECT", "request_reconnect")
        restored = observer.wait_event("connection_restored", cursor, 20, context)
        after = observer.snapshot("post_reconnect")
        lab.save("observer-check.json", {
            "scope": "real Observer adapter smoke",
            "simulated": False,
            "events": [asdict(event) for event in (baseline, disconnected, restored)],
            "final_sta_state": after["sta"].get("wpa_state"),
            "same_processes_alive": after["same_processes_alive"],
            "auth_path": after["auth_path"],
            "key_state": after["key_state"],
            "security_verdict": "not_evaluated",
        })
    finally:
        observer.close()


PROBES = {"connect": connect, "deauth": deauth, "disassoc": disassoc,
          "observer-reconnect": observer_reconnect,
          "reconnect": reconnect}
