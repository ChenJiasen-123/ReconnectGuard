"""仅停止本项目记录的、进程启动时间匹配的平台主进程。"""
import json
import os
import signal
import time
from pathlib import Path


def main():
    active = Path("/run/reconnectguard/active.json")
    if not active.exists():
        print("没有保持运行的平台")
        return
    data = json.loads(active.read_text())
    proc = Path("/proc") / str(data["pid"])
    root = Path(__file__).resolve().parents[1]
    if (not proc.exists() or (proc / "stat").read_text().split()[21] != data["proc_start"]):
        raise RuntimeError("平台记录已过期；拒绝按旧 PID 终止其他进程")
    cmd = (proc / "cmdline").read_bytes().split(b"\0")
    if (b"rg_platform.lab" not in cmd or (proc / "cwd").resolve() != root
            or data["root"] != str(root)):
        raise RuntimeError("进程身份不匹配，拒绝停止")
    os.kill(data["pid"], signal.SIGINT)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and active.exists():
        time.sleep(.2)
    if active.exists():
        raise TimeoutError("平台未完成清理；请查看 cleanup.json 和主进程日志")
    print("本实验已停止")


if __name__ == "__main__":
    main()
