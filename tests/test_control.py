"""使用实际 UNIX datagram 服务验证两种命令协议，而非无线模拟结果。"""
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from rg_platform.control import Control


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "需要 UNIX datagram socket")
class ControlTest(unittest.TestCase):
    def exchange(self, framework, expected, reply, event=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "server")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            server.bind(path)
            server.settimeout(2)
            received = []
            def serve():
                data, peer = server.recvfrom(4096)
                received.append(data)
                if event:
                    server.sendto(event, peer)
                server.sendto(reply, peer)
            thread = threading.Thread(target=serve)
            thread.start()
            try:
                with Control(path, framework=framework, timeout=1) as ctrl:
                    result = ctrl.request("STATUS")
                thread.join(2)
                self.assertEqual(received, [expected])
                return result
            finally:
                server.close()

    def test_stock_standard_protocol(self):
        self.assertEqual(self.exchange(False, b"STATUS", b"wpa_state=COMPLETED\n"), "wpa_state=COMPLETED")

    def test_framework_prefixed_reply_after_event(self):
        self.assertEqual(self.exchange(True, b"> STATUS", b"> wpa_state=COMPLETED\n", b"<3>EVENT"), "wpa_state=COMPLETED")

    def test_rejection_is_preserved(self):
        self.assertEqual(self.exchange(False, b"STATUS", b"FAIL\n"), "FAIL")


if __name__ == "__main__":
    unittest.main()
