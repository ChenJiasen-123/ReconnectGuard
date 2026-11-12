"""真实证据提取模块的纯函数回归；不需要 scapy / Linux / hwsim。

pcap 读取通过 patch 替身注入，测试的是帧分类、RSN 解析、时钟换算与
报告组装的保守性（不把缓存存在当复用、不把控制事件当帧）。
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from observer import frames

# 来自真实 hwsim 轮次的 Association Request RSN IE（sta.log 十六进制转写）
RSN_NO_PMKID = bytes.fromhex(
    "0100000fac040100000fac040100000fac08c0000000000fac06")
RSN_WITH_PMKID = bytes.fromhex(
    "0100000fac040100000fac040100000fac08c0000100"
    "440c661a8089039046985a11618e34f7000fac06")


class RsnTest(unittest.TestCase):
    def test_no_pmkid_is_not_reuse(self):
        parsed = frames.parse_rsn_pmkids(RSN_NO_PMKID)
        self.assertEqual(parsed["pmkid_count"], 0)
        self.assertEqual(parsed["pmkids"], [])

    def test_real_pmkid_is_extracted(self):
        parsed = frames.parse_rsn_pmkids(RSN_WITH_PMKID)
        self.assertEqual(parsed["pmkid_count"], 1)
        self.assertEqual(parsed["pmkids"], ["440c661a8089039046985a11618e34f7"])

    def test_truncated_ie_reports_error_instead_of_guessing(self):
        self.assertIn("error", frames.parse_rsn_pmkids(b"\x01\x00"))
        self.assertIn("error", frames.parse_rsn_pmkids(RSN_NO_PMKID[:12]))


class EapolTest(unittest.TestCase):
    def test_real_four_way_key_info(self):
        self.assertEqual(frames.eapol_message(0x0088), "eapol_m1")
        self.assertEqual(frames.eapol_message(0x0108), "eapol_m2")
        self.assertEqual(frames.eapol_message(0x13C8), "eapol_m3")
        self.assertEqual(frames.eapol_message(0x0308), "eapol_m4")

    def test_group_key_and_missing_info_are_not_pairwise_messages(self):
        self.assertIsNone(frames.eapol_message(0x0080))  # 无 pairwise 位
        self.assertIsNone(frames.eapol_message(None))


class ClassifyTest(unittest.TestCase):
    def test_sae_and_open_authentication_are_distinguished(self):
        self.assertEqual(
            frames.classify_frame({"type": 0, "subtype": 11, "auth_algo": 3, "auth_seq": 1}),
            ["sae_commit"])
        self.assertEqual(
            frames.classify_frame({"type": 0, "subtype": 11, "auth_algo": 3, "auth_seq": 2}),
            ["sae_confirm"])
        self.assertEqual(
            frames.classify_frame({"type": 0, "subtype": 11, "auth_algo": 0, "auth_seq": 1}),
            ["open_system_auth"])

    def test_assoc_req_with_pmkid_and_disconnect_frames(self):
        self.assertIn("assoc_req_with_pmkid", frames.classify_frame(
            {"type": 0, "subtype": 0, "rsn_pmkids": ["a" * 32]}))
        self.assertIn("deauth", frames.classify_frame({"type": 0, "subtype": 12}))
        self.assertIn("disassoc", frames.classify_frame({"type": 0, "subtype": 10}))

    def test_protected_data_and_eapol_are_separate(self):
        self.assertEqual(frames.classify_frame(
            {"type": 2, "subtype": 8, "protected": True}), ["data_frame"])
        self.assertEqual(frames.classify_frame(
            {"type": 2, "subtype": 8, "protected": False, "eapol_key_info": 0x13C8}),
            ["eapol_m3"])
        self.assertEqual(frames.classify_frame(
            {"type": 2, "subtype": 8, "protected": False}), [])


class ClockTest(unittest.TestCase):
    def test_offset_is_consistent_across_records(self):
        stamp = datetime(2026, 11, 10, 8, 0, 43, 574561, tzinfo=timezone.utc).timestamp()
        records = [
            {"monotonic_s": 1353.897237168,
             "received_at": "2026-11-10T08:00:43.574561+00:00"},
            {"monotonic_s": 1353.904633814,  # 采集抖动约 10 微秒
             "received_at": "2026-11-10T08:00:43.581946+00:00"},
            {"monotonic_s": 1353.965167508,
             "received_at": "2026-11-10T08:00:43.642488+00:00"},
        ]
        offset = frames.clock_offset(records)
        self.assertIsNotNone(offset)
        # 取中位数而非均值，单条抖动不会把帧时间整体平移。
        self.assertAlmostEqual(frames.to_monotonic(stamp, offset), 1353.897237168, places=5)

    def test_no_timestamps_yields_none(self):
        self.assertIsNone(frames.clock_offset([{"raw_event": "x"}]))
        self.assertIsNone(frames.to_monotonic(1.0, None))


class ReportTest(unittest.TestCase):
    def _run_dir(self, folder, *, reuse_log=True):
        (folder / "context.json").write_text(json.dumps({
            "run_id": "20261110-real-0001", "attempt_id": 2,
            "reconnect_mode": "automatic"}), encoding="utf-8")
        events = [
            {"seq": 6, "source": "sta", "received_at": "2026-11-10T08:00:43.574561+00:00",
             "monotonic_s": 1353.897237168, "raw_event": "<3>PMKSA-CACHE-ADDED 02:00:00:00:00:00 0"},
            {"seq": 17, "source": "sta", "received_at": "2026-11-10T08:00:43.681667+00:00",
             "monotonic_s": 1354.004358877, "raw_event": "<3>CTRL-EVENT-DISCONNECTED bssid=02:00:00:00:00:00 reason=3"},
            {"seq": 27, "source": "sta", "received_at": "2026-11-10T08:00:47.557862+00:00",
             "monotonic_s": 1357.880554314, "raw_event": "<3>WPA: Key negotiation completed with 02:00:00:00:00:00"},
            {"seq": 28, "source": "sta", "received_at": "2026-11-10T08:00:47.558203+00:00",
             "monotonic_s": 1357.880883158, "raw_event": "<3>CTRL-EVENT-CONNECTED - Connection to 02:00:00:00:00:00 completed"},
        ]
        (folder / "events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
        log_lines = ["RSN: No PMKSA cache entry found"]
        if reuse_log:
            log_lines += ["RSN: PMKSA cache entry found - PMKID - hexdump(len=16): 44 0c",
                          "PMKSA cache entry found - try to use PMKSA caching instead of new SAE authentication",
                          "RSN: matched PMKID - hexdump(len=16): 44 0c"]
        (folder / "sta.log").write_text("\n".join(log_lines), encoding="utf-8")
        (folder / "trace.pcap").write_bytes(b"fake")

    @staticmethod
    def _pcap_rows(*, with_sae):
        base = 1794297647.0
        rows = []
        if with_sae:
            rows += [{"frame": 96, "time": base + .1, "type": 0, "subtype": 11,
                      "auth_algo": 3, "auth_seq": 1, "protected": False},
                     {"frame": 97, "time": base + .2, "type": 0, "subtype": 11,
                      "auth_algo": 3, "auth_seq": 2, "protected": False}]
        else:
            rows.append({"frame": 96, "time": base + .1, "type": 0, "subtype": 11,
                         "auth_algo": 0, "auth_seq": 1, "protected": False})
        rows.append({"frame": 98, "time": base + .3, "type": 0, "subtype": 0,
                     "protected": False, "rsn_pmkids": ["440c661a8089039046985a11618e34f7"]})
        for number, info in ((100, 0x0088), (101, 0x0108), (102, 0x13C8), (103, 0x0308)):
            rows.append({"frame": number, "time": base + .4 + (number - 100) * .001,
                         "type": 2, "subtype": 8, "protected": False,
                         "eapol_key_info": info})
        rows.append({"frame": 104, "time": base + .5, "type": 2, "subtype": 8,
                     "protected": True})
        return rows

    def _build(self, folder, *, with_sae, reuse_log=True):
        self._run_dir(folder, reuse_log=reuse_log)
        with patch.object(frames, "read_pcap_frames", return_value=self._pcap_rows(with_sae=with_sae)):
            return frames.build_real_evidence(folder)

    def test_cache_reuse_requires_no_sae_and_pmkid_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._build(Path(tmp), with_sae=False)
        self.assertEqual(report["auth_path"]["value"], "pmksa_reuse")
        self.assertEqual(report["auth_path"]["sae_frames"], [])
        self.assertEqual(report["auth_path"]["assoc_req_with_pmkid_frames"], [98])
        self.assertEqual(report["auth_path"]["pmkids"], ["440c661a8089039046985a11618e34f7"])
        self.assertEqual([row["frame"] for row in report["frame_evidence"]["timeline"]],
                         [100, 101, 102, 103, 104])

    def test_sae_frames_override_reuse_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._build(Path(tmp), with_sae=True)
        self.assertEqual(report["auth_path"]["value"], "full_sae")
        self.assertFalse(report["frame_evidence"]["pmkid_match"])

    def test_reuse_log_absent_keeps_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._build(Path(tmp), with_sae=False, reuse_log=False)
        self.assertEqual(report["auth_path"]["value"], "unknown")

    def test_authorization_timing_stays_insufficient_because_key_install_is_not_a_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._build(Path(tmp), with_sae=False)
        timing = report["authorization_timing"]
        self.assertEqual(timing["status"], "insufficient_evidence")
        self.assertIn("key_install", timing["reason"])
        self.assertIn("key_install", timing["not_frame_stages"])
        self.assertEqual(report["security_verdict"], "not_evaluated")
        refs = [row["ref"] for row in report["control_timeline"]]
        self.assertIn("events.jsonl#seq=27", refs)
