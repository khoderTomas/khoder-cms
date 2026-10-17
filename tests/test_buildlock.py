# -*- coding: utf-8 -*-
"""Zámek buildu: živost procesu, který zámek drží, a převzetí mrtvého zámku.

  python -m unittest discover -s cms/tests -t cms/tests -p "test_buildlock.py"
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buildlock as L  # noqa: E402


def finished_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class AliveTest(unittest.TestCase):
    def test_own_process(self):
        self.assertTrue(L._alive(os.getpid()))

    def test_no_pid(self):
        self.assertFalse(L._alive(0))
        self.assertFalse(L._alive(-1))

    def test_finished_process(self):
        self.assertFalse(L._alive(finished_pid()))

    @unittest.skipIf(sys.platform == "win32", "os.kill a PermissionError jsou cesta pro Linux")
    def test_other_users_process(self):
        # PID 1 patří rootovi: os.kill(1, 0) neprivilegovanému uživateli hodí PermissionError
        self.assertTrue(L._alive(1))


class LockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ke-lock-")
        self.orig = L.LOCK
        L.LOCK = os.path.join(self.tmp, ".build.lock")

    def tearDown(self):
        L.LOCK = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lock_by(self, pid):
        Path(L.LOCK).write_text(json.dumps({"pid": pid, "who": "jinde", "t": time.time()}), encoding="utf-8")

    def test_dead_lock_is_taken_over(self):
        self._lock_by(finished_pid())
        with L.hold("test", wait=0):
            self.assertEqual(json.loads(Path(L.LOCK).read_text(encoding="utf-8"))["pid"], os.getpid())
        self.assertFalse(os.path.exists(L.LOCK))

    @unittest.skipIf(sys.platform == "win32", "os.kill a PermissionError jsou cesta pro Linux")
    def test_lock_of_other_user_is_kept(self):
        self._lock_by(1)
        with self.assertRaises(L.Busy):
            L.acquire("test", wait=0)
        self.assertEqual(json.loads(Path(L.LOCK).read_text(encoding="utf-8"))["pid"], 1)


if __name__ == "__main__":
    unittest.main()
