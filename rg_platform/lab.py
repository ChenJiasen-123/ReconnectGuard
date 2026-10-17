"""仅管理 hwsim 实验资源，不触碰现有 wlan 接口。"""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from .control import Control

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "vendor/wifi-framework"


def utc():
    return datetime.now(timezone.utc).isoformat()


def run(*args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.STDOUT, text=True).strip()


def fields(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def hwsim_phys():
    return sorted(p.name for p in Path("/sys/class/ieee80211").glob("phy*")
                  if "mac80211_hwsim" in str((p / "device").resolve()))


class Lab:
    def __init__(self, mode):
        if mode not in ("framework", "stock"):
            raise ValueError(mode)
        self.mode = mode
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex()
        self.out = ROOT / "results" / self.run_id
        self.out.mkdir(parents=True)
        self.runtime = Path("/run/reconnectguard") / self.run_id
        self.runtime.mkdir(parents=True)
        self.processes, self.namespaces, self.threads = [], [], []
        self.stop_event = threading.Event()
        self.loaded = False
        self.events = []
        self.phys = []
        self.lock = None
        self.meta = {"run_id": self.run_id, "simulated": False, "mode": mode,
                     "scope": "platform smoke only", "created_at": utc(), "actions": []}

    def save(self, name, value):
        (self.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def start(self):
        self.lock = open("/run/reconnectguard/platform.lock", "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if Path("/sys/module/mac80211_hwsim").exists():
            raise RuntimeError("hwsim already loaded; refuse to reset existing radios")
        run("modprobe", "mac80211_hwsim", "radios=2")
        self.loaded = True
        self.phys = hwsim_phys()
        if len(self.phys) != 2:
            raise RuntimeError("expected exactly two newly created hwsim phys")
        suffix = self.run_id[-6:]
        self.ap_if, self.sta_if = "rgap" + suffix, "rgst" + suffix
        for phy, role, iface in zip(self.phys, ("ap", "sta"), (self.ap_if, self.sta_if)):
            ns = "rg-" + role + "-" + suffix
            run("ip", "netns", "add", ns)
            self.namespaces.append(ns)
            run("iw", "phy", phy, "set", "netns", "name", ns)
            listing = run("ip", "netns", "exec", ns, "iw", "dev")
            original = next(line.strip().split()[1] for line in listing.splitlines()
                            if line.strip().startswith("Interface "))
            run("ip", "-n", ns, "link", "set", original, "name", iface)
            run("ip", "-n", ns, "link", "set", "lo", "up")
        self.ap_ctrl = Path("/var/run/hostapd") / self.ap_if
        sta_dir = Path("/var/run/wpa_supplicant")
        self.sta_ctrl = sta_dir / self.sta_if
        ap_template = (ROOT / "configs/ap.conf").read_text()
        sta_template = (ROOT / "configs/sta.conf").read_text()
        self.ap_config = self.runtime / "ap.conf"
        self.sta_config = self.runtime / "sta.conf"
        self.ap_config.write_text(ap_template)
        self.sta_config.write_text(sta_template)
        os.chmod(self.sta_config, 0o600)
        fw_python = FW / "setup/venv/bin/python"
        ap_binary = FW / "dependencies/hostap_2_10/hostapd/hostapd"
        sta_binary = (FW / "dependencies/hostap_2_10/wpa_supplicant/wpa_supplicant"
                      if self.mode == "framework" else Path("/usr/sbin/wpa_supplicant"))
        self.spawn(self.namespaces[0], [fw_python, FW / "hostap.py", self.ap_if,
                   "--ap", "--config", self.ap_config, "--binary", ap_binary, "--debug", "1"], "ap")
        if self.mode == "framework":
            sta_cmd = [fw_python, FW / "hostap.py", self.sta_if,
                       "--config", self.sta_config, "--binary", sta_binary, "--debug", "1"]
        else:
            sta_cmd = [sta_binary, "-Dnl80211", "-i", self.sta_if, "-c", self.sta_config, "-dd"]
        self.spawn(self.namespaces[1], sta_cmd, "sta")
        self.wait_controls()
        self.spawn(self.namespaces[0], ["tcpdump", "-i", "mon" + self.ap_if,
                   "-s", "0", "-U", "-w", self.out / "trace.pcap"], "capture")
        time.sleep(.3)
        if self.processes[-1][1].poll() is not None:
            raise RuntimeError("capture failed; see capture.log")
        for role, endpoint in (("ap", self.ap_ctrl), ("sta", self.sta_ctrl)):
            listener = Control(endpoint)
            listener.attach()
            thread = threading.Thread(target=self.listen, args=(role, listener), daemon=True)
            thread.start()
            self.threads.append(thread)
        # 网络配置初始 disabled，确保监听开始后才放行第一次连接。
        self.command("sta", "ENABLE_NETWORK 0", "initial_connect")
        self.meta.update({"kernel": run("uname", "-r"), "framework_commit": run("git", "-C", FW, "rev-parse", "HEAD"),
                          "submodules": run("git", "-C", FW, "submodule", "status"),
                          "ap_iface": self.ap_if, "sta_iface": self.sta_if,
                          "namespaces": self.namespaces, "phys": self.phys,
                          "processes": [{"role": r, "pid": p.pid, "process_epoch": epoch}
                                        for r, p, epoch in self.processes],
                          "binary_sha256": {"ap": hashlib.sha256(ap_binary.read_bytes()).hexdigest(),
                                            "sta": hashlib.sha256(sta_binary.read_bytes()).hexdigest()},
                          "profile": {"key_mgmt": "SAE", "pairwise": "CCMP", "pmf": "required"},
                          "initial_policy": "cold_start", "cache_clear": "not_requested"})
        self.save("meta.json", self.meta)

    def spawn(self, ns, cmd, role):
        log = open(self.out / (role + ".log"), "w")
        proc = subprocess.Popen(["ip", "netns", "exec", ns] + [str(x) for x in cmd],
                                cwd=FW, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        self.processes.append((role, proc, utc()))

    def wait_controls(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if any(p.poll() is not None for _, p, _ in self.processes):
                raise RuntimeError("daemon exited; see ap.log / sta.log")
            if self.ap_ctrl.exists() and self.sta_ctrl.exists():
                return
            time.sleep(.1)
        raise TimeoutError("control sockets not ready")

    def command(self, target, command, semantic, allow_rejected=False):
        fw = target == "ap" or self.mode == "framework"
        endpoint = self.ap_ctrl if target == "ap" else self.sta_ctrl
        start = time.monotonic()
        with Control(endpoint, framework=fw) as ctrl:
            response = ctrl.request(command)
        receipt = {"run_id": self.run_id, "action_id": len(self.meta["actions"]) + 1,
                   "target": target, "semantic": semantic, "command": command,
                   "response": response, "received_at": utc(),
                   "duration_ms": round((time.monotonic() - start) * 1000, 2),
                   "effect_status": "unverified"}
        self.meta["actions"].append(receipt)
        if (response.startswith("FAIL") or "UNKNOWN COMMAND" in response) and not allow_rejected:
            raise RuntimeError(str(receipt))
        return response

    def listen(self, role, ctrl):
        import socket
        try:
            with open(self.out / (role + "-events.jsonl"), "a") as log:
                while not self.stop_event.is_set():
                    try:
                        event = ctrl.sock.recv(65535).decode(errors="replace")
                        self.events.append((role, event, time.monotonic()))
                        log.write(json.dumps({"run_id": self.run_id, "source": role,
                                  "received_at": utc(), "monotonic_ns": time.monotonic_ns(),
                                  "raw_event": event, "attempt_id": None}) + "\n")
                        log.flush()
                    except socket.timeout:
                        pass
        finally:
            ctrl.close()

    def status(self):
        return fields(self.command("sta", "STATUS", "read_status"))

    def wait_connected(self, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.status()
            if state.get("wpa_state") == "COMPLETED":
                if (state.get("key_mgmt") != "SAE" or state.get("pairwise_cipher") != "CCMP"
                        or state.get("pmf") != "2"):
                    raise RuntimeError("unexpected negotiated security: " + str(state))
                return state
            time.sleep(.2)
        raise TimeoutError("WPA3 connection timeout")

    def snapshot(self, checkpoint):
        began = utc()
        sta = self.status()
        ap = self.command("ap", "STA " + sta["address"], "read_ap_station", allow_rejected=True) if "address" in sta else "unknown"
        cache = self.command("sta", "PMKSA", "read_pmksa", allow_rejected=True)
        record = {"checkpoint": checkpoint, "read_started_at": began, "read_finished_at": utc(),
                  "sta": sta, "ap_raw": ap, "ap_query_available": not (ap.startswith("FAIL") or "UNKNOWN COMMAND" in ap or ap == "unknown"),
                  "sta_pmksa_raw": cache,
                  "auth_path": "unknown", "key_lifecycle": "unknown"}
        self.save(checkpoint + ".json", record)
        return record

    def stop(self):
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=1)
        errors = []
        import signal
        for role, proc, _ in reversed(self.processes):
            try:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=3)
            except Exception as exc:
                errors.append(role + ": " + str(exc))
        for ns in reversed(self.namespaces):
            try:
                run("ip", "netns", "del", ns)
            except Exception as exc:
                errors.append(str(exc))
        if self.loaded:
            # 只在 hwsim 仍完全属于本次创建的集合时卸载。
            remaining = hwsim_phys()
            if set(remaining).issubset(set(self.phys)):
                try:
                    run("modprobe", "-r", "mac80211_hwsim")
                except Exception as exc:
                    errors.append(str(exc))
            else:
                errors.append("foreign hwsim radios detected; not unloading module")
        self.meta["finished_at"] = utc()
        self.save("meta.json", self.meta)
        self.save("cleanup.json", {"errors": errors})
        active = Path("/run/reconnectguard/active.json")
        if active.exists() and json.loads(active.read_text()).get("run_id") == self.run_id:
            active.unlink()
        shutil.rmtree(self.runtime)
        if self.lock:
            self.lock.close()


def main():
    parser = argparse.ArgumentParser(description="陈家森阶段二：虚拟无线平台验证")
    parser.add_argument("--client", choices=["framework", "stock"], default="stock")
    from .probes import PROBES
    parser.add_argument("--probe", choices=sorted(PROBES), default="deauth")
    parser.add_argument("--hold", action="store_true", help="验证后保持环境；scripts/stop.sh 可关闭")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("需 sudo；只在 hwsim 中执行")
    lab = Lab(args.client)
    try:
        lab.start()
        lab.wait_connected()
        PROBES[args.probe](lab)
        print(json.dumps({"platform_smoke": "completed", "results": str(lab.out)}), flush=True)
        if args.hold:
            active = {"pid": os.getpid(), "run_id": lab.run_id, "root": str(ROOT),
                      "proc_start": Path("/proc/self/stat").read_text().split()[21]}
            Path("/run/reconnectguard/active.json").write_text(json.dumps(active))
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("stopping owned lab resources", flush=True)
    except Exception as exc:
        lab.save("error.json", {"classification": "environment_error", "error": str(exc)})
        raise
    finally:
        lab.stop()


if __name__ == "__main__":
    main()
