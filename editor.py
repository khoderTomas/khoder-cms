# -*- coding: utf-8 -*-
"""Samostatný server editoru (Khoder CMS): komponenta app.Web s vlastním přihlášením, šasi a syncem
checkoutu. Jen standardní knihovna, Python 3.12+. Instalace na server: INSTALACE.md.

  python3 cms/editor.py                 lokální režim, http://127.0.0.1:8765 bez přihlášení
  python3 cms/editor.py user <login> [--role admin|editor] [--name JMÉNO] [--email E-MAIL] [--delete]
                                        uživatel v souboru CMS_USERS (--users), heslo z prvního řádku stdin
  python3 cms/editor.py --root <repo>   repo webu jinde (výchozí složka nad cms/)

Režimy:
- lokální (bez CMS_URL): poslouchá jen na 127.0.0.1 a přihlášení nemá (uživatel local, role admin).
  Publikovat sloučí úpravy s main, udělá build, commit a push s gitem toho, kdo editor pustil, a commit
  nasadí podle cms.toml [publish]: deploy_ssh (ssh na server s vynuceným příkazem cms-deploy cms),
  nebo deploy = "direct" (cms/deploy.py rovnou na [deploy.target]). S [editor] local_publish = false
  je Publikovat vypnuté a editor odkáže na [editor] publish_url (editor na serveru).
- služba (CMS_URL): za reverse proxy s HTTPS, poslouchá jen na 127.0.0.1. Každý požadavek kromě
  /health, přihlášení, stylů přihlašovací stránky a souborů webu pro náhled chce přihlášení formulářem
  (soubor uživatelů CMS_USERS, login.py), bez platného souboru služba nenastartuje. Každých CMS_SYNC_S
  sekund posune checkout na origin/main (gitsync.sync), když neběží publikace. Když se tím změnilo
  něco mimo složku stránek ([deploy] source), skončí pod zámky zápisu kódem 10 a systemd ji spustí
  znovu s novým kódem (Restart=always).

Ochrana: jen známý Host (127.0.0.1:<port>, localhost:<port>, host z CMS_URL). Zápis do API jen
s hlavičkou X-KE: 1 a s Origin editoru, formulář přihlášení a odhlášení jen s Origin editoru.
Adresa klienta pro brzdu přihlášení je X-Real-IP od proxy, jinak poslední položka X-Forwarded-For.

Proměnné prostředí:
  CMS_URL         veřejná adresa editoru (https://…), bez ní lokální režim
  CMS_USERS       soubor uživatelů (JSON, login.py), ve službě povinný
  CMS_SESSIONS    SQLite se sezeními přihlášení (bez ní paměť procesu, restart odhlásí)
  CMS_PORT        port na 127.0.0.1 (výchozí 8765)
  CMS_JOURNAL     journal úprav (výchozí cms.toml [editor] journal), na serveru mimo checkout
  CMS_WORK        složka dočasných worktree publikace (výchozí systémový temp)
  CMS_SYNC_S      interval syncu checkoutu ve službě v sekundách (výchozí 120, 0 = vypnuto)
  CMS_DEPLOY_SSH  cíl ssh deploye (přebíjí cms.toml [publish] deploy_ssh)

Cesty: /prihlaseni, POST /prihlaseni a /odhlasit, /health; všechno ostatní je editor (app.py).
"""
import argparse
import getpass
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
import tomllib
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import app as W
import apply as A
import gitsync as G
import login as L
import publish as P

PKG = Path(__file__).resolve().parent
STATIC = PKG / "static"
PORT = 8765
SYNC_S = 120
EXIT_RESTART = 10
MAX_BODY = 5_000_000
LOGIN, LOGOUT = "/prihlaseni", "/odhlasit"
HTML_CT = "text/html; charset=utf-8"
TEXT_CT = "text/plain; charset=utf-8"
JSON_CT = "application/json; charset=utf-8"
CSS = ("tokens.css", "components.css", "editor.css", "host.css")
PUBLIC_STATIC = ("tokens.css", "components.css", "host.css")      # styly přihlašovací stránky
LOGIN_CSP = ("default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
             "frame-ancestors 'none'; base-uri 'none'")
OFFLINE = "Server editoru neodpovídá. Zkus to za chvíli znovu."
ICON_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FILES = {}


def cms_config(root):
    """[deploy] site a [editor] local_publish, publish_url z cms.toml."""
    try:
        with open(Path(root) / "cms.toml", "rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        cfg = {}
    ed = cfg.get("editor") or {}
    return {"site": (cfg.get("deploy") or {}).get("site") or "", "local_publish": bool(ed.get("local_publish", True)),
            "publish_url": ed.get("publish_url") or None}


def static_file(name):
    """(obsah, krátký otisk) souboru ze static/; čte se znovu jen po změně souboru."""
    f = STATIC / name
    key = f.stat().st_mtime_ns
    hit = _FILES.get(f)
    if not hit or hit[0] != key:
        data = f.read_bytes()
        hit = _FILES[f] = (key, data, hashlib.sha1(data).hexdigest()[:10])
    return hit[1], hit[2]


def asset(name):
    """URL statiky s verzí podle obsahu: nové HTML nedostane z cache staré CSS ani JS."""
    return "/_admin/%s?v=%s" % (name, static_file(name)[1])


def icons():
    return static_file("icons.svg")[0].decode("utf-8").strip()


def ic(name, size=None, cls=""):
    """Ikona ze spritu static/icons.svg (id i-<jméno>). name je vždy konstanta z kódu, nikdy vstup."""
    if not ICON_RE.match(name):
        raise ValueError("Neplatné jméno ikony: %r" % name)
    return '<svg class="k-ic%s%s" aria-hidden="true"><use href="#i-%s"/></svg>' % (
        " k-ic--%d" % size if size else "", " " + cls if cls else "", name)


def safe_next(dalsi):
    """Kam po přihlášení: jen cesta na tomtéž serveru (/…, ne //… ani se zpětným lomítkem), jinak /."""
    d = dalsi or "/"
    if (not d.startswith("/") or d.startswith("//") or "\\" in d or any(ord(c) < 32 for c in d)
            or d == LOGIN or d.startswith(LOGIN + "?")):
        return "/"
    return d


def log(msg):
    """Řádek do logu služby (stdout, journalctl)."""
    print(msg, flush=True)


def plural_minutes(seconds):
    return P.plural(max(1, (seconds + 59) // 60), "minutu", "minuty", "minut")


class Host:
    """Šasi stránek, ikony a sada prvků editoru pro app.Web: lišta s názvem webu a uživatelem, bez sidebaru."""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def ic(name, size=None, cls=""):
        return ic(name, size, cls)

    @staticmethod
    def ui_css():
        """tokens.css a components.css s :root přepsaným na .ke-ui: web má proměnné stejného jména
        (--ink, --surface…), tokeny sady proto nad stránkou webu platí jen v prvcích editoru."""
        out = ["/* Sada design language jen pro prvky editoru (.ke-ui), skládá cms/editor.py */"]
        for n in ("tokens.css", "components.css"):
            text = static_file(n)[0].decode("utf-8")
            if text.count(":root {") != 1:
                raise ValueError("%s: čekám právě jeden :root (tokeny pro .ke-ui)" % n)
            out.append(text.replace(":root {", ".ke-ui {"))
        return "\n".join(out)

    @staticmethod
    def sprite():
        """Sprite ikon pro úpravu stránky: id s předponou ke-i- (web může mít vlastní #i-…)."""
        return icons().replace('<symbol id="i-', '<symbol id="ke-i-')

    def page(self, title, body, crumbs, metas):
        return self.app.page(title, body, crumbs, metas)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):
            return                                    # prohlížeč odešel uprostřed odpovědi
        super().handle_error(request, client_address)


class App:
    """root = repo webu (checkout), public_url + users = služba s přihlášením, jinak lokální režim.
    sessions = cesta k SQLite se sezeními (None = paměť). publisher a check_drift jdou podstrčit testům."""

    def __init__(self, root, port=PORT, public_url=None, users=None, sessions=None, journal_dir=None,
                 work_dir=None, publisher=None, check_drift=True):
        if public_url and users is None:
            raise ValueError("Veřejná adresa bez přihlášení: chybí soubor uživatelů (CMS_USERS).")
        self.root = Path(root).resolve()
        self.port = port
        self.public = urllib.parse.urlsplit(public_url) if public_url else None
        self.users = users
        self.brake = L.Brake() if users else None
        self.sessions = L.Sessions(sessions) if users else None
        self.exit_code = 0
        self._stop = threading.Event()
        cfg = cms_config(self.root)
        editor = A.Editor(self.root, journal_dir)
        publisher = publisher or P.Publisher(self.root, editor.journal, work_dir=work_dir, site=editor.site)
        self.web = W.Web(editor, publisher, Host(self), publish=bool(self.public) or cfg["local_publish"],
                         publish_url=cfg["publish_url"], static_dir=STATIC, check_drift=check_drift, offline=OFFLINE)
        self.title = urllib.parse.urlsplit(self.web.site).netloc or "Editor webu"
        if check_drift:
            threading.Thread(target=self.web.check_drift, daemon=True).start()
            editor.warm()                        # sonda proveniance celého webu, první stránka bez čekání
        self.code_rev = self._head()

    # ── šasi ──
    def page(self, title, body, crumbs, metas):
        """Stránka editoru (seznam stránek, zdroj stránky): lišta s názvem webu, drobečky a uživatelem."""
        esc = html.escape
        metas = dict({"ke-offline": OFFLINE}, **metas)
        cr = []
        for i, (label, href) in enumerate(crumbs):
            if i:
                cr.append('<span class="k-crumb__sep" aria-hidden="true">›</span>')
            cr.append('<a class="k-crumb__item" href="%s">%s</a>' % (esc(href), esc(label)) if href
                      else '<span class="k-crumb__current">%s</span>' % esc(label))
        user = A.actor()
        who = ""
        if self.users and user.get("login") != A.LOCAL["login"]:
            who = ('<div class="h-user"><span class="k-role">%s</span><span class="h-user__name">%s</span>'
                   '<form method="post" action="%s"><button class="k-btn k-btn--ghost k-btn--sm" type="submit">%s'
                   'Odhlásit se</button></form></div>'
                   % (esc(user.get("role") or ""), esc(user.get("name") or user.get("login") or ""), LOGOUT,
                      ic("log-out")))
        return ("<!doctype html>\n<html lang=\"cs\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<meta name=\"robots\" content=\"noindex, nofollow\"><title>%s</title>%s\n%s</head>\n"
                "<body class=\"k-app\">\n%s\n"
                "<div class=\"k-shell k-shell--no-side\" data-theme=\"light\" id=\"shell\">\n"
                "<header class=\"k-top\">\n  <div class=\"k-top__brand\"><a class=\"h-site\" href=\"/\">%s</a></div>\n"
                "  <div class=\"k-top__main\">\n    <nav class=\"k-crumb\" aria-label=\"Drobečková navigace\">%s</nav>\n"
                "    <div class=\"k-top__tools\" id=\"top-tools\">%s</div>\n  </div>\n</header>\n"
                "<main class=\"k-main\"><div class=\"k-wrap\">\n%s\n</div></main>\n</div>\n"
                "<script src=\"%s\" defer></script>\n</body></html>\n"
                % (esc(title), "".join('<meta name="%s" content="%s">' % (k, esc(v)) for k, v in metas.items()),
                   "".join('<link rel="stylesheet" href="%s">' % asset(c) for c in CSS), icons(), esc(self.title),
                   "".join(cr), who, body, asset("editor.js")))

    def login_page(self, dalsi, state=None, login="", left=0):
        """Přihlašovací stránka (karta .k-auth sady). state: None, chyba, brzda (left sekund), odhlaseno."""
        esc = html.escape

        def notice(kind, text):
            return '<div class="k-notice%s" role="%s">%s<span>%s</span></div>' % (
                " k-notice--" + kind if kind else "", "alert" if kind == "err" else "status",
                ic("circle-alert" if kind == "err" else "info"), esc(text))
        n = {"chyba": notice("err", "Špatný uživatel nebo heslo."),
             "brzda": notice("err", "Moc pokusů o přihlášení. Zkus to znovu za %s." % plural_minutes(left)),
             "odhlaseno": notice("", "Odhlášeno.")}.get(state, "")
        bad = ' aria-invalid="true"' if state == "chyba" else ""
        dis = " disabled" if state == "brzda" else ""
        days = L.VALID_S // 86400
        body = """<div class="k-auth">
  <div class="h-brand">%s</div>
  <div class="k-auth__card">
    <h1 class="k-auth__title">Přihlášení</h1>
    <p class="k-auth__desc">Editor webu</p>
    %s
    <form class="k-stack h-login" method="post" action="%s">
      <input type="hidden" name="dalsi" value="%s">
      <div class="k-field">
        <label class="k-field__label" for="l-user">Uživatel</label>
        <input class="k-input" id="l-user" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required value="%s"%s%s>
      </div>
      <div class="k-field">
        <label class="k-field__label" for="l-pass">Heslo</label>
        <input class="k-input" id="l-pass" name="password" type="password" autocomplete="current-password" required%s%s>
      </div>
      <button class="k-btn k-btn--primary k-btn--block" type="submit"%s>Přihlásit se</button>
    </form>
    <div class="k-auth__foot">Přihlášení platí %s od posledního otevření editoru.</div>
  </div>
</div>""" % (esc(self.title), n, LOGIN, esc(safe_next(dalsi)), esc(login), bad, dis, bad, dis, dis,
             P.plural(days, "den", "dny", "dní"))
        return ("<!doctype html>\n<html lang=\"cs\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<meta name=\"robots\" content=\"noindex, nofollow\"><title>Přihlášení · %s</title>%s</head>\n"
                "<body class=\"k-app\">\n%s\n%s\n</body></html>\n"
                % (esc(self.title), "".join('<link rel="stylesheet" href="%s">' % asset(c) for c in PUBLIC_STATIC),
                   icons().replace(' style="display:none"', " hidden", 1), body))

    # ── sync checkoutu (jen služba) ──
    def _head(self):
        try:
            return G.Git(self.root).rev("HEAD") if (self.root / ".git").exists() else None
        except G.GitError:
            return None

    def restart_needed(self, changed):
        """Změnilo se něco mimo složku stránek ([deploy] source)? Stránky editor čte při každém
        požadavku, kód motoru, adaptér, data a cms.toml jen při startu."""
        src = self.web.editor.site.source.strip("/") + "/"
        return any(not f.startswith(src) for f in changed)

    def sync_once(self):
        """Posune checkout na origin/main pod zámkem publikace, když publikace neběží. Stav pro hlášku
        (neodeslané úpravy blokují posun) dá editoru. Vrací True, když se má editor restartovat."""
        web = self.web
        if self.code_rev is None or web.publisher.running():
            return False
        git = G.Git(self.root)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            r = G.sync(git, wait=5)
        except G.Busy:
            return False
        except G.GitError as e:
            web.sync = {"error": str(e), "checked": now}
            return False
        state = None
        if r["blocked"]:
            head, target = r["from"], r["to"]
            touch = {c["sha"] for c in git.log_detail(head, target, r["blocked"], limit=50)}
            commits = [dict(c, touches=c["sha"] in touch) for c in git.log_detail(head, target, limit=8)]
            state = {"blocked": r["blocked"], "commits": commits, "count": git.log(head, target, limit=0)[0],
                     "pages": sorted({p for f in r["blocked"] for p in [web.editor.site.page_slug(f)] if p}),
                     "head": git.out("log", "-1", "--format=%cI", head), "checked": now}
        web.sync = state
        return self.restart_needed(git.changed(self.code_rev, git.rev("HEAD")))

    def start_sync(self, interval, on_restart):
        """Vlákno syncu: každých interval sekund sync_once(); restart (on_restart, kód EXIT_RESTART)
        až pod zámkem zápisu, ať nepřeruší ukládání ani publikaci."""
        def loop():
            while not self._stop.wait(interval):
                try:
                    if not self.sync_once():
                        continue
                    with A.write_lock(self.root, "restart editoru"):
                        if self.web.publisher.running():
                            continue
                        log("sync: změnil se kód nebo data, restart")
                        self.exit_code = EXIT_RESTART
                        on_restart()
                        self._stop.wait(30)               # zámky drží proces až do konce
                        return
                except Exception:                         # noqa: BLE001 — sync nesmí shodit editor
                    traceback.print_exc()
        threading.Thread(target=loop, daemon=True).start()

    def close(self):
        """Zastaví sync a zavře sezení (testy, konec procesu)."""
        self._stop.set()
        if self.sessions is not None:
            self.sessions.close()

    # ── HTTP ──
    def bind(self, port=None):
        srv = Server(("127.0.0.1", self.port if port is None else port), type("Handler", (Handler,), {"app": self}))
        self.port = srv.server_address[1]
        return srv

    def hosts(self):
        h = {"127.0.0.1:%d" % self.port, "localhost:%d" % self.port}
        if self.public:
            h.add(self.public.netloc)
        return h

    def origins(self):
        """Původy, ze kterých editor přijme zápis: ve službě jen veřejná adresa."""
        if self.public:
            return {"%s://%s" % (self.public.scheme, self.public.netloc)}
        return {"http://" + h for h in self.hosts()}

    def session_user(self, cookie, refresh=False):
        """(uživatel, token, poslat cookie znovu) z hlavičky Cookie, nebo (None, None, False)."""
        if self.sessions is None:
            return None, None, False
        token = L.token_from(cookie)
        login, moved = self.sessions.check(token, self.users.password_hash, refresh=refresh) if token else (None, False)
        info = self.users.info(login) if login else None
        if not info:
            return None, None, False
        return info, token, moved



class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "cms-editor"

    def log_message(self, *a):
        pass

    # ── odpovědi ──
    def _send(self, body, ct, code=200, headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        cookie = getattr(self, "_cookie", None)    # posun nebo smazání cookie sezení
        if cookie:
            self._cookie = None
            self.send_header("Set-Cookie", cookie)
        super().end_headers()

    def _see_other(self, location, cookie=None):
        self._cookie = cookie or getattr(self, "_cookie", None)
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _web(self, method, path, query, raw=b""):
        code, headers, body = self.app.web.handle(method, path, query, raw, self.user, self._ip())
        ct = headers.pop("Content-Type", TEXT_CT)
        return self._send(body, ct, code, headers)

    def _json_error(self, msg, code):
        return self._send(json.dumps({"error": msg}, ensure_ascii=False), JSON_CT, code)

    # ── ochrana ──
    def _host_ok(self):
        return self.headers.get("Host", "") in self.app.hosts()

    def _ip(self):
        """Adresa klienta: ve službě od reverse proxy (editor poslouchá jen na 127.0.0.1), lokálně ze spojení."""
        if self.app.public:
            real = (self.headers.get("X-Real-IP") or "").strip()
            fwd = [p.strip() for p in (self.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
            return real or (fwd[-1] if fwd else self.client_address[0])
        return self.client_address[0]

    def _auth_ok(self):
        """Lokálně bez přihlášení (uživatel local, role admin), ve službě platné sezení z cookie."""
        self.user = None
        self._cookie = None
        if not self.app.users:
            self.user = dict(A.LOCAL)
            return True
        user, token, moved = self.app.session_user(self.headers.get("Cookie"), refresh=True)
        if not user:
            return False
        self.user = user
        if moved:
            self._cookie = L.set_cookie(token)
        return True

    def _deny(self, path):
        """Bez přihlášení: stránka → přihlašovací formulář, API → 401 s hláškou pro editor.js."""
        if self.command == "GET" and not path.startswith("/api/"):
            return self._see_other("%s?dalsi=%s" % (LOGIN, urllib.parse.quote(safe_next(self.path), safe="")))
        return self._json_error("Přihlášení vypršelo. Obnov stránku a přihlas se znovu.", 401)

    def _post_ok(self):
        if self.headers.get("X-KE") != "1":
            return False
        o = urllib.parse.urlsplit(self.headers.get("Origin") or "")
        return "%s://%s" % (o.scheme, o.netloc) in self.app.origins()

    def _form_ok(self):
        """Formulář z vlastní stránky: Origin editoru, bez Origin (nebo null) rozhoduje Sec-Fetch-Site."""
        origin = self.headers.get("Origin") or ""
        if origin in ("", "null"):
            return self.headers.get("Sec-Fetch-Site") == "same-origin"
        o = urllib.parse.urlsplit(origin)
        return "%s://%s" % (o.scheme, o.netloc) in self.app.origins()

    # ── GET ──
    def do_GET(self):
        if not self._host_ok():
            return self._send("Neznámý Host.", TEXT_CT, 403)
        u = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(u.path)
        q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        app = self.app
        try:
            if path == "/health":
                return self._send("ok\n", TEXT_CT)
            if path == LOGIN:
                return self._login_get(q)
            if path.startswith("/_admin/") and path[len("/_admin/"):] in PUBLIC_STATIC:
                return self._send(static_file(path[len("/_admin/"):])[0], W.TYPES[".css"])
            if app.web.public("GET", path):             # soubory webu pro náhled v sandboxu
                self.user = None
                return self._web("GET", path, q)
            if not self._auth_ok():
                return self._deny(path)
            return self._web("GET", path, q)
        except Exception:                               # noqa: BLE001
            traceback.print_exc()
            return self._send("Chyba serveru.", TEXT_CT, 500)

    def _login_get(self, q):
        app = self.app
        dalsi = safe_next((q.get("dalsi") or ["/"])[0])
        if not app.users or app.session_user(self.headers.get("Cookie"))[0]:
            return self._see_other(dalsi)               # lokálně přihlášení není, přihlášený rovnou dál
        left = app.brake.blocked(self._ip())
        state = "brzda" if left else ("odhlaseno" if "odhlaseno" in q else None)
        return self._send(app.login_page(dalsi, state, left=left), HTML_CT, 429 if left else 200,
                          headers={"Content-Security-Policy": LOGIN_CSP})

    # ── POST ──
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= MAX_BODY:
            return self._json_error("Neplatná délka požadavku.", 413)
        raw = self.rfile.read(n)                        # tělo se čte vždy, jinak Windows spojení utne
        if not self._host_ok():
            return self._send("Neznámý Host.", TEXT_CT, 403)
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path in (LOGIN, LOGOUT):
                return self._form_post(path, raw)
            if not self._auth_ok():
                return self._deny(path)
            if not self._post_ok():
                return self._json_error("Odmítnuto: úpravy jdou jen z vlastní stránky editoru.", 403)
            if not path.startswith("/api/"):
                return self._json_error("Neznámé API.", 404)
            return self._web("POST", path, {}, raw)
        except Exception:                               # noqa: BLE001
            traceback.print_exc()
            return self._json_error("Chyba serveru.", 500)

    def _form_post(self, path, raw):
        """POST /prihlaseni a /odhlasit: obyčejný formulář bez X-KE, proto jen s Origin editoru."""
        app = self.app
        if not self._form_ok():
            return self._send("Odmítnuto: formulář jen ze stránky editoru.", TEXT_CT, 403)
        if not app.users:
            return self._see_other("/")
        form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        if path == LOGOUT:
            app.sessions.drop(L.token_from(self.headers.get("Cookie")))
            return self._see_other(LOGIN + "?odhlaseno=1", L.clear_cookie())
        login = (form.get("username") or [""])[0].strip()
        dalsi = safe_next((form.get("dalsi") or ["/"])[0])
        ip = self._ip()
        left = app.brake.blocked(ip)
        if left:
            return self._send(app.login_page(dalsi, "brzda", login, left), HTML_CT, 429,
                              headers={"Content-Security-Policy": LOGIN_CSP, "Retry-After": str(left)})
        user = app.users.check(login, (form.get("password") or [""])[0])
        if not user:
            app.brake.fail(ip)
            left = app.brake.blocked(ip)
            return self._send(app.login_page(dalsi, "brzda" if left else "chyba", login, left), HTML_CT,
                              429 if left else 401, headers={"Content-Security-Policy": LOGIN_CSP})
        app.brake.ok(ip)
        token = app.sessions.create(user["login"], app.users.password_hash(user["login"]))
        log("přihlášení: %s z %s" % (user["login"], ip))
        return self._see_other(dalsi, L.set_cookie(token))


# ── spuštění ──
def app_from_env(env, root):
    """App z proměnných prostředí; chyba nastavení = SystemExit se zprávou."""
    public = env.get("CMS_URL") or None
    users = None
    if public:
        u = urllib.parse.urlsplit(public)
        if u.scheme != "https" or not u.netloc or u.path not in ("", "/"):
            raise SystemExit("CMS_URL musí být adresa https:// bez cesty, třeba https://editor.example.cz "
                             "(cookie přihlášení jde jen přes HTTPS), ne %r." % public)
        public = "https://" + u.netloc
        if not env.get("CMS_USERS"):
            raise SystemExit("Služba s CMS_URL potřebuje soubor uživatelů CMS_USERS "
                             "(python3 cms/editor.py user <login>).")
        try:
            users = L.Users(env["CMS_USERS"])
        except ValueError as e:
            raise SystemExit(str(e)) from None
    try:
        return App(root, port=int(env.get("CMS_PORT") or PORT), public_url=public, users=users,
                   sessions=env.get("CMS_SESSIONS") or None, journal_dir=env.get("CMS_JOURNAL") or None,
                   work_dir=env.get("CMS_WORK") or None)
    except ValueError as e:
        raise SystemExit(str(e)) from None


def read_password():
    """Heslo: v terminálu dvakrát skrytě, jinak první řádek stdin (roura ze souboru, nic se nevypíše)."""
    if sys.stdin.isatty():
        pw = getpass.getpass("Heslo: ")
        if getpass.getpass("Heslo znovu: ") != pw:
            raise SystemExit("Hesla se neshodují, nic se nezměnilo.")
        return pw
    return sys.stdin.readline().rstrip("\r\n")


def user_cmd(args):
    path = args.users or os.environ.get("CMS_USERS")
    if not path:
        raise SystemExit("Soubor uživatelů: --users <soubor> nebo CMS_USERS.")
    try:
        if args.delete:
            if not L.delete_user(path, args.login):
                raise SystemExit("Uživatel %s v %s není." % (args.login, path))
            print("Uživatel %s smazán, jeho přihlášení skončila." % args.login)
            return 0
        exists = args.login in L.read_raw(path)
        pw = read_password() if not exists or args.password else None
        new = L.save_user(path, args.login, pw, args.role, args.name, args.email)
    except ValueError as e:
        raise SystemExit("Nic se nezměnilo: %s." % e) from None
    print("Uživatel %s %s v %s." % (args.login, "založen" if new else "upraven", path))
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:                             # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(prog="editor.py", description="Editor webu Khoder CMS: lokálně, nebo jako služba (CMS_URL).")
    ap.add_argument("--root", default=str(PKG.parent), help="repo webu (výchozí složka nad cms/)")
    sub = ap.add_subparsers(dest="cmd")
    u = sub.add_parser("user", help="založí nebo upraví uživatele v souboru uživatelů (heslo ze stdin)")
    u.add_argument("login")
    u.add_argument("--users", help="soubor uživatelů (výchozí CMS_USERS)")
    u.add_argument("--role", choices=L.ROLES, help="role (nový uživatel výchozí editor)")
    u.add_argument("--name", help="jméno, autor commitu publikace")
    u.add_argument("--email", help="e-mail, autor commitu publikace")
    u.add_argument("--password", action="store_true", help="změnit heslo existujícího uživatele")
    u.add_argument("--delete", action="store_true", help="smazat uživatele")
    args = ap.parse_args(argv)
    if args.cmd == "user":
        return user_cmd(args)
    app = app_from_env(os.environ, args.root)
    try:
        srv = app.bind()
    except OSError as e:
        print("Port %d je obsazený (%s). Neběží už editor v jiném okně?" % (app.port, e), file=sys.stderr)
        return 1
    if app.public:
        print("Editor webu -> 127.0.0.1:%d za %s (uživatelé %s)" % (app.port, app.public.geturl(), app.users.path),
              flush=True)
        if not os.environ.get("CMS_SESSIONS"):
            print("  ! Bez CMS_SESSIONS drží editor přihlášení v paměti, restart odhlásí.", flush=True)
        interval = int(os.environ.get("CMS_SYNC_S") or SYNC_S)
        if interval > 0:
            app.start_sync(interval, lambda: threading.Thread(target=srv.shutdown, daemon=True).start())
    else:
        print("Editor webu -> http://127.0.0.1:%d  (Ctrl+C = konec)" % app.port, flush=True)
        if not app.web.publish:
            print("Publikovat je tu vypnuté (cms.toml [editor] local_publish), publikuje se z %s"
                  % (app.web.publish_url or "editoru na serveru"), flush=True)
    for m in app.web.messages:
        print("  ! " + m, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return app.exit_code


if __name__ == "__main__":
    sys.exit(main())
