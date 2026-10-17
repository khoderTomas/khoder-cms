# -*- coding: utf-8 -*-
"""Deploy z gitu (cms/deploy.py, cms/bin/cms-deploy) nad temp repem a cílovou složkou; nic nejde ven.

  python -m unittest discover -s cms/tests -t cms/tests
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CMS = Path(__file__).resolve().parents[1]
ROOT = CMS.parent
sys.path.insert(0, str(CMS))
import deploy as D  # noqa: E402

CONFIG = """[deploy]
source = "dist"
site = "https://example.test"
pages = "content/pages.json"
php = ["send.php", "lib/*.php"]
dotfiles = [".htaccess"]

[deploy.target]
kind = "local"
path = "{target}"

[deploy.indexnow]
key_file = "key.txt"
"""
PAGE = '<!doctype html><link rel="stylesheet" href="style.css"><p>{}</p><script src="app.js"></script>\n'
SITEMAP = ("<urlset>\n<url><loc>https://example.test/</loc></url>\n"
           "<url><loc>https://example.test/a</loc></url>\n</urlset>\n")
CSS = b"body{color:red}\n"
OLD = 1_600_000_000


def git(cwd, *args, input=""):
    p = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", input=input)
    if p.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), p.stdout + p.stderr))
    return p.stdout.strip()


def tok(data):
    return hashlib.sha256(data).hexdigest()[:10]


class DeployBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cms-deploy-"))
        self.target = self.tmp / "www"
        self.target.mkdir()
        self.config = CONFIG.format(target=self.target.as_posix())
        self.work = self.tmp / "work"
        self.work.mkdir()
        git(self.work, "init", "-q", "-b", "main")
        git(self.work, "config", "user.name", "Test")
        git(self.work, "config", "user.email", "test@example.com")
        self.base = self.commit({
            "cms.toml": self.config,
            "cms/deploy.py": (CMS / "deploy.py").read_bytes(),
            "content/pages.json": json.dumps({"index": {"output": "dist/index.html"}, "a": {"output": "dist/a.html"},
                                              "skryta": {"output": "dist/skryta.html"}}),
            "dist/index.html": PAGE.format("úvod"),
            "dist/a.html": PAGE.format("stránka A"),
            "dist/skryta.html": PAGE.format("noindex"),
            "dist/style.css": CSS,
            "dist/app.js": "console.log(1)\n",
            "dist/send.php": "<?php echo 1;\n",
            "dist/lib/Mail.php": "<?php\n",
            "dist/.htaccess": "Options -Indexes\n",
            "dist/sitemap.xml": SITEMAP,
            "dist/key.txt": "abc123key\n",
            "dist/assets/logo.png": b"\x89PNG\r\n\x1a\n\x00binary",
        }, "init")
        git(self.tmp, "clone", "-q", "--bare", str(self.work), "remote.git")
        git(self.work, "remote", "add", "origin", str(self.tmp / "remote.git"))
        git(self.work, "fetch", "-q", "origin")
        git(self.tmp, "clone", "-q", "--bare", str(self.tmp / "remote.git"), "server.git")
        self.server = self.tmp / "server.git"
        self.state = self.tmp / "state" / "deploy.json"
        # soubory mimo git, které na produkci žijí (heslo SMTP, feedy, staré assety)
        self.foreign = {"send.config.php": b"<?php $pass = 'x';\n", "feeds/feed.xml": b"<rss/>\n",
                        "assets/stary.png": b"old"}
        for rel, data in self.foreign.items():
            p = self.target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            os.utime(p, (OLD, OLD))
        self.pings = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, files, msg="změna"):
        for rel, data in files.items():
            p = self.work / rel
            if data is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", msg)
        return git(self.work, "rev-parse", "HEAD")

    def push(self, force=False):
        git(self.work, "push", "-q", *(["-f"] if force else []), "origin", "main")

    def ship(self, files, msg="změna"):
        sha = self.commit(files, msg)
        self.push()
        return sha

    def deploy(self, config=True, **kw):
        d = D.Deployer(self.server, self.config if config else None, self.state, out=io.StringIO(),
                       post=lambda endpoint, payload: self.pings.append(payload))
        try:
            r = d.deploy(**kw)
        finally:
            self.out = d.out.getvalue()
        return r

    def read(self, rel):
        return (self.target / rel).read_bytes()

    def mtimes(self):
        return {str(p): p.stat().st_mtime_ns for p in self.target.rglob("*") if p.is_file()}


class DeployTest(DeployBase):
    def test_first_deploy_stamps_and_keeps_files_outside_git(self):
        r = self.deploy()
        self.assertIn("OK: Deployed version %s to https://example.test" % r["sha"][:12], self.out)
        html = self.read("index.html").decode("utf-8")
        self.assertIn('href="style.css?v=%s"' % tok(CSS), html)
        self.assertIn('src="app.js?v=%s"' % tok(b"console.log(1)\n"), html)
        self.assertIn("úvod", html)
        self.assertEqual(self.read("style.css"), CSS)
        self.assertEqual(self.read("assets/logo.png"), b"\x89PNG\r\n\x1a\n\x00binary")
        self.assertEqual(self.read(".htaccess"), b"Options -Indexes\n")
        for rel, data in self.foreign.items():
            self.assertEqual(self.read(rel), data, rel)
            self.assertEqual((self.target / rel).stat().st_mtime, OLD, rel)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["deployed"]["sha"], r["sha"])

    def test_second_deploy_changes_nothing(self):
        self.deploy()
        before = self.mtimes()
        r = self.deploy()
        self.assertEqual(r["changed"], [])
        self.assertIn("Změněno: 0 souborů", self.out)
        self.assertEqual(self.mtimes(), before)

    def test_stamp_is_stable_and_follows_content(self):
        self.deploy()
        index = self.read("index.html")
        r = self.deploy(sha=self.ship({"dist/a.html": PAGE.format("nové A")}))
        self.assertEqual(r["changed"], ["a.html"])
        self.assertEqual(self.read("index.html"), index)
        r = self.deploy(sha=self.ship({"dist/style.css": "body{color:blue}\n"}))
        # nejdřív ne-HTML, pak stránky s novým razítkem
        self.assertEqual(r["changed"], ["style.css", "a.html", "index.html", "skryta.html"])
        self.assertIn('style.css?v=%s"' % tok(b"body{color:blue}\n"), self.read("index.html").decode())

    def test_order_sitemap_last(self):
        self.deploy()
        r = self.deploy(sha=self.ship({"dist/sitemap.xml": SITEMAP.replace("</urlset>", "<!-- x -->\n</urlset>"),
                                       "dist/a.html": PAGE.format("A2"), "dist/assets/novy.png": b"new"}))
        self.assertEqual(r["changed"], ["assets/novy.png", "a.html", "sitemap.xml"])

    def test_commits_since_last_deploy_are_listed(self):
        self.deploy()
        self.commit({"dist/a.html": PAGE.format("A2")}, "content: úprava z Claude Code")
        sha = self.ship({"dist/skryta.html": PAGE.format("S2")}, "content: úprava z editoru")
        self.deploy(sha=sha)
        self.assertIn("Od posledního deploye (%s): 2 commity" % self.base[:12], self.out)
        self.assertIn("content: úprava z Claude Code (Test)", self.out)

    def test_dry_run_writes_nothing(self):
        r = self.deploy(dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertIn("DRY RUN", self.out)
        self.assertNotIn("OK: Deployed", self.out)
        self.assertFalse((self.target / "index.html").exists())
        self.assertFalse(self.state.exists())

    def test_sha_outside_main_is_refused(self):
        git(self.work, "checkout", "-q", "-b", "feature")
        sha = self.commit({"dist/a.html": PAGE.format("z větve")})
        git(self.work, "push", "-q", "origin", "feature")
        git(self.server, "fetch", "-q", "origin", "feature:refs/heads/feature")
        with self.assertRaisesRegex(D.DeployError, "není na origin/main"):
            self.deploy(sha=sha)
        with self.assertRaisesRegex(D.DeployError, "repo nezná"):
            self.deploy(sha="0" * 40)
        self.assertFalse((self.target / "a.html").exists())

    def test_backwards_needs_rollback(self):
        first = self.deploy()["sha"]
        self.deploy(sha=self.ship({"dist/a.html": PAGE.format("A2")}))
        with self.assertRaisesRegex(D.DeployError, "nenavazuje"):
            self.deploy(sha=first)
        with self.assertRaisesRegex(D.DeployError, "jen dopředu"):
            self.deploy(sha=first, caller="cms", rollback=True)
        r = self.deploy(sha=first, rollback=True)
        self.assertEqual(r["changed"], ["a.html"])
        self.assertIn("stránka A", self.read("a.html").decode("utf-8"))
        r = self.deploy()                                  # po rollbacku zase dopředu na špičku
        self.assertEqual(r["changed"], ["a.html"])

    def test_checks_refuse_whole_deploy(self):
        cases = [({"dist/evil.php": "<?php system($_GET[1]);"}, "evil.php: PHP mimo povolené"),
                 ({"dist/lib/sub/x.php": "<?php"}, "lib/sub/x.php: PHP mimo povolené"),
                 ({"dist/.env": "X=1\n"}, ".env: skrytý soubor"),
                 ({"dist/.git-keep/x.txt": "x"}, "skrytý soubor"),
                 ({"dist/nova.html": PAGE.format("nová")}, "nova.html: stránka není v content/pages.json"),
                 ({"dist/skryta.html": None}, "skryta.html: stránka z content/pages.json v commitu chybí")]
        for files, msg in cases:
            with self.subTest(msg=msg):
                git(self.work, "reset", "-q", "--hard", self.base)
                self.commit(dict(files, **{"dist/a.html": PAGE.format("nesmí ven")}))
                self.push(force=True)
                with self.assertRaisesRegex(D.DeployError, "Kontroly neprošly") as cm:
                    self.deploy()
                self.assertIn(msg, str(cm.exception))
                self.assertFalse((self.target / "a.html").exists())
                self.assertFalse(self.state.exists())

    def test_symlink_is_refused(self):
        blob = git(self.work, "hash-object", "-w", "--stdin")  # prázdný blob jako cíl odkazu
        git(self.work, "update-index", "--add", "--cacheinfo", "120000,%s,dist/odkaz" % blob)
        git(self.work, "commit", "-q", "-m", "odkaz")
        self.push()
        with self.assertRaisesRegex(D.DeployError, "odkaz: symbolický odkaz"):
            self.deploy()


class CmsModeTest(DeployBase):
    def test_cms_deploys_html_and_sitemap(self):
        self.deploy()
        r = self.deploy(caller="cms", sha=self.ship({"dist/a.html": PAGE.format("z editoru"),
                                                     "dist/sitemap.xml": SITEMAP + "<!-- lastmod -->\n"}))
        self.assertEqual(r["changed"], ["a.html", "sitemap.xml"])
        self.assertIn("z editoru", self.read("a.html").decode("utf-8"))
        self.assertIn("OK: Deployed version", self.out)

    def test_cms_refuses_css(self):
        self.deploy()
        self.ship({"dist/style.css": "body{color:blue}\n", "dist/a.html": PAGE.format("z editoru")})
        with self.assertRaisesRegex(D.DeployError, "mimo HTML a sitemapu") as cm:
            self.deploy(caller="cms")
        self.assertIn("style.css", str(cm.exception))
        self.assertEqual(self.read("style.css"), CSS)
        self.assertIn("stránka A", self.read("a.html").decode("utf-8"))

    def test_cms_refuses_php_and_assets(self):
        self.deploy()
        self.ship({"dist/send.php": "<?php echo 2;\n", "dist/assets/novy.png": b"new"})
        with self.assertRaisesRegex(D.DeployError, "mimo HTML a sitemapu") as cm:
            self.deploy(caller="cms")
        self.assertIn("send.php", str(cm.exception))
        self.assertIn("assets/novy.png", str(cm.exception))
        self.assertEqual(self.read("send.php"), b"<?php echo 1;\n")

    def test_cms_reads_config_only_from_outside(self):
        with self.assertRaisesRegex(D.DeployError, "jen z --config"):
            self.deploy(caller="cms", config=False)

    def test_full_mode_reads_config_from_commit(self):
        r = self.deploy(config=False)
        self.assertIn("OK: Deployed version %s" % r["sha"][:12], self.out)


class IndexNowTest(DeployBase):
    def test_only_changed_pages_from_sitemap(self):
        self.deploy()
        self.assertEqual(len(self.pings), 1)                 # prázdný cíl: obě stránky ze sitemapy
        self.pings.clear()
        r = self.deploy(sha=self.ship({"dist/a.html": PAGE.format("nové A"), "dist/skryta.html": PAGE.format("x")}))
        self.assertEqual(r["indexnow"], ["https://example.test/a"])
        self.assertEqual(self.pings, [{"host": "example.test", "key": "abc123key",
                                       "keyLocation": "https://example.test/key.txt",
                                       "urlList": ["https://example.test/a"]}])

    def test_stamp_change_is_not_content_change(self):
        # produkce po deploy.ps1: týž obsah, razítka z času
        for rel in ("index.html", "a.html", "skryta.html"):
            src = (self.work / "dist" / rel).read_bytes()
            (self.target / rel).write_bytes(src.replace(b'style.css"', b'style.css?v=20261014-120000"')
                                            .replace(b'app.js"', b'app.js?v=20261014-120000"'))
        r = self.deploy()
        self.assertIn("index.html", r["changed"])
        self.assertEqual(r["indexnow"], [])
        self.assertEqual(self.pings, [])
        r = self.deploy(sha=self.ship({"dist/style.css": "body{}\n"}))
        self.assertEqual(r["indexnow"], [])

    def test_ping_failure_does_not_fail_deploy(self):
        def boom(endpoint, payload):
            raise OSError("síť nejde")
        d = D.Deployer(self.server, self.config, self.state, out=io.StringIO(), post=boom)
        d.deploy()
        self.assertIn("IndexNow selhal (deploy proběhl)", d.out.getvalue())
        self.assertIn("OK: Deployed version", d.out.getvalue())


class StampTest(unittest.TestCase):
    def test_stamp_and_unstamp(self):
        tokens = {"style.css": "aaaa", "js/app.js": "bbbb"}
        html = (b'<link href="style.css"><link href="./style.css?v=old"><script src="js/app.js"></script>'
                b'<script src="https://cdn.example/x.js"></script><script src="//cdn.example/y.js"></script>'
                b'<a data-href="style.css">x</a><script src="chybi.js"></script><link href="/style.css">')
        out = D.stamp("index.html", html, tokens)
        self.assertIn(b'href="style.css?v=aaaa"', out)
        self.assertIn(b'href="./style.css?v=aaaa"', out)
        self.assertIn(b'src="js/app.js?v=bbbb"', out)
        self.assertIn(b'href="/style.css?v=aaaa"', out)
        self.assertIn(b'src="https://cdn.example/x.js"', out)
        self.assertIn(b'src="//cdn.example/y.js"', out)
        self.assertIn(b'data-href="style.css"', out)
        self.assertIn(b'src="chybi.js"', out)
        self.assertEqual(D.unstamp(out), D.unstamp(html))
        self.assertEqual(D.stamp("sub/x.html", b'<link href="../style.css">', tokens), b'<link href="../style.css?v=aaaa">')

    def test_sitemap_urls(self):
        sm = (b"<loc>https://example.test/</loc><loc> https://example.test/a </loc><loc>https://example.test/b.html</loc>"
              b"<loc>https://example.test/blog/</loc><loc>https://jinde.test/x</loc><loc>https://example.test/f.pdf</loc>")
        self.assertEqual(D.sitemap_urls(sm, "https://example.test"), [
            ("https://example.test/", "index.html"), ("https://example.test/a", "a.html"),
            ("https://example.test/b.html", "b.html"), ("https://example.test/blog/", "blog/index.html"),
            ("https://example.test/f.pdf", "f.pdf")])

    def test_config_rejects_unknown_keys(self):
        with self.assertRaisesRegex(D.DeployError, "neznámé klíče v \\[deploy\\]: dotfile"):
            D.load_config('[deploy]\ndotfile = [".env"]\n')

    def test_sftp_batch(self):
        t = D.SftpTarget("u@host", "/www/")
        t.stage = Path("/stage")
        batch = t.batch(["assets/a/x.png", "assets/b.png", 'i "q".html'])

        def q(s):
            return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"')
        self.assertEqual(batch.splitlines(), [
            '-mkdir "/www/assets"', '-mkdir "/www/assets/a"',
            'put %s "/www/assets/a/x.png"' % q(Path("/stage/assets/a/x.png")),
            'put %s "/www/assets/b.png"' % q(Path("/stage/assets/b.png")),
            'put %s "/www/i \\"q\\".html"' % q(Path('/stage/i "q".html'))])


@unittest.skipIf(os.name == "nt", "práva souborů jen na POSIX")
class ModesTest(DeployBase):
    def test_existing_mode_kept_new_files_644_new_dirs_like_parent(self):
        (self.target / "assets").mkdir(exist_ok=True)
        os.chmod(self.target / "assets", 0o700)
        (self.target / ".htaccess").write_bytes(b"stary\n")
        os.chmod(self.target / ".htaccess", 0o660)
        self.deploy(sha=self.ship({"dist/assets/nove/x.png": b"x"}))
        mode = lambda rel: (self.target / rel).stat().st_mode & 0o777  # noqa: E731
        self.assertEqual(mode(".htaccess"), 0o660)
        self.assertEqual(self.read(".htaccess"), b"Options -Indexes\n")
        self.assertEqual(mode("index.html"), 0o644)
        self.assertEqual(mode("assets/nove"), 0o700)
        self.assertEqual(list(self.target.rglob("*.cms-tmp")), [])


@unittest.skipUnless(shutil.which("rsync"), "rsync není")
class RsyncTest(DeployBase):
    def test_rsync_target_changes_only_and_keeps_foreign(self):
        cfg = self.config.replace('kind = "local"\npath = "%s"' % self.target.as_posix(),
                                  'kind = "rsync"\ndest = "%s"' % self.target.as_posix())
        self.config = cfg
        r = self.deploy()
        self.assertIn("index.html", r["changed"])
        self.assertEqual(self.read("send.config.php"), self.foreign["send.config.php"])
        r = self.deploy()
        self.assertEqual(r["changed"], [])


@unittest.skipUnless(shutil.which("sh"), "sh není")
class WrapperTest(DeployBase):
    def run_wrapper(self, *args, ssh_cmd=None):
        env = dict(os.environ, CMS_HOME=str(self.home), CMS_PYTHON=sys.executable)
        env.pop("SSH_ORIGINAL_COMMAND", None)
        if ssh_cmd is not None:
            env["SSH_ORIGINAL_COMMAND"] = ssh_cmd
        p = subprocess.run(["sh", str(CMS / "bin" / "cms-deploy"), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "cmshome"
        self.home.mkdir()
        git(self.tmp, "clone", "-q", "--bare", str(self.tmp / "remote.git"), str(self.home / "repo.git"))

    def test_full_deploy_pins_code_and_cms_uses_pin(self):
        rc, out = self.run_wrapper("cms", "deploy")
        self.assertEqual(rc, 1, out)
        self.assertIn("nejdřív plný deploy", out)
        rc, out = self.run_wrapper("dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertFalse((self.home / "pin").exists())
        rc, out = self.run_wrapper("deploy")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK: Deployed version", out)
        self.assertTrue((self.home / "pin" / "deploy.py").is_file())
        self.ship({"dist/a.html": PAGE.format("z editoru")})
        rc, out = self.run_wrapper("cms", ssh_cmd="deploy")
        self.assertEqual(rc, 0, out)
        self.assertIn("režim cms", out)
        self.assertIn("z editoru", self.read("a.html").decode("utf-8"))
        rc, out = self.run_wrapper("status")
        self.assertEqual(rc, 0, out)
        self.assertIn("Nasazeno: ", out)

    def test_forced_command_accepts_only_known_commands(self):
        self.run_wrapper("deploy")
        for cmd in ("rollback %s" % self.base, "deploy; rm -rf /", "deploy HEAD", "deploy %s x" % self.base[:7],
                    "deploy ../../etc", "sh"):
            with self.subTest(cmd=cmd):
                rc, out = self.run_wrapper("cms", ssh_cmd=cmd)
                self.assertEqual(rc, 1, out)
                self.assertIn("CHYBA", out)
                self.assertNotIn("OK: Deployed", out)


if __name__ == "__main__":
    unittest.main()
