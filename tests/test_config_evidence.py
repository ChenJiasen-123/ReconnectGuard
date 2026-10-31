import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from rg_platform.evidence import archive_configs


class ConfigEvidenceTests(unittest.TestCase):
    def test_archive_survives_runtime_removal_without_byte_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            output = root / "results"
            output.mkdir()
            payloads = {"ap": b"ieee80211w=2\r\nwpa_key_mgmt=SAE\n",
                        "sta": b"disabled=1\n# exact bytes: \xff\n"}
            configs = {}
            for role, data in payloads.items():
                configs[role] = runtime / (role + ".conf")
                configs[role].write_bytes(data)
            manifest = archive_configs(output, configs)
            # Delete only the named files and now-empty runtime directory.
            for path in configs.values():
                path.unlink()
            runtime.rmdir()
            for role, original in payloads.items():
                archived = output / manifest[role]["evidence_path"]
                self.assertEqual(archived.read_bytes(), original)
                self.assertEqual(hashlib.sha256(archived.read_bytes()).hexdigest(),
                                 manifest[role]["sha256"])
                if os.name == "posix":
                    self.assertEqual(archived.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(archived.parent.stat().st_mode & 0o777, 0o700)

    def test_missing_input_prevents_incomplete_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ap = root / "ap.conf"
            ap.write_bytes(b"ieee80211w=2\n")
            with self.assertRaises(FileNotFoundError):
                archive_configs(root, {"ap": ap, "sta": root / "missing.conf"})
            self.assertFalse((root / "runtime-configs").exists())

    def test_existing_archive_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ap, sta = root / "ap.conf", root / "sta.conf"
            ap.write_bytes(b"first ap")
            sta.write_bytes(b"first sta")
            archive_configs(root, {"ap": ap, "sta": sta})
            ap.write_bytes(b"changed ap")
            with self.assertRaises(FileExistsError):
                archive_configs(root, {"ap": ap, "sta": sta})
            self.assertEqual((root / "runtime-configs/ap.conf").read_bytes(), b"first ap")
