# -*- coding: utf-8 -*-
"""Sladění checkoutu s větví (cms/gitsync.py) nad temp repem s bare remote; nic nejde ven.

  python -m unittest discover -s cms/tests -t cms/tests
"""
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

CMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CMS))
import gitsync as G  # noqa: E402

PY = sys.executable


def git(cwd, *args, input=""):
    p = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", input=input)
    if p.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), p.stdout + p.stderr))
    return p.stdout.strip()


class MergeFileTest(unittest.TestCase):
    BASE = b"a\nb\nc\nd\ne\nf\n"

    def test_clean_merge_keeps_both_changes(self):
        out, n = G.merge_file(b"a editor\nb\nc\nd\ne\nf\n", self.BASE, b"a\nb\nc\nd\ne\nf main\n")
        self.assertEqual((out, n), (b"a editor\nb\nc\nd\ne\nf main\n", 0))

    def test_same_line_is_conflict(self):
        _, n = G.merge_file(b"a\nb\nc editor\nd\ne\nf\n", self.BASE, b"a\nb\nc main\nd\ne\nf\n")
        self.assertEqual(n, 1)

    def test_identical_change_and_shortcuts(self):
        same = b"a\nb\nc X\nd\ne\nf\n"
        self.assertEqual(G.merge_file(same, self.BASE, same), (same, 0))
        self.assertEqual(G.merge_file(self.BASE, self.BASE, same), (same, 0))
        self.assertEqual(G.merge_file(same, self.BASE, self.BASE), (same, 0))


    def test_json_values_on_one_line_merge_by_path(self):
        base = b'{"a": {"t": "jedna", "u": "dva"}}\n'
        ours, theirs = base.replace(b'"jedna"', b'"JEDNA"'), base.replace(b'"dva"', b'"DVA"')
        self.assertEqual(G.merge_file(ours, base, theirs)[1], 1)
        self.assertEqual(G.merge("content/x.json", ours, base, theirs), (b'{"a": {"t": "JEDNA", "u": "DVA"}}\n', 0))
        self.assertEqual(G.merge("dist/x.html", ours, base, theirs)[1], 1)
        clash = base.replace(b'"jedna"', b'"JINAK"')
        self.assertEqual(G.merge("content/x.json", ours, base, clash)[1], 1)


class RepoBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cms-gitsync-"))
        seed = self.tmp / "seed"
        seed.mkdir()
        for name, text in (("dist/a.html", "<p>a1</p>\n<p>a2</p>\n<p>a3</p>\n"), ("dist/b.html", "<p>b</p>\n"),
                           ("build/gen.py", "T = 'x'\n"), ("README.txt", "r\n")):
            (seed / name).parent.mkdir(parents=True, exist_ok=True)
            (seed / name).write_text(text, encoding="utf-8", newline="")
        git(seed, "init", "-q", "-b", "main")
        git(seed, "add", "-A")
        git(seed, "-c", "user.name=Seed", "-c", "user.email=seed@example.com", "commit", "-q", "-m", "init")
        git(self.tmp, "clone", "-q", "--bare", str(seed), "origin.git")
        self.work, self.other = self.tmp / "work", self.tmp / "other"
        for d in (self.work, self.other):
            git(self.tmp, "clone", "-q", str(self.tmp / "origin.git"), str(d))
            git(d, "config", "core.autocrlf", "false")
            git(d, "config", "user.name", "Test")
            git(d, "config", "user.email", "test@example.com")
        self.g = G.Git(self.work)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def main_commit(self, files, msg="main"):
        """Commit v jiném klonu a push: main mezitím ujel."""
        for name, text in files.items():
            (self.other / name).parent.mkdir(parents=True, exist_ok=True)
            (self.other / name).write_text(text, encoding="utf-8", newline="")
        git(self.other, "add", "-A")
        git(self.other, "commit", "-q", "-m", msg)
        git(self.other, "push", "-q", "origin", "main")
        return git(self.other, "rev-parse", "HEAD")

    def write(self, name, text):
        (self.work / name).write_text(text, encoding="utf-8", newline="")

    def read(self, name):
        return (self.work / name).read_text(encoding="utf-8")


class AdvanceTest(RepoBase):
    def test_unrelated_dirty_file_stays_and_checkout_moves(self):
        target = self.main_commit({"dist/b.html": "<p>b main</p>\n"})
        self.write("dist/a.html", "<p>a1 editor</p>\n<p>a2</p>\n<p>a3</p>\n")
        r = G.advance(self.g, self.g.fetch("origin", "main"))
        self.assertTrue(r["moved"])
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), target)
        self.assertEqual(self.read("dist/b.html"), "<p>b main</p>\n")
        self.assertEqual(self.read("dist/a.html"), "<p>a1 editor</p>\n<p>a2</p>\n<p>a3</p>\n")

    def test_overlap_with_unsent_edit_blocks_and_changes_nothing(self):
        self.main_commit({"dist/a.html": "<p>a1</p>\n<p>a2</p>\n<p>a3 main</p>\n"})
        head = git(self.work, "rev-parse", "HEAD")
        self.write("dist/a.html", "<p>a1 editor</p>\n<p>a2</p>\n<p>a3</p>\n")
        r = G.advance(self.g, self.g.fetch("origin", "main"))
        self.assertEqual((r["moved"], r["blocked"]), (False, ["dist/a.html"]))
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head)
        self.assertEqual(self.read("dist/a.html"), "<p>a1 editor</p>\n<p>a2</p>\n<p>a3</p>\n")

    def test_owned_file_and_file_equal_to_target_move_with_checkout(self):
        target = self.main_commit({"dist/a.html": "<p>a1 published</p>\n<p>a2</p>\n<p>a3</p>\n",
                                   "dist/b.html": "<p>b main</p>\n"})
        self.write("dist/a.html", "<p>a1 published, jen v editoru jinak</p>\n")
        self.write("dist/b.html", "<p>b main</p>\n")
        r = G.advance(self.g, self.g.fetch("origin", "main"), owned=["dist/a.html"])
        self.assertTrue(r["moved"], r)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), target)
        self.assertEqual(self.read("dist/a.html"), "<p>a1 published</p>\n<p>a2</p>\n<p>a3</p>\n")
        self.assertEqual(git(self.work, "status", "--porcelain"), "")

    def test_clean_follows_autocrlf(self):
        git(self.work, "config", "core.autocrlf", "true")
        (self.work / "dist" / "b.html").write_bytes(b"<p>b</p>\r\n<p>nov\xc3\xbd</p>\r\n")
        self.assertEqual(self.g.clean(["dist/b.html", "chybi.html"]),
                         {"dist/b.html": "<p>b</p>\n<p>nový</p>\n".encode("utf-8"), "chybi.html": None})
        self.assertEqual(self.g.clean_bytes("dist/b.html", b"x\r\ny\r\n"), b"x\ny\n")


class WorktreePushTest(RepoBase):
    def test_worktree_is_removed_and_push_reports_branch_that_moved(self):
        base = git(self.work, "rev-parse", "HEAD")
        with G.Worktree(self.g, base) as wt:
            self.assertNotEqual(wt.path, self.work)
            (wt.path / "README.txt").write_text("r editor\n", encoding="utf-8")
            wt.git.run("commit", "-q", "-am", "editor")
            self.main_commit({"dist/b.html": "<p>b main</p>\n"})
            self.assertEqual(wt.git.push("origin", "main"), "behind")
        self.assertFalse(wt.path.exists())
        self.assertEqual(len(git(self.work, "worktree", "list").splitlines()), 1)
        target = self.g.fetch("origin", "main")
        with G.Worktree(self.g, target) as wt:
            (wt.path / "README.txt").write_text("r editor\n", encoding="utf-8")
            wt.git.run("commit", "-q", "-am", "editor")
            self.assertEqual(wt.git.push("origin", "main"), "ok")
            pushed = wt.git.rev("HEAD")
        self.assertEqual(git(self.tmp / "origin.git", "rev-parse", "main"), pushed)


class SyncCliTest(RepoBase):
    def cli(self, *args, **kw):
        return subprocess.run([PY, str(CMS / "gitsync.py"), "--repo", str(self.work), "sync", *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)

    def test_exit_codes(self):
        self.assertEqual(self.cli().returncode, 0)                                   # aktuální
        self.main_commit({"dist/b.html": "<p>b main</p>\n"})
        p = self.cli("--restart-prefix", "build/")
        self.assertEqual(p.returncode, 0, p.stdout)                                  # posunuto, jen dist/
        self.main_commit({"build/gen.py": "T = 'main'\n"})
        self.assertEqual(self.cli("--restart-prefix", "build/").returncode, G.EXIT_RESTART)
        self.main_commit({"dist/a.html": "<p>a1 main</p>\n<p>a2</p>\n<p>a3</p>\n"})
        self.write("dist/a.html", "<p>a1 editor</p>\n<p>a2</p>\n<p>a3</p>\n")
        p = self.cli()
        self.assertEqual(p.returncode, G.EXIT_BLOCKED, p.stdout)
        self.assertIn("dist/a.html", p.stdout)
        git(self.work, "checkout", "-q", "-b", "jina")
        self.assertEqual(self.cli().returncode, 1)

    def test_sync_waits_for_publication_lock(self):
        target = self.main_commit({"dist/b.html": "<p>b main</p>\n"})
        with G.Lock(self.g.lock_path(), wait=1):
            with self.assertRaises(G.Busy):
                with G.Lock(self.g.lock_path(), wait=0.3):
                    pass
            proc = subprocess.Popen([PY, str(CMS / "gitsync.py"), "--repo", str(self.work), "sync"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            time.sleep(1.5)
            self.assertIsNone(proc.poll(), "sync nečeká na zámek publikace")
            self.assertNotEqual(git(self.work, "rev-parse", "HEAD"), target)
        out, _ = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), target)


if __name__ == "__main__":
    unittest.main()
