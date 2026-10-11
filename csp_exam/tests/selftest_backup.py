import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core.runtime_lock import data_lock
from csp_exam.tools.backup_data import backup


class Backups(unittest.TestCase):
    def test_backup_round_trip_and_reject_active_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"; root.mkdir()
            (root / "contests.json").write_text("[]")
            (root / "answer.in").write_bytes(b"\x00\xff\r\n")
            output = Path(tmp) / "backup.tgz"
            with data_lock(root):
                with self.assertRaises(RuntimeError): backup(root, output)
            self.assertFalse(output.exists())
            report = backup(root, output)
            self.assertEqual(report["files"], 2)
            with tarfile.open(output) as tar:
                self.assertEqual(tar.extractfile("answer.in").read(), b"\x00\xff\r\n")
                self.assertNotIn(".service.lock", tar.getnames())
            with self.assertRaises(FileExistsError): backup(root, output)
            with self.assertRaises(ValueError): backup(root, root / "backup.tgz")


if __name__ == "__main__": unittest.main()
