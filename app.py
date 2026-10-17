# -*- coding: utf-8 -*-
"""Editor webu jako komponenta (Khoder CMS): seznam stránek, úprava textů, náhled, API a publikace
pod cestou base. Hostitel (admin pod /web, vývojový editor pod /) předá požadavek Web.handle()
a dostane (status, hlavičky, tělo). Přihlášení dělá hostitel a předá uživatele {login, role, name,
email}; šasi stránek a ikony dodá přes host.page() a host.ic(). Co je specifické pro web, dodá
adaptér (adapter.py, cms.toml [editor] adapter) přes editor.site.

Cesty relativně k base:
GET  /                 seznam stránek (šasi hostitele)
     /<slug>           náhled se skripty webu, CSP sandbox: skripty webu nevidí API ani přihlášení
     /<slug>?e=1       úpravy textů, CSP pouští jen editor.js (nonce)
     /<slug>?src=1     celý zdroj stránky (jen role admin)
     /_admin/<soubor>  statika editoru, /_admin/site.css styly editačního pohledu webu (cms.toml [editor] css)
     /<soubor>         soubory webu ze složky stránek (ne HTML stránky) bez přihlášení: náhled v sandboxu je
                       načítá bez přihlašovacích údajů; chybějící /assets/* → 302 na web
     /api/pages | status | journal | changes | source | seo | find | publish/preview | publish/status
POST /api/save | attrs | undo | discard | unlock | lock | source | seo | replace | publish | deploy
     (attrs = panel Další texty: alt, title, aria-label, placeholder a text v SVG, apply.save_attrs)
"""
import collections
import html
import json
import re
import secrets
import subprocess
import time
import traceback
from pathlib import Path

import adapter as AD
import apply as A
import blocks as B
import buildlock
import publish as P
import replace as R
import view as V

HTML = "text/html; charset=utf-8"
JSON = "application/json; charset=utf-8"
TEXT = "text/plain; charset=utf-8"
TYPES = {".html": HTML, ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".json": "application/json", ".xml": "application/xml", ".txt": TEXT, ".webmanifest": "application/manifest+json",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".avif": "image/avif", ".gif": "image/gif", ".ico": "image/x-icon",
         ".woff2": "font/woff2", ".woff": "font/woff", ".mp4": "video/mp4", ".webm": "video/webm",
         ".pdf": "application/pdf"}
KINDS = {"handbuilt": "ruční", "generated": "generovaná", "owner": "owner"}
WRITES = {"save", "attrs", "undo", "discard", "source", "seo", "replace"}
# Náhled: skripty webu běží v sandboxu bez allow-same-origin, takže API editoru nečtou a nezapíšou
SANDBOX = "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms"
# Editační pohled: jen editor.js (nonce), styly a písma webu, obrázky i z webu (chybějící /assets → 302)
EDIT_CSP = ("default-src 'self'; script-src 'nonce-%s'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: https:; media-src 'self' https:; "
            "connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")


def git_dates(root, source="dist"):
    """{<source>/<slug>.html: YYYY-MM-DD} poslední commit každé stránky."""
    try:
        out = subprocess.run(["git", "log", "--format=>%cs", "--name-only", "--", "%s/*.html" % source], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    dates, cur = {}, None
    for line in out.splitlines():
        if line.startswith(">"):
            cur = line[1:]
        elif line.strip():
            dates.setdefault(line.strip(), cur)
    return dates


_TITLES = {}


def page_title(path):
    """<title> stránky; čte se znovu jen po změně souboru."""
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return ""
    if key not in _TITLES:
        with open(path, encoding="utf-8") as f:
            m = re.search(r"<title>(.*?)</title>", f.read(20000), re.S)
        _TITLES[key] = html.unescape(m.group(1)).strip() if m else ""
    return _TITLES[key]


def short_name(title, slug):
    """Název stránky do seznamu: <title> do první oddělovací čárky („Název | Web“ → „Název“)."""
    if slug == "index":
        return "Homepage"
    return re.split(r"\s+[|·—–]\s+|\s+-\s+", title or "")[0] or "/" + slug


def cz_date(iso):
    if not iso:
        return ""
    y, m, d = iso[:10].split("-")
    return "%d. %d. %s" % (int(d), int(m), y)


def cz_time(epoch):
    t = time.localtime(epoch)
    return "%d. %d. %d %d:%02d" % (t.tm_mday, t.tm_mon, t.tm_year, t.tm_hour, t.tm_min)


def hhmm(epoch):
    t = time.localtime(epoch)
    return "%d:%02d" % (t.tm_hour, t.tm_min)


class Web:
    """editor = apply.Editor, publisher = publish.Publisher, host = šasi a ikony hostitele.
    base = cesta ("" nebo "/web"), site = adresa webu (redirect chybějících assetů, výchozí z cms.toml),
    publish = jde tady publikovat (vývojový editor ne), publish_url = kde se publikuje, když tady ne,
    offline = hláška, když server neodpovídá (výchozí obecná v editor.js)."""

    def __init__(self, editor, publisher, host, base="", site=None, publish=True, publish_url=None,
                 static_dir=None, check_drift=True, offline=None):
        self.editor, self.publisher, self.host = editor, publisher, host
        self.base = base.rstrip("/")
        self.site = (editor.site.url if site is None else site).rstrip("/")
        self.publish = publish
        self.publish_url = publish_url
        self.offline = offline
        self.root = editor.root
        self.static = Path(static_dir) if static_dir else Path(__file__).resolve().parent / "static"
        self.messages = editor.journal.recover()
        self.drift = {"state": "running" if check_drift else "off", "problems": []}
        self.sync = None                  # stav syncu hostitele: {"blocked", "commits", "head", "checked"}

    def check_drift(self):
        """Odpovídá dist/ zdrojům? Volá hostitel na pozadí po startu."""
        try:
            problems = P.drift(self.editor)
        except Exception as e:                        # noqa: BLE001 — banner, ne pád serveru
            problems = ["Kontrola dist/ při startu spadla: %r" % e]
        self.drift = {"state": "done", "problems": problems}

    # ── cesty ──
    def owns(self, path):
        """Patří cesta (bez query) editoru?"""
        return not self.base or path == self.base or path.startswith(self.base + "/")

    def sub(self, path):
        return path[len(self.base):] or "/"

    def public(self, method, path):
        """Smí požadavek bez přihlášení? Jen soubory webu z dist/ (ne HTML) pro náhled v sandboxu."""
        if method != "GET" or not self.owns(path):
            return False
        rel = self.sub(path).lstrip("/")
        if not rel or rel.startswith(("api/", "_admin/")) or rel.endswith(".html"):
            return False
        f = self._dist_path(rel)
        return f is not None and f.suffix.lower() in TYPES and f.suffix.lower() != ".html"

    def _dist_path(self, rel):
        base = self.editor.dist.resolve()
        f = (base / rel).resolve()
        if base not in f.parents or f.name.startswith(".") or f.suffix == ".php" or not f.is_file():
            return None
        return f

    # ── odpovědi ──
    @staticmethod
    def _out(code, ct, body, headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        return code, dict({"Content-Type": ct}, **(headers or {})), body

    def _json(self, obj, code=200):
        return self._out(code, JSON, json.dumps(obj, ensure_ascii=False))

    def _error(self, msg, status, **extra):
        return self._json(dict({"error": msg}, **extra), status)

    # ── vstup ──
    def handle(self, method, path, query, raw=b"", user=None, ip=None):
        """path = celá cesta (s base), query = parse_qs, raw = tělo POST. Vrací (status, hlavičky, tělo)."""
        sub = self.sub(path)
        api = sub.startswith("/api/")
        with A.acting(user, ip):
            try:
                if method == "GET":
                    return self._get(sub, query)
                return self._post(sub[5:] if api else "", raw)
            except A.Fail as e:
                if api:
                    return self._error(e.msg, e.status, code=e.code, details=e.details)
                return self._out(e.status, TEXT, e.msg)
            except buildlock.Busy as e:
                return self._error(str(e), 409, code="busy")
            except (KeyError, TypeError, ValueError) as e:
                if method == "POST":
                    return self._error("Neplatný požadavek (%s)." % e, 400)
                if api:
                    return self._error("Chyba serveru:\n" + traceback.format_exc()[-1500:], 500)
                return self._out(500, TEXT, traceback.format_exc())
            except Exception:                         # noqa: BLE001
                if api:
                    return self._error("Chyba serveru:\n" + traceback.format_exc()[-1500:], 500)
                return self._out(500, TEXT, traceback.format_exc())

    @staticmethod
    def _admin(what):
        if A.actor()["role"] != "admin":
            raise A.Fail("%s smí jen role admin." % what, code="role", status=403)

    # ── GET ──
    def _get(self, sub, q):
        if sub == "/":
            return self._out(200, HTML, self.list_page())
        if sub.startswith("/api/"):
            return self._api_get(sub[5:], q)
        if sub == "/_admin/ui.css" and hasattr(self.host, "ui_css"):
            return self._out(200, TYPES[".css"], self.host.ui_css().encode("utf-8"))
        if sub == "/_admin/site.css" and self.editor.site.css_rel:
            f = self.root / self.editor.site.css_rel
            return self._out(200, TYPES[".css"], f.read_bytes()) if f.is_file() else self._out(404, TEXT, "Nenalezeno.")
        if sub.startswith("/_admin/"):
            base = self.static.resolve()
            f = (base / sub[8:]).resolve()
            if base not in f.parents or not f.is_file():
                return self._out(404, TEXT, "Nenalezeno.")
            return self._out(200, TYPES.get(f.suffix.lower(), "application/octet-stream"), f.read_bytes())
        slug = sub.strip("/")
        slug = slug[:-5] if slug.endswith(".html") else slug
        if slug and "/" not in slug and slug in self.editor.registry():
            return self._page(slug, q)
        f = self._dist_path(sub.lstrip("/"))
        if f is not None:
            return self._out(200, TYPES.get(f.suffix.lower(), "application/octet-stream"), f.read_bytes())
        if sub.startswith("/assets/") and self.site:
            # .png a část médií je jen na serveru (P01), editor si je vezme z webu
            return self._out(302, TEXT, "", {"Location": self.site + sub})
        return self._out(404, TEXT, "Nenalezeno.")

    def _page(self, slug, q):
        ed = self.editor
        slugs = ed.registry()
        if "src" in q:
            self._admin("Celý zdroj stránky")
            return self._out(200, HTML, self.source_page(slug))
        src = ed.read_page(slug)
        if "e" in q:
            kind, lock = ed.kind(slug), ed.lock_reason(slug)
            prov = ed.provenance(slug) if ed.mapped(slug, src) and not lock else None
            nonce = secrets.token_urlsafe(16)
            until = ed.unlocked_until(slug) if kind == "owner" else None
            role = A.actor()["role"]
            # sdílené soubory stránek (menu, patička) a kolik stránek nesou: rozsah sdílené změny v editoru
            parts = {rel: len(ed.site.partial_pages(rel)) for rel in ed.site.partial_files()}
            metas = {"ke-role": role, "ke-until": hhmm(until) if until else "",
                     "ke-publish": "1" if self.publish else "0", "ke-path": ed.page_rel(slug),
                     "ke-title-suffix": AD.RULES.title_suffix, "ke-parts": json.dumps(parts),
                     "ke-menu": json.dumps(AD.RULES.menu)}
            if self.offline:
                metas["ke-offline"] = self.offline
            page = V.edit_view(src, slug, kind, lock, prov, ed.script_rel(slug) if kind == "generated" else ed.page_rel(slug),
                               base=self.base, nonce=nonce, metas=metas, slugs=slugs,
                               site_css=bool(ed.site.css_rel), part_lock=None if role == "admin" else A.LOCK_PART_ROLE,
                               sprite=self.host.sprite() if hasattr(self.host, "sprite") else "",
                               ui_css=hasattr(self.host, "ui_css"))
            return self._out(200, HTML, page, {"Content-Security-Policy": EDIT_CSP % nonce})
        return self._out(200, HTML, V.preview(src, self.base, slugs, ui_css=hasattr(self.host, "ui_css")),
                         {"Content-Security-Policy": SANDBOX})

    def _api_get(self, name, q):
        ed = self.editor
        if name == "pages":
            return self._json({"pages": self.pages()})
        if name == "status":
            return self._json(self.status())
        if name == "journal":
            return self._json({"entries": self.journal()})
        if name == "changes":
            return self._json(self.changes())
        if name == "source":
            self._admin("Celý zdroj stránky")
            slug = q.get("slug", [""])[0]
            ed.kind(slug)
            src = ed.read_page(slug)
            return self._json({"slug": slug, "content": src, "base": B.page_hash(src), "lock": ed.source_lock(slug)})
        if name == "seo":
            return self._json(ed.seo_panel(q.get("slug", [""])[0]))
        if name == "find":
            return self._json(R.find(ed, q.get("q", [""])[0]))
        if name == "publish/preview":
            return self._json(self.preview())
        if name == "publish/status":
            return self._json(self.publisher.status())
        return self._error("Neznámé API.", 404)

    # ── POST ──
    def _post(self, name, raw):
        ed = self.editor
        try:
            d = json.loads(raw or b"{}")
            if not isinstance(d, dict):
                raise ValueError
        except ValueError:
            return self._error("Neplatný požadavek.", 400)
        if name in WRITES and self.publisher.running():
            return self._error("Právě běží publikace. Počkej, až doběhne.", 409, code="busy")
        if name == "save":
            r = ed.save_block(d["slug"], int(d["n"]), d["h"], d["page"], d["html"], d.get("confirm"), d.get("owners") is True)
            if r.get("ok") and not r.get("unchanged"):
                ed.audit("save", slug=d["slug"], entry=r.get("entry"))
        elif name == "attrs":
            changes = d["changes"]
            if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
                raise ValueError("changes")
            r = ed.save_attrs(str(d["slug"]), changes, d.get("confirm"), d.get("owners") is True)
            if r.get("ok") and not r.get("unchanged"):
                ed.audit("attrs", slug=d["slug"], entry=r.get("entry"), pages=r.get("pages"))
        elif name == "undo":
            e = ed.undo(d["id"])
            ed.audit("undo", slug=e.get("slug"), entry=e["id"])
            r = {"ok": True, "slug": e.get("slug")}
        elif name == "discard":
            r = P.discard(ed, str(d["slug"]), d.get("confirm"))
            if r.get("ok"):
                ed.audit("discard", slug=d["slug"], entry=r.get("entry"), pages=r.get("pages"))
        elif name == "unlock":
            self._admin("Odemknout owner stránku")
            if ed.kind(d["slug"]) != "owner":
                return self._error("Odemknout jde jen owner stránku.", 400)
            until = ed.unlock(d["slug"])
            ed.audit("unlock", slug=d["slug"])
            r = {"ok": True, "until": hhmm(until)}
        elif name == "lock":
            ed.relock(str(d["slug"]))
            ed.audit("lock", slug=d["slug"])
            r = {"ok": True}
        elif name == "source":
            self._admin("Celý zdroj stránky")
            r = ed.save_source(d["slug"], d["content"], d["base"])
            if r.get("ok") and not r.get("unchanged"):
                ed.audit("source", slug=d["slug"], entry=r.get("entry"))
        elif name == "seo":
            changes = d["changes"]
            if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
                raise ValueError("changes")
            r = ed.save_seo(d["slug"], changes, d.get("confirm"), bool(d.get("owners")))
            if r.get("ok") and not r.get("unchanged"):
                ed.audit("seo", slug=d["slug"], entry=r.get("entry"), part=r.get("part"))
        elif name == "replace":
            files = d["files"]
            if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
                raise ValueError("files")
            r = R.replace(ed, str(d["q"]), str(d.get("r") or ""), files, str(d["sig"]), d.get("confirm"),
                          d.get("owners") is True)
            if r.get("ok"):
                ed.audit("replace", entry=r.get("entry"), pages=r.get("pages"))
        elif name in ("publish", "deploy"):
            if not self.publish:
                return self._error("Odsud se nepublikuje. Publikuj z %s." % (self.publish_url or "adminu na serveru"),
                                   403, code="publish_off")
            if name == "publish":
                self.publisher.start(str(d.get("message") or ""), author=A.author(A.actor()))
            else:
                self.publisher.start_redeploy()
            ed.audit(name)
            r = {"ok": True}
        else:
            return self._error("Neznámé API.", 404)
        if name in WRITES:
            ed.warm()                            # sonda proveniance pro další stránku na pozadí
        r["count"] = len(ed.journal.unpublished())
        return self._json(r)

    # ── data ──
    def pages(self):
        ed = self.editor
        reg = ed.registry()
        dates = git_dates(self.root, ed.site.source)
        pending = collections.Counter(s for e in ed.journal.unpublished()
                                      for s in ((e.get("pages") or []) if e.get("kind") == "replace" else [e.get("slug")]))
        out = []
        for s, v in sorted(reg.items()):
            title = page_title(self.root / ed.page_rel(s))
            out.append({"slug": s, "kind": v["kind"], "title": title, "name": short_name(title, s),
                        "date": dates.get(ed.page_rel(s)), "pending": pending.get(s, 0)})
        return out

    def journal(self):
        out = []
        for e in reversed(self.editor.journal.unpublished()):
            out.append({k: e.get(k) for k in ("id", "slug", "label", "before", "after", "t", "block", "user")}
                       | {"files": [f["path"] for f in e["files"]]})
        return out

    def changes(self):
        """Panel Změny (publish.changes) a k úpravám jméno stránky a kdo je udělal."""
        ch = P.changes(self.editor)
        users = {e["id"]: e.get("user") for e in self.editor.journal.unpublished()}
        for p in ch["pages"]:
            p["name"] = short_name(page_title(self.root / self.editor.page_rel(p["slug"])), p["slug"])
            for it in p["items"]:
                it["user"] = users.get(it.get("undo"))
            for e in p["edits"]:
                e["user"] = users.get(e["id"])
        for x in ch["replaces"]:
            x["user"] = users.get(x["id"])
        return ch

    def preview(self):
        """Dialog Publikovat: náhled publikace (publish.preview), jména stránek, autor commitu, kroky a adresa webu."""
        p = self.publisher.preview()
        for x in p.get("pages", []):
            x["name"] = short_name(page_title(self.root / self.editor.page_rel(x["slug"])), x["slug"])
        p["author"] = A.author(A.actor())
        p["publish"] = self.publish
        p["site"] = self.publisher.url
        p["steps"] = [{"key": k, "label": label, "state": "pending", "detail": "", "t": None} for k, label in P.STEPS]
        return p

    def status(self):
        ed = self.editor
        last = ed.journal.last_published()
        if last:
            last = dict(last, when=cz_time(last["t"]))
        return {"messages": self.messages, "drift": self.drift, "count": len(ed.journal.unpublished()),
                "publishing": self.publisher.running(), "unlocked": {s: hhmm(ed.unlocked_until(s)) for s in ed.unlocked},
                "role": A.actor()["role"], "publish": self.publish, "publish_url": self.publish_url,
                "sync": self.sync, "redeploy": self.publisher.redeploy_info(), "last": last}

    # ── stránky ──
    def list_page(self):
        """Seznam stránek (prototyp A4, varianta B): hlavička s Publikovat, hlášky (plní editor.js),
        karta Nepublikované úpravy (editor.js z /api/changes) a tabulka stránek."""
        esc, ic = html.escape, self.host.ic
        pages = self.pages()
        total = sum(p["pending"] for p in pages)
        rows = []
        for p in pages:
            s = p["slug"]
            go = "%s/%s?e=1" % (self.base, s)
            kind = KINDS.get(p["kind"], p["kind"])
            kind_html = ('<span class="w-kind">%s%s</span>' % (ic("lock"), kind) if p["kind"] == "owner"
                         else '<span class="w-kind">%s</span>' % kind)
            pend = ('<span class="w-pendcell">%s</span>' % P.plural(p["pending"], "úprava", "úpravy", "úprav")
                    if p["pending"] else "")
            src = ('<a class="k-opt" role="menuitem" href="%s/%s?src=1">%sZdroj stránky</a>' % (self.base, s, ic("file-text"))
                   if p["kind"] != "generated" and A.actor()["role"] == "admin" else "")
            menu = ('<details class="k-menu"><summary class="k-ibtn" aria-label="Další akce se stránkou /%s">%s</summary>'
                    '<div class="k-pop" role="menu"><a class="k-opt" role="menuitem" href="%s/%s">%sNáhled</a>%s'
                    '<a class="k-opt" role="menuitem" href="%s/%s" target="_blank" rel="noopener">%sOtevřít na webu</a>'
                    '</div></details>' % (esc(s), ic("ellipsis"), self.base, s, ic("eye"), src,
                                          esc(self.site), "" if s == "index" else esc(s), ic("external-link")))
            rows.append('<tr data-kind="%s" data-q="%s"%s><td class="is-wrap"><div class="k-entity"><div>'
                        '<a class="w-open" href="%s">%s</a><div class="k-entity__meta"><span class="k-mono">/%s</span></div>'
                        '</div></div></td><td class="w-col-opt">%s</td><td class="w-col-opt">%s</td><td>%s</td>'
                        '<td><div class="k-table__actions"><a class="k-btn k-btn--secondary k-btn--sm" href="%s">%sUpravit</a>%s'
                        '</div></td></tr>'
                        % (p["kind"], esc(("%s %s %s" % (s, p["name"], p["title"])).lower()),
                           ' class="w-row--pend"' if p["pending"] else "", go, esc(p["name"]),
                           esc("" if s == "index" else s), kind_html, cz_date(p["date"]), pend, go, ic("pencil"), menu))
        counts = collections.Counter(p["kind"] for p in pages)
        seg = "".join('<button class="k-seg__opt" type="button" role="radio" aria-checked="%s" data-kind="%s">%s '
                      '<span class="k-seg__count">%d</span></button>'
                      % ("true" if not k else "false", k, label, len(pages) if not k else counts.get(k, 0))
                      for k, label in (("", "Vše"), ("handbuilt", "Ruční"), ("generated", "Generované"), ("owner", "Owner")))
        last = self.editor.journal.last_published()
        meta = ['<span><b>%s</b></span>' % P.plural(len(pages), "stránka", "stránky", "stránek")]
        if self.site:
            meta.append('<span>%s</span>' % esc(self.site.split("//", 1)[-1]))
        if not self.publish:
            meta.append('<span>vývojový editor, publikuje se z %s</span>'
                        % esc((self.publish_url or "adminu na serveru").split("//", 1)[-1]))
        elif last:
            meta.append('<span>publikováno naposledy %s%s</span>'
                        % (cz_time(last["t"]), ", " + esc(last["by"]) if last.get("by") else ""))
        pub = ('<button class="k-btn k-btn--primary" type="button" id="kePublish"%s>%sPublikovat'
               '<span class="k-btn__count" id="keCount"%s>%d</span></button>'
               % ("" if self.publish and total else " disabled", ic("send"), "" if total else " hidden", total))
        body = ('<div class="k-page-head"><div class="k-page-head__text"><h1 class="k-h1">Web</h1>'
                '<div class="k-meta w-meta">%s</div></div>'
                '<div class="k-page-head__actions"><button class="k-btn k-btn--secondary" type="button" id="keFind">%s'
                'Najít a nahradit</button>%s</div></div>\n'
                '<div class="w-notices" id="wNotices"></div>\n'
                '<div class="k-stack">\n<section class="k-card w-pend" id="wPend" aria-labelledby="h-pend"%s>'
                '<div class="k-card__head k-card__head--bd"><h2 class="k-card__title" id="h-pend">Nepublikované úpravy</h2>'
                '<span class="k-card__meta" id="wPendMeta"></span></div><div id="wPendBody"></div>'
                '<div class="k-card__foot"><span class="k-card__foot-label">Vrátit jde vždy nejnovější úpravu souboru. '
                'Zahodit vrátí stránku do stavu poslední publikace.</span></div></section>\n'
                '<section class="k-card" aria-labelledby="h-pages"><div class="k-card__head k-card__head--bd">'
                '<h2 class="k-card__title" id="h-pages">Stránky</h2><span class="k-card__meta">klik na název otevře úpravy textů</span></div>'
                '<div class="k-fbar k-fbar--card w-fbar"><label class="k-search">%s<input id="keSearch" type="search" '
                'placeholder="Hledat podle adresy nebo názvu" aria-label="Hledat stránku" autocomplete="off"></label>'
                '<div class="k-seg" role="radiogroup" aria-label="Druh stránky">%s</div>'
                '<label class="w-only"><input class="k-check" type="checkbox" id="keOnly"%s>Jen s úpravami</label></div>'
                '<div class="k-card__table"><table class="k-table w-list"><thead><tr><th>Stránka</th>'
                '<th class="w-col-opt">Druh</th><th class="w-col-opt">Naposledy v gitu</th><th>Nepublikováno</th>'
                '<th class="is-num"><span class="k-sr">Akce</span></th></tr></thead>\n<tbody>\n%s\n</tbody></table></div>'
                '<div class="k-state k-state--row" id="keNone" hidden><p class="k-state__desc">Nic nenalezeno.</p></div>'
                '</section>\n</div>'
                % ("".join(x if i == 0 else '<span class="k-meta__dv"></span>' + x for i, x in enumerate(meta)),
                   ic("search"), pub, "" if total else " hidden", ic("search"), seg, "" if total else " disabled",
                   "\n".join(rows)))
        metas = {"ke-mode": "list", "ke-base": self.base, "ke-role": A.actor()["role"],
                 "ke-publish": "1" if self.publish else "0", "ke-publish-url": self.publish_url or ""}
        return self.host.page("Web · editor textů", body, [("Web", None)], metas)

    def source_page(self, slug):
        ed = self.editor
        src = ed.read_page(slug)
        lock = ed.source_lock(slug) or ""
        esc = html.escape
        body = ('<div class="k-page-head"><div class="k-page-head__text"><h1 class="k-h1">Zdroj /%s</h1>'
                '<p class="k-lead">%s. Uložení projde stejnou kontrolou jako úprava textu (build stránky a kontrola) '
                'a objeví se v nepublikovaných úpravách.</p></div>'
                '<div class="k-page-head__actions"><a class="k-btn k-btn--secondary" href="%s/%s?e=1">Zpět do úprav</a>'
                '<button class="k-btn k-btn--primary" type="button" id="keSrcSave"%s>Uložit <span class="k-kbd">Ctrl+S</span>'
                '</button></div></div>\n'
                '%s<textarea id="keSrc" class="k-input w-src" spellcheck="false"%s>\n%s</textarea>\n'
                '<div id="keSrcOut" class="k-notice k-notice--err w-tone--err w-srcout" hidden></div>'
                % (esc(slug), esc(ed.page_rel(slug)), self.base, esc(slug), " disabled" if lock else "",
                   '<div class="k-notice w-srclock">%s<span>%s</span></div>' % (self.host.ic("lock"), esc(lock)) if lock else "",
                   " readonly" if lock else "", esc(src)))
        return self.host.page("Zdroj /%s · editor textů" % slug, body,
                              [("Web", self.base + "/"), ("Zdroj /%s" % slug, None)],
                              {"ke-mode": "source", "ke-slug": slug, "ke-kind": ed.kind(slug), "ke-base": self.base,
                               "ke-page": B.page_hash(src), "ke-lock": lock, "ke-role": A.actor()["role"]})
