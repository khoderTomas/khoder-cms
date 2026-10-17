# -*- coding: utf-8 -*-
"""Adaptér Khoder CMS pro ukázkový web (cms.toml [editor] adapter, rozhraní cms/adapter.py).
Načítá ho motor, který už má v procesu svůj modul adapter.

Rules  údaje z content/site.json v textu (data-site) jako pevné prvky, které počítá build,
       hlavička a patička z content/partials/ (partial_parts: bloky v nich editor upravuje
       v šabloně), panel SEO (texty JSON-LD, které admin mění, a údaje o firmě jako entita),
       Najít a nahradit vynechá blok entity a údaje data-site, menu webu pro editační pohled,
       typografie a og:title bez názvu webu.
Site   build stránky v paměti a kontrola (build/web.py, stejné kroky jako build/build.py),
       sdílené části a stránky, které je nesou, overlay pro zkušební sestavení s neuloženou
       šablonou nebo content/site.json a stránky, kterým změna údajů o firmě změní výstup.
"""
import contextlib
import re
import sys
import threading
from pathlib import Path

import adapter as CA

sys.path.insert(0, str(Path(__file__).resolve().parent))
import web as W  # noqa: E402

WHY_ATOM = "Údaj z content/site.json: build ho vkládá na všechny stránky, mění ho Claude Code v datech webu."
TITLE_SUFFIX = r"\s+\|\s+Truhlářství Habr\s*$"

LD_ALLOW = {
    ("WebSite", "name"): "Název webu",
    ("WebPage", "name"): "Název stránky",
    ("WebPage", "description"): "Popis stránky",
    ("AboutPage", "name"): "Název stránky",
    ("AboutPage", "description"): "Popis stránky",
    ("ContactPage", "name"): "Název stránky",
    ("ContactPage", "description"): "Popis stránky",
    ("Service", "name"): "Název služby",
    ("Service", "description"): "Popis služby",
}
LD_TYPES = {"WebSite": "Web", "WebPage": "Stránka", "AboutPage": "Stránka O nás", "ContactPage": "Kontakt",
            "Service": "Služba"}


class Rules(CA.Rules):
    atom_attrs = frozenset({"data-site"})
    computed = (("data-site", WHY_ATOM, None),)
    title_suffix = TITLE_SUFFIX
    menu = {"toggle": ".nav-toggle", "nav": ".site-nav", "open": "is-open", "narrow": 760,
            "frame": {"header": ".site-header__inner", "footer": ".site-footer__inner"}}
    ld_allow = LD_ALLOW
    ld_types = LD_TYPES
    entity = {"file": W.SITE_FILE, "key": W.ENTITY_KEY, "type": W.ENTITY_TYPE,
              "text": [("name", "Název firmy"), ("description", "Popis firmy"),
                       ("address.streetAddress", "Ulice a číslo"), ("address.addressLocality", "Město")],
              "contacts": [("telephone", "Telefon"), ("email", "E-mail"), ("url", "Web"),
                           ("address.postalCode", "PSČ"), ("openingHours", "Otevírací doba")],
              "topics": "knowsAbout"}

    def locked_spans(self, src):
        return W.entity_spans(src) + W.atom_spans(src)

    def partial_parts(self, src):
        try:
            return [(a, z, W.partial_rel(n)) for a, z, n in W.slots(src)]
        except W.BuildError:
            return []                             # rozbité značky ohlásí kontrola stránky

    def og_title(self, title):
        return re.sub(TITLE_SUFFIX, "", title)

    def typo(self, html):
        return W.typo_html(html)


class Site(CA.Site):
    rules = Rules()

    def __init__(self, root, config=None):
        super().__init__(root, config)
        self._local = threading.local()           # overlay platí jen ve vlákně požadavku

    def data(self):
        return W.Data(self.root, getattr(self._local, "files", None))

    # ── build stránky v paměti ──
    def inject(self, html, slug, sources=None):
        try:
            return W.render(slug, html, self.data())
        except W.BuildError as e:
            raise CA.SiteError(str(e)) from None

    def check(self, slug, html):
        return W.check(slug, html, self.data())

    # ── hlavička a patička ──
    def partial_files(self):
        return [W.partial_rel(n) for n in W.PARTIALS]

    def partial_pages(self, rel):
        if rel not in self.partial_files():
            return []
        return sorted(s for s, v in self.registry().items() if v["kind"] in W.KINDS)

    @contextlib.contextmanager
    def overlay(self, files):
        allowed = set(self.partial_files()) | {W.SITE_FILE}
        bad = sorted(set(files or {}) - allowed)
        if bad:
            raise ValueError("overlay: %s není šablona ani %s" % (", ".join(bad), W.SITE_FILE))
        prev = getattr(self._local, "files", None)
        self._local.files = dict(prev or {}, **(files or {}))
        try:
            yield
        finally:
            self._local.files = prev

    # ── údaje o firmě (content/site.json) ──
    def entity_text(self, data):
        return W.entity_text(data)

    def entity_reach(self, old, new):
        root = self.root
        before = W.Data(root, {W.SITE_FILE: W.entity_text(old)})
        after = W.Data(root, {W.SITE_FILE: W.entity_text(new)})
        out = []
        for slug in sorted(self.registry()):
            try:
                src = before.page(slug)
                if W.render(slug, src, before) != W.render(slug, src, after):
                    out.append(slug)
            except (FileNotFoundError, W.BuildError):
                continue
        return out
