# -*- coding: utf-8 -*-
"""Samostatný server editoru (editor.py) a přihlášení (login.py) nad kopií ukázkového webu v tempu:
přihlášení formulářem, sezení, brzda, Host, Origin a X-KE přes skutečné HTTP na 127.0.0.1, lokální
režim, uživatelé z příkazové řádky, sync checkoutu s restartem a Publikovat v lokálním režimu
(cms.toml [publish] deploy = "direct") až do cílové složky.

  python -m unittest discover -s tests -t tests -p "test_editor.py"
"""
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

CMS = Path(__file__).resolve().parents[1]
EXAMPLE = CMS / "example"
sys.path.insert(0, str(CMS))
import adapter as AD    # noqa: E402
import apply as A       # noqa: E402
import blocks as B      # noqa: E402
import editor as E      # noqa: E402
import gitsync as G     # noqa: E402
import htmltext as H    # noqa: E402
import login as L       # noqa: E402
import publish as P     # noqa: E402

URL = "https://editor.example.cz"
HOST = "editor.example.cz"
FAST = 1000             # iterace PBKDF2 v testech (výchozích 600 000 by test zdržely)
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1"}


def tmpdir(test, prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, d, True)
    return d


def copy_example(dest):
    shutil.copytree(EXAMPLE, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".cms"))
    return dest


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=dict(os.environ, **GIT_ENV), check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def git_site(test):
    """(checkout editoru, bare origin, druhý klon) s ukázkovým webem na main."""
    if not shutil.which("git"):
        test.skipTest("bez gitu")
    base = tmpdir(test, "cms-editor-git-")
    work = copy_example(base / "work")
    git(work, "init", "-q", "-b", "main")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "ukázka")
    git(base, "clone", "-q", "--bare", str(work), "origin.git")
    git(work, "remote", "add", "origin", str(base / "origin.git"))
    git(work, "fetch", "-q", "origin")
    git(work, "branch", "-q", "--set-upstream-to=origin/main", "main")
    git(base, "clone", "-q", str(base / "origin.git"), "other")
    return work, base / "origin.git", base / "other"


def write_users(path, users):
    """users = {login: (heslo, role)}; hash s FAST iteracemi."""
    path.write_text(json.dumps({login: {"heslo": L.hash_password(pw, iterations=FAST), "role": role,
                                        "jmeno": login.title(), "email": "%s@example.cz" % login}
                                for login, (pw, role) in users.items()}), encoding="utf-8")


class Client:
    """HTTP na editor s Host podle režimu; cookie sezení drží sám."""

    def __init__(self, app, host):
        self.app, self.host, self.cookie = app, host, None

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        h = {"Host": host or self.host}
        if self.cookie:
            h["Cookie"] = "%s=%s" % (L.COOKIE, self.cookie)
        if isinstance(body, dict):
            body = urllib.parse.urlencode(body).encode("utf-8")
            h["Content-Type"] = "application/x-www-form-urlencoded"
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read().decode("utf-8", "replace")
        c.close()
        return r, data

    def login(self, user, password, ip=None, dalsi="/"):
        h = {"Origin": URL}
        if ip:
            h["X-Real-IP"] = ip
        r, data = self.req("POST", E.LOGIN, {"username": user, "password": password, "dalsi": dalsi}, h)
        m = re.match(r"%s=([^;]*);" % re.escape(L.COOKIE), r.getheader("Set-Cookie") or "")
        if r.status == 303 and m:
            self.cookie = m.group(1)
        return r, data


class ServerCase(unittest.TestCase):
    public = True

    def setUp(self):
        self.tmp = tmpdir(self, "cms-editor-")
        self.root = copy_example(self.tmp / "site")
        self.users_file = self.tmp / "users.json"
        write_users(self.users_file, {"jana": ("heslo-jany-123", "editor"), "petr": ("heslo-petra-123", "admin")})
        patcher = mock.patch.object(E, "log")           # přihlášení jde do logu služby, v testech ticho
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        self.srv = self.app.bind(0)
        self.addCleanup(self.app.close)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.c = Client(self.app, HOST if self.public else "127.0.0.1:%d" % self.app.port)

    def make_app(self, **kw):
        opts = dict(journal_dir=self.tmp / "journal", check_drift=False)
        if self.public:
            opts.update(public_url=URL, users=L.Users(self.users_file))
        opts.update(kw)
        return E.App(self.root, port=0, **opts)


class ServiceTest(ServerCase):
    def test_health_and_login_styles_without_login(self):
        r, body = self.c.req("GET", "/health")
        self.assertEqual((r.status, body), (200, "ok\n"))
        for name in E.PUBLIC_STATIC:
            r, body = self.c.req("GET", "/_admin/" + name)
            self.assertEqual(r.status, 200, name)
        r, _ = self.c.req("GET", "/_admin/editor.js")              # statika editoru jen po přihlášení
        self.assertEqual(r.status, 303)
        r, _ = self.c.req("GET", "/assets/style.css")              # soubory webu pro náhled v sandboxu
        self.assertEqual(r.status, 200)

    def test_unknown_host_is_refused(self):
        r, _ = self.c.req("GET", "/health", host="evil.example.com")
        self.assertEqual(r.status, 403)

    def test_without_login_page_goes_to_form_and_api_gets_401(self):
        r, _ = self.c.req("GET", "/kontakt?e=1")
        self.assertEqual(r.status, 303)
        self.assertEqual(r.getheader("Location"), "/prihlaseni?dalsi=%2Fkontakt%3Fe%3D1")
        r, body = self.c.req("GET", "/api/status")
        self.assertEqual(r.status, 401)
        self.assertIn("Přihlášení vypršelo", json.loads(body)["error"])
        r, body = self.c.req("GET", "/prihlaseni?dalsi=//evil.example.com")
        self.assertEqual(r.status, 200)
        self.assertIn("form-action 'self'", r.getheader("Content-Security-Policy"))
        self.assertIn('name="dalsi" value="/"', body)
        self.assertIn("www.example.com", body)                    # název webu z cms.toml

    def test_login_logout_and_session_cookie(self):
        r, body = self.c.req("POST", E.LOGIN, {"username": "jana", "password": "heslo-jany-123"})
        self.assertEqual(r.status, 403)                           # bez Origin ani Sec-Fetch-Site
        r, body = self.c.login("jana", "spatne-heslo-123")
        self.assertEqual(r.status, 401)
        self.assertIn("Špatný uživatel nebo heslo.", body)
        r, _ = self.c.login("jana", "heslo-jany-123", dalsi="/kontakt")
        self.assertEqual((r.status, r.getheader("Location")), (303, "/kontakt"))
        self.assertRegex(r.getheader("Set-Cookie"), r"^__Host-cms=[\w-]{40,}; Path=/; Max-Age=\d+; Secure; HttpOnly; SameSite=Lax$")
        r, body = self.c.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn("Odhlásit se", body)
        self.assertIn('<span class="k-role">editor</span><span class="h-user__name">Jana</span>', body)
        r, body = self.c.req("GET", "/api/status")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body)["role"], "editor")
        r, _ = self.c.req("GET", "/kontakt?src=1")                # celý zdroj stránky jen role admin
        self.assertEqual(r.status, 403)
        old = self.c.cookie
        r, _ = self.c.req("POST", E.LOGOUT, {}, {"Origin": URL})
        self.assertEqual((r.status, r.getheader("Location")), (303, "/prihlaseni?odhlaseno=1"))
        self.assertIn("Max-Age=0", r.getheader("Set-Cookie"))
        self.c.cookie = old
        r, _ = self.c.req("GET", "/")
        self.assertEqual(r.status, 303)

    def test_admin_sees_page_source(self):
        self.c.login("petr", "heslo-petra-123")
        r, body = self.c.req("GET", "/kontakt?src=1")
        self.assertEqual(r.status, 200)
        self.assertIn("<h1 class=\"k-h1\">Zdroj /kontakt</h1>", body)

    def test_writes_need_x_ke_and_origin(self):
        self.c.login("jana", "heslo-jany-123")
        body = json.dumps({}).encode("utf-8")
        r, _ = self.c.req("POST", "/api/undo", body, {"Origin": URL})
        self.assertEqual(r.status, 403)
        r, _ = self.c.req("POST", "/api/undo", body, {"Origin": "https://evil.example.com", "X-KE": "1"})
        self.assertEqual(r.status, 403)
        r, data = self.c.req("POST", "/api/undo", body, {"Origin": URL, "X-KE": "1"})
        self.assertNotIn(r.status, (401, 403), data)               # prošlo až do editoru (prázdný požadavek)
        self.c.cookie = None
        r, _ = self.c.req("POST", "/api/undo", body, {"Origin": URL, "X-KE": "1"})
        self.assertEqual(r.status, 401)

    def test_brake_blocks_address_after_five_bad_passwords(self):
        for _ in range(4):
            r, _ = self.c.login("jana", "spatne-heslo-123", ip="203.0.113.5")
            self.assertEqual(r.status, 401)
        r, body = self.c.login("jana", "spatne-heslo-123", ip="203.0.113.5")
        self.assertEqual(r.status, 429)
        self.assertIn("Moc pokusů o přihlášení. Zkus to znovu za 15 minut.", body)
        r, _ = self.c.login("jana", "heslo-jany-123", ip="203.0.113.5")
        self.assertEqual(r.status, 429)                           # ani správné heslo
        r, _ = self.c.login("jana", "heslo-jany-123", ip="198.51.100.7")
        self.assertEqual(r.status, 303)                           # jiná adresa ano

    def test_password_change_and_deletion_end_sessions(self):
        self.c.login("jana", "heslo-jany-123")
        other = Client(self.app, HOST)
        other.login("petr", "heslo-petra-123")
        time.sleep(0.01)
        L.save_user(self.users_file, "jana", "nove-heslo-jany-1")
        r, _ = self.c.req("GET", "/")
        self.assertEqual(r.status, 303)
        r, _ = other.req("GET", "/")
        self.assertEqual(r.status, 200)
        L.delete_user(self.users_file, "petr")
        r, _ = other.req("GET", "/")
        self.assertEqual(r.status, 303)

    def test_edit_view_gets_scoped_kit_and_icons(self):
        self.c.login("jana", "heslo-jany-123")
        r, css = self.c.req("GET", "/_admin/ui.css")
        self.assertEqual(r.status, 200)
        self.assertIn(".ke-ui {", css)
        self.assertNotIn(":root {", css)
        r, body = self.c.req("GET", "/index?e=1")
        self.assertEqual(r.status, 200)
        self.assertIn('<symbol id="ke-i-', body)
        self.assertIn("/_admin/ui.css", body)


class LocalTest(ServerCase):
    public = False

    def test_no_login_and_local_origin(self):
        r, body = self.c.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertNotIn("Odhlásit se", body)
        r, body = self.c.req("GET", "/api/status")
        self.assertEqual(json.loads(body)["role"], "admin")
        r, _ = self.c.req("GET", "/prihlaseni")
        self.assertEqual(r.status, 303)
        r, _ = self.c.req("GET", "/", host="www.example.com")
        self.assertEqual(r.status, 403)
        r, _ = self.c.req("POST", "/api/undo", b"{}", {"Origin": "http://evil.example.com", "X-KE": "1"})
        self.assertEqual(r.status, 403)
        r, _ = self.c.req("POST", "/api/undo", b"{}", {"Origin": "http://localhost:%d" % self.app.port, "X-KE": "1"})
        self.assertNotIn(r.status, (401, 403))

    def test_local_publish_off_points_to_server(self):
        toml = self.root / "cms.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace(
            "[editor]\n", '[editor]\nlocal_publish = false\npublish_url = "%s"\n' % URL), encoding="utf-8")
        app = self.make_app()
        self.addCleanup(app.close)
        self.assertFalse(app.web.publish)
        self.assertEqual(app.web.publish_url, URL)
        self.assertTrue(self.app.web.publish)


class ConfigTest(unittest.TestCase):
    def test_service_refuses_bad_setup(self):
        root = copy_example(tmpdir(self, "cms-editor-cfg-") / "site")
        users = root.parent / "users.json"
        cases = [({"CMS_URL": "http://editor.example.cz", "CMS_USERS": str(users)}, "https://"),
                 ({"CMS_URL": URL + "/web"}, "bez cesty"),
                 ({"CMS_URL": URL}, "CMS_USERS"),
                 ({"CMS_URL": URL, "CMS_USERS": str(users)}, "nejde přečíst")]
        for env, msg in cases:
            with self.assertRaises(SystemExit) as cm:
                E.app_from_env(env, root)
            self.assertIn(msg, str(cm.exception), env)
        users.write_text('{"jana": {"heslo": "x", "role": "editor"}}', encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            E.app_from_env({"CMS_URL": URL, "CMS_USERS": str(users)}, root)
        self.assertIn("pbkdf2_sha256", str(cm.exception))

    def test_publish_deploy_modes(self):
        site = AD.Site(EXAMPLE, {"deploy": {"source": "www"}, "publish": {"deploy": "direct"}})
        with mock.patch.dict(os.environ, {"CMS_DEPLOY_SSH": ""}):
            pub = P.Publisher(EXAMPLE, None, site=site)
            self.assertEqual(pub.deploy_cmd[1:], [str(CMS / "deploy.py"), "--repo", str(EXAMPLE), "deploy",
                                                  "--sha", "{sha}"])
            both = AD.Site(EXAMPLE, {"publish": {"deploy": "direct", "deploy_ssh": "web@127.0.0.1"}})
            self.assertEqual(P.Publisher(EXAMPLE, None, site=both).deploy_cmd[0], "ssh")     # ssh má přednost
            self.assertIsNone(P.Publisher(EXAMPLE, None, site=AD.Site(EXAMPLE, {})).deploy_cmd)
        with self.assertRaises(ValueError):
            AD.Site(EXAMPLE, {"publish": {"deploy": "rsync"}})


class UsersTest(unittest.TestCase):
    def setUp(self):
        self.path = tmpdir(self, "cms-users-") / "users.json"

    def test_save_check_and_delete(self):
        with self.assertRaisesRegex(ValueError, "jméno .*e-mail"):
            L.save_user(self.path, "jana", "heslo-jany-123")
        with self.assertRaisesRegex(ValueError, "aspoň 12 znaků"):
            L.save_user(self.path, "jana", "kratke", name="Jana", email="jana@example.cz")
        with self.assertRaisesRegex(ValueError, "neplatné jméno"):
            L.save_user(self.path, "Jana Nová", "heslo-jany-123", name="Jana", email="jana@example.cz")
        self.assertTrue(L.save_user(self.path, "jana", "heslo-jany-123", name="Jana Nováková", email="jana@example.cz"))
        self.assertFalse(self.path.parent.joinpath(".users-").exists())
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        users = L.Users(self.path)
        self.assertEqual(users.check("jana", "heslo-jany-123"),
                         {"login": "jana", "role": "editor", "name": "Jana Nováková", "email": "jana@example.cz"})
        self.assertIsNone(users.check("jana", "heslo-jany-124"))
        self.assertIsNone(users.check("nikdo", "heslo-jany-123"))
        old = users.password_hash("jana")
        self.assertFalse(L.save_user(self.path, "jana", role="admin"))
        self.assertEqual(users.info("jana")["role"], "admin")     # soubor se čte znovu po změně
        self.assertEqual(users.password_hash("jana"), old)
        with self.assertRaisesRegex(ValueError, "poslední uživatel"):
            L.delete_user(self.path, "jana")
        L.save_user(self.path, "petr", "heslo-petra-123", name="Petr", email="petr@example.cz")
        self.assertTrue(L.delete_user(self.path, "jana"))
        self.assertFalse(L.delete_user(self.path, "jana"))
        self.assertIsNone(users.info("jana"))

    def test_broken_file_keeps_last_good_state(self):
        L.save_user(self.path, "jana", "heslo-jany-123", name="Jana", email="jana@example.cz")
        users = L.Users(self.path)
        time.sleep(0.01)
        self.path.write_text("{rozbité", encoding="utf-8")
        self.assertIsNotNone(users.info("jana"))
        self.assertIn("users.json", users.error)

    def test_cli(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", CMS_USERS=str(self.path))

        def cli(*args, stdin="heslo-jany-123\n"):
            return subprocess.run([sys.executable, str(CMS / "editor.py"), "user", *args], input=stdin, env=env,
                                  capture_output=True, text=True, encoding="utf-8")
        p = cli("jana")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("jméno (--name)", p.stderr)
        p = cli("jana", "--name", "Jana Nováková", "--email", "jana@example.cz")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("heslo-jany", p.stdout + p.stderr)
        first = L.read_raw(self.path)["jana"]["heslo"]
        self.assertEqual(cli("jana", "--role", "admin", stdin="").returncode, 0)
        data = L.read_raw(self.path)["jana"]
        self.assertEqual((data["role"], data["heslo"]), ("admin", first))
        self.assertEqual(cli("jana", "--password", stdin="nove-heslo-jany\n").returncode, 0)
        self.assertNotEqual(L.read_raw(self.path)["jana"]["heslo"], first)
        self.assertIsNotNone(L.Users(self.path).check("jana", "nove-heslo-jany"))


class SessionsTest(unittest.TestCase):
    def test_sliding_validity_and_password_check(self):
        now = [1_800_000_000.0]
        s = L.Sessions(None, clock=lambda: now[0])
        self.addCleanup(s.close)
        pw = {"jana": "hash-1"}
        token = s.create("jana", "hash-1")
        self.assertEqual(s.check(token, pw.get), ("jana", False))
        now[0] += 2 * 86400
        self.assertEqual(s.check(token, pw.get, refresh=True), ("jana", True))
        self.assertEqual(s.check(token, pw.get, refresh=True), ("jana", False))    # nejvýš jednou denně
        now[0] += L.VALID_S - 3600
        self.assertEqual(s.check(token, pw.get), ("jana", False))                  # platnost se posunula
        now[0] += 7200
        self.assertEqual(s.check(token, pw.get), (None, False))
        token = s.create("jana", "hash-1")
        pw["jana"] = "hash-2"
        self.assertEqual(s.check(token, pw.get), (None, False))
        self.assertEqual(s.count(), 0)

    def test_file_survives_restart(self):
        db = tmpdir(self, "cms-sessions-") / "sessions.sqlite"
        token = L.Sessions(db).create("jana", "hash-1")
        self.assertEqual(L.Sessions(db).check(token, {"jana": "hash-1"}.get), ("jana", False))
        L.Sessions(db).drop(token)
        self.assertEqual(L.Sessions(db).check(token, {"jana": "hash-1"}.get), (None, False))

    def test_brake(self):
        now = [0.0]
        b = L.Brake(limit=3, block=60, clock=lambda: now[0])
        b.fail("a")
        b.fail("a")
        self.assertEqual(b.blocked("a"), 0)
        b.fail("a")
        self.assertEqual(b.blocked("a"), 60)
        self.assertEqual(b.blocked("b"), 0)
        now[0] += 61
        self.assertEqual(b.blocked("a"), 0)


class SyncTest(unittest.TestCase):
    """Sync ve službě: checkout se posune na origin/main, restart jen při změně mimo složku stránek."""

    def setUp(self):
        self.work, self.origin, self.other = git_site(self)
        users = self.work.parent / "users.json"
        write_users(users, {"jana": ("heslo-jany-123", "editor")})
        self.app = E.App(self.work, port=0, public_url=URL, users=L.Users(users),
                         journal_dir=self.work.parent / "journal", check_drift=False)
        self.addCleanup(self.app.close)

    def push_other(self, rel, fn):
        f = self.other / rel
        f.write_text(fn(f.read_text(encoding="utf-8")), encoding="utf-8")
        git(self.other, "commit", "-q", "-am", "změna " + rel)
        git(self.other, "push", "-q", "origin", "main")
        return git(self.other, "rev-parse", "HEAD")

    def test_pages_only_no_restart_code_restarts(self):
        self.assertFalse(self.app.sync_once())                   # nic nového
        sha = self.push_other("www/o-dilne.html", lambda s: s.replace("</main>", "<!-- x -->\n</main>", 1))
        self.assertFalse(self.app.sync_once())
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), sha)
        self.push_other("content/site.json", lambda s: s.replace('"name"', '"name"', 1) + "\n")
        self.assertTrue(self.app.sync_once())
        self.assertTrue(self.app.restart_needed(["cms.toml"]))
        self.assertFalse(self.app.restart_needed(["www/index.html", "www/assets/style.css"]))


class LocalPublishTest(unittest.TestCase):
    """Lokální režim: Publikovat = sloučení, build, commit a push s gitem toho, kdo editor pustil, pak
    deploy.py rovnou na [deploy.target] (deploy = "direct") a kontrola na webu."""

    def test_publish_pushes_and_deploys_to_target(self):
        work, origin, _ = git_site(self)
        target = work.parent / "web"
        target.mkdir()
        toml = work / "cms.toml"
        text = toml.read_text(encoding="utf-8").replace('path = "/var/www/example"', 'path = "%s"' % target.as_posix())
        text = text.replace('pages_field = "output"\n', 'pages_field = "output"\nstate = "%s"\n'
                            % (work.parent / "state.json").as_posix())
        toml.write_text(text + '\ndeploy = "direct"\n', encoding="utf-8")       # poslední sekce je [publish]
        git(work, "commit", "-q", "-am", "deploy do složky")
        git(work, "push", "-q", "origin", "main")

        with mock.patch.dict(os.environ, dict(GIT_ENV, CMS_DEPLOY_SSH="")):
            ed = A.Editor(work, journal_dir=work.parent / "journal")

            def fetch(url):
                slug = urllib.parse.urlsplit(url).path.strip("/") or "index"
                f = target / ("%s.html" % slug)
                return (200, f.read_text(encoding="utf-8")) if f.is_file() else (404, "")
            pub = P.Publisher(work, ed.journal, site=ed.site, fetch=fetch, work_dir=work.parent / "wt")
            src = ed.read_page("o-dilne")
            b = next(x for x in B.blocks(src) if H.norm(B.block_text(src, x)).startswith("Dílnu založil"))
            ann = B.annotate(src)
            inner = next(x for x in B.blocks(ann) if x.n == b.n).inner(ann)
            r = ed.save_block("o-dilne", b.n, b.hash(src), B.page_hash(src),
                              inner.replace("Dílnu založil", "Dílnu tehdy založil", 1))
            self.assertTrue(r["ok"], r)
            st = pub.run("Úprava O dílně")
        self.assertEqual(st["state"], "done", json.dumps(st, ensure_ascii=False, indent=1))
        self.assertEqual([s["state"] for s in st["steps"]], ["done"] * 6)
        head = git(origin, "rev-parse", "main")
        self.assertEqual(git(work, "rev-parse", "HEAD"), head)
        self.assertEqual(git(origin, "log", "-1", "--format=%s", "main"), "Úprava O dílně")
        self.assertIn("Dílnu tehdy založil", (target / "o-dilne.html").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((work.parent / "state.json").read_text(encoding="utf-8"))["deployed"]["sha"],
                         head)
        self.assertEqual(ed.journal.unpublished(), [])


class InstallGuideTest(unittest.TestCase):
    """INSTALACE.md a soubory install/ mluví o tom, co v balíčku je: příkazy, soubory, proměnné, port,
    čísla přihlášení, kroky publikace a hlášky, podle kterých čtenář pozná, že krok prošel."""

    def setUp(self):
        self.guide = (CMS / "INSTALACE.md").read_text(encoding="utf-8")
        self.install = {p.name: p.read_text(encoding="utf-8") for p in (CMS / "install").iterdir()}
        self.code = (CMS / "editor.py").read_text(encoding="utf-8")

    def test_commands_files_and_variables_exist(self):
        cmds = re.findall(r"\bcms/(\w+\.py)", self.guide)
        files = re.findall(r"cms/((?:install|bin)/[\w.-]+)", self.guide + "".join(self.install.values()))
        names = set(re.findall(r"\bCMS_[A-Z_]+\b", self.guide + self.install["editor.env"]))
        self.assertLessEqual({"editor.py", "deploy.py"}, set(cmds))
        self.assertIn("python3 ~/web/cms/editor.py user jana --users ~/data/users.json", self.guide)
        self.assertLessEqual({"install/cms-editor.service", "install/editor.env", "install/Caddyfile",
                              "install/github-deploy.yml", "bin/cms-deploy"}, set(files))
        self.assertEqual([c for c in cmds if not (CMS / c).is_file()], [])
        self.assertEqual([f for f in files if not (CMS / f).is_file()], [])
        self.assertEqual(sorted(n for n in names if "  %s " % n not in self.code), [])     # popsané v hlavičce
        self.assertEqual({k for k in re.findall(r"^(CMS_\w+)=", self.install["editor.env"], re.M)} - names, set())

    def test_service_proxy_and_port_agree(self):
        unit, env, caddy = self.install["cms-editor.service"], self.install["editor.env"], self.install["Caddyfile"]
        self.assertIn("ExecStart=/usr/bin/python3 cms/editor.py\n", unit)
        self.assertIn("Restart=always\n", unit)                    # restart kódem EXIT_RESTART po syncu
        self.assertIn("WorkingDirectory=/home/cms/web\n", unit)
        self.assertIn("git clone git@github.com:vas-ucet/mojedilna.git ~/web", self.guide)
        self.assertIn("EnvironmentFile=/home/cms/editor.env\n", unit)
        self.assertIn("install -m 600 ~/web/cms/install/editor.env ~/editor.env", self.guide)
        self.assertIn("CMS_PORT=%d\n" % E.PORT, env)
        self.assertIn("reverse_proxy 127.0.0.1:%d {" % E.PORT, caddy)
        self.assertIn("proxy_pass http://127.0.0.1:%d;" % E.PORT, self.guide)
        self.assertIn("header_up X-Real-IP {remote_host}", caddy)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;", self.guide)
        self.assertGreaterEqual(5 * 1024 * 1024, E.MAX_BODY)        # client_max_body_size 5m v návodu
        self.assertIn("client_max_body_size 5m;", self.guide)
        self.assertIn("Editor webu -> 127.0.0.1:%d za", self.code)
        self.assertIn("install -d -m 700 ~/data ~/data/journal ~/data/work", self.guide)
        for k in ("CMS_JOURNAL=/home/cms/data/journal", "CMS_WORK=/home/cms/data/work"):
            self.assertIn(k + "\n", env)

    def test_numbers_and_messages_match_code(self):
        brake = L.Brake()
        self.assertIn("po %d špatných pokusech z jedné adresy ji na %d minut" % (brake.limit, brake.block // 60),
                      self.guide)
        self.assertIn("aspoň %d znaků" % L.MIN_LEN, self.guide)
        self.assertIn("kroky %s a %s" % (", ".join(lbl for _, lbl in P.STEPS[:-1]), P.STEPS[-1][1]),
                      self.guide.replace("\n  ", " "))
        self.assertIn("(výchozí %d)" % E.SYNC_S, self.guide)
        deploy = (CMS / "deploy.py").read_text(encoding="utf-8")
        self.assertIn("OK: Deployed version", deploy)
        self.assertIn('"Změněno: %s', deploy)
        self.assertIn("CHYBA: režim cms umí jen deploy, dry-run a status",
                      self.guide.replace("\n   ", " "))
        self.assertIn("die \"režim cms umí jen deploy, dry-run a status\"",
                      (CMS / "bin" / "cms-deploy").read_text(encoding="utf-8"))
        self.assertIn("Push selhal, nic se nepublikovalo", (CMS / "publish.py").read_text(encoding="utf-8"))
        flow = self.install["github-deploy.yml"]
        for name in ("secrets.DEPLOY_KEY", "secrets.DEPLOY_KNOWN_HOSTS", "vars.DEPLOY_SSH"):
            self.assertIn(name, flow)
            self.assertIn("`%s`" % name.split(".")[1], self.guide)


if __name__ == "__main__":
    unittest.main()
