"""独立命令/事件连接；支持 Framework 前缀与原版标准协议。"""
import socket
import tempfile
import time
from pathlib import Path


class Control:
    def __init__(self, endpoint, framework=False, timeout=3):
        self.tmp = tempfile.TemporaryDirectory(prefix="rg-ctrl-")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(str(Path(self.tmp.name) / "client"))
        self.sock.settimeout(timeout)
        self.sock.connect(str(endpoint))
        self.framework = framework
        self.timeout = timeout

    def request(self, command):
        prefix = "> " if self.framework else ""
        self.sock.send((prefix + command).encode())
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.sock.settimeout(max(.01, deadline - time.monotonic()))
            reply = self.sock.recv(65535).decode(errors="replace")
            if prefix:
                if reply.startswith(prefix):
                    return reply[len(prefix):].strip()
            elif not reply.startswith("<"):
                return reply.strip()
        raise TimeoutError(command)

    def attach(self):
        # ATTACH 属于标准控制命令，不使用 Framework request 前缀。
        self.sock.send(b"ATTACH")
        if self.sock.recv(4096).strip() != b"OK":
            raise RuntimeError("event ATTACH failed")
        self.sock.settimeout(.2)

    def close(self):
        self.sock.close()
        self.tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
