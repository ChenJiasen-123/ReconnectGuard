"""回归：断开后 AP 查询不到 STA，不应使状态采集崩溃。"""
import tempfile
import unittest
from pathlib import Path
import sys
if sys.platform != "linux":
    raise unittest.SkipTest("Lab 需要 Linux fcntl 与网络命名空间")
from rg_platform.lab import Lab


class SnapshotTest(unittest.TestCase):
    def test_station_absent_after_disconnect(self):
        lab = Lab.__new__(Lab)
        lab.status = lambda: {"address": "02:00:00:00:00:01", "wpa_state": "DISCONNECTED"}
        def query(target, command, semantic, allow_rejected=False):
            if command.startswith("STA "):
                if not allow_rejected:
                    raise RuntimeError("FAIL")
                return "FAIL"
            return "Index / AA / PMKID"
        lab.command = query
        with tempfile.TemporaryDirectory() as tmp:
            lab.out = Path(tmp)
            result = lab.snapshot("post_disconnect")
            self.assertEqual(result["ap_raw"], "FAIL")
            self.assertFalse(result["ap_query_available"])
            self.assertEqual(result["sta"]["wpa_state"], "DISCONNECTED")
            self.assertEqual(result["auth_path"], "unknown")
