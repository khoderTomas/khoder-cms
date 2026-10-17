# -*- coding: utf-8 -*-
"""Adaptér webu: co motor editoru o konkrétním webu neví sám.

Motor zná tři druhy stránek z manifestu stránek ([deploy] pages v cms.toml): ruční (zdrojem je
soubor stránky), owner (ruční, zamčená do odemčení adminem) a generované (zdrojem jsou textové
literály skriptů generátoru, pole source). Všechno ostatní dodá web modulem adaptéru, který
cms.toml uvádí v [editor] adapter: třída Site (potomek Site níž) s pravidly Rules.

Rules   pravidla stránky: oblasti, které plní build (zamčené s důvodem), prvky zamčené podle id
        nebo třídy, pevné prvky podle atributu, texty, které build počítá, citace, typografie.
        V procesu jsou jedny (jeden web): load() je nainstaluje a blocks, classify, replace a seo
        je čtou z RULES.
Site    operace nad repem webu: manifest stránek, build stránky v paměti (injektory a kontrola),
        stránky, které přebírají obsah jiné stránky, sdílené části stránek (partials: hlavička,
        patička) ve vlastních souborech, zkušební build generátoru do tempu a příkazy publikace.
        Výchozí Site je web z ručního HTML bez buildu, bez partials a bez generátoru.

cms.toml:
  [deploy]  source (složka stránek), site (adresa webu), pages (manifest stránek)
  [editor]  adapter (modul adaptéru), journal (journal úprav, výchozí .cms/journal), css (styly
            editačního pohledu pro prvky webu, servíruje se jako <base>/_admin/site.css)
  [publish] build a sitemap (Python skripty s argumenty, spouští se ve worktree publikace),
            sitemap_file (cesta sitemapy v repu), deploy_ssh (cíl ssh deploye, přebíjí CMS_DEPLOY_SSH)
"""
import contextlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

CONFIG = "cms.toml"
# Buildy editoru s nižší prioritou: na serveru sdílí procesor s ostatními službami hostitele.
NICE = ["nice", "-n", "10"] if os.name == "posix" and shutil.which("nice") else []


class SiteError(Exception):
    """Build stránky v paměti selhal (injektor odmítl obsah). Motor ho ukáže jako chybu kontroly."""


class Rules:
    """Výchozí pravidla: web bez oblastí buildu, zámků a typografie."""
    lock_ids = {}                 # id prvku → důvod zámku (prvek i jeho obsah)
    lock_classes = {}             # třída prvku → důvod zámku
    atom_attrs = frozenset()      # prvek s takovým atributem je pevný (hodnota z dat, nejde přepsat)
    computed = ()                 # (atribut, důvod, zámek oblasti nebo None): text prvku počítá build
    quote_attrs = ()              # atributy s citacemi (v adminu se nemění)
    quote_types = frozenset()     # @type v JSON-LD, jehož texty jsou citace
    quote_detail = "quote"        # podkategorie citací v JSON-LD (report pokrytí)
    quote_why = "Citace, v adminu se nemění."
    labels = {}                   # podkategorie zdroje textu → popisek v reportu pokrytí
    script_detail = "script"      # podkategorie textu ze skriptu generované stránky
    title_suffix = ""             # regulární výraz konce title s názvem webu (JS i Python)
    # Menu webu v editačním pohledu (skripty webu tam neběží, editor.js je otevírá sám), selektory CSS:
    # mega = spouštěč rozbalovacího menu (aria-expanded), toggle a nav = tlačítko a navigace na telefonu
    # (nav dostane třídu open), narrow = do jaké šířky okna má web navigaci za tlačítkem, frame =
    # {header|footer: prvek, podle kterého se kreslí rám sdílené části}
    menu = {}
    # Panel SEO: texty JSON-LD, které jde v adminu měnit ((@type, klíč) → popisek), popisky skupin
    # podle @type a sdílený objekt JSON-LD v datech webu (autor webu): {file: cesta v repu, key: klíč
    # objektu v souboru, type: @type, text: [(klíč s tečkami, popisek)] textů, contacts: [(klíč,
    # popisek)] jen ke čtení, topics: klíč seznamu témat, který mívá stránka vlastní}. Soubor má tvar
    # {<key>: objekt, pages: {slug: {klíč: hodnota}}} a stránky z něj sestavuje build webu.
    ld_allow = {}
    ld_types = {}
    entity = None

    def ld_label(self, ty, key, ctx):
        """Popisek textu JSON-LD v panelu SEO (ctx = klíče předků), None = panel ho nenabízí."""
        return self.ld_allow.get((ty, key))

    def ld_fixed(self, ty, key, ctx, value, generated):
        """(stav, důvod, popisek nebo None) textu JSON-LD, který plní build ("computed") nebo
        který panel nenabízí ("locked"), jinak None."""
        return None

    def robots_why(self, generated):
        """Proč je robots v panelu SEO jen ke čtení."""
        return "Jen ke čtení: indexování mění Claude Code ve zdroji stránky."

    def regions(self, src):
        """[(začátek, konec, důvod zámku)] oblastí, které plní build, v pořadí přednosti."""
        return []

    def faq_from(self, src):
        """[(začátek, konec, slug)] FAQ, které build převzal z jiné stránky."""
        return []

    def locked_spans(self, src):
        """Další rozsahy, ve kterých Najít a nahradit nehledá (citace v JSON-LD…)."""
        return []

    def partial_spans(self, src):
        """[(začátek, konec)] úseků stránky, které build vkládá ze sdíleného souboru (Site.partial_files).
        Najít a nahradit je ve stránce přeskočí, hledá je v tom souboru."""
        return [(a, z) for a, z, _ in self.partial_parts(src)]

    def partial_parts(self, src):
        """[(začátek, konec, soubor v repu)] týchž úseků se sdíleným souborem, ze kterého jsou. Bloky
        v nich editor upravuje v tom souboru (sdílená změna, apply.save_part), hlavička a patička
        stránky v nich nejsou zamčené."""
        return []

    def region_source(self, why):
        """(podkategorie, [soubory šablon v repu]) oblasti podle důvodu zámku, nebo None."""
        return None

    def keep_closed(self, attrs):
        """Má <details> s těmito atributy zůstat v editačním pohledu zavřené?"""
        return False

    def og_title(self, title):
        """og:title a twitter:title, jak je generátor odvozuje z title."""
        return title

    def typo(self, html):
        """Typografie textu, kterého se úprava dotkla (pevné mezery…)."""
        return html

    def adds_typo(self, src):
        """Doplňuje skript generátoru typografii sám (pak se pevné mezery do literálu nepíšou)?"""
        return False


RULES = Rules()


def install(rules):
    """Pravidla webu pro celý proces (blocks, classify, replace, seo, htmltext)."""
    global RULES
    RULES = rules


def read_config(root):
    try:
        with open(Path(root) / CONFIG, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


class Site:
    rules = Rules()

    def __init__(self, root, config=None):
        self.root = Path(root)
        self.config = read_config(root) if config is None else config
        dep = self.config.get("deploy") or {}
        ed = self.config.get("editor") or {}
        pub = self.config.get("publish") or {}
        self.source = dep.get("source", "dist")
        self.url = (dep.get("site") or "").rstrip("/")
        self.pages_rel = dep.get("pages", "pages.json")
        self.journal_rel = ed.get("journal", ".cms/journal")
        self.css_rel = ed.get("css")
        self.build_cmd = list(pub.get("build") or [])
        self.sitemap_cmd = list(pub.get("sitemap") or [])
        self.sitemap_rel = pub.get("sitemap_file") or "%s/sitemap.xml" % self.source
        self.deploy_ssh = pub.get("deploy_ssh")

    # ── stránky ──
    @property
    def dist(self):
        return self.root / self.source

    def registry(self, root=None):
        """{slug: {kind, source, output}} z manifestu stránek (root = jiný strom, třeba worktree)."""
        return json.loads((Path(root or self.root) / self.pages_rel).read_text(encoding="utf-8"))

    def page_rel(self, slug):
        return "%s/%s.html" % (self.source, slug)

    def page_slug(self, rel):
        """Slug stránky z cesty v repu, nebo None."""
        m = re.fullmatch(re.escape(self.source) + r"/([^/]+)\.html", rel)
        return m.group(1) if m else None

    # ── build stránky v paměti ──
    def inject(self, html, slug, sources=None):
        """Stránka po injektorech buildu. sources = {slug: obsah} stránek, které ještě nejsou na
        disku (převzaté FAQ). Odmítnutí = SiteError."""
        return html

    def check(self, slug, html):
        """[problém] kontroly stránky, kterou build pouští nad každou stránkou."""
        return []

    def followers(self, slug, dist=None):
        """Stránky, které při buildu přebírají obsah stránky slug (dist = jiná složka stránek)."""
        return []

    # ── sdílené části stránek (partials) ──
    def partial_files(self):
        """Soubory s HTML, které build vkládá do stránek (hlavička, patička; cesty v repu). Najít
        a nahradit v nich hledá a nahrazuje, změna se propíše do stránek z partial_pages."""
        return []

    def partial_pages(self, rel):
        """Stránky, jejichž výstup se mění se souborem rel z partial_files (i generované)."""
        return []

    @contextlib.contextmanager
    def overlay(self, files):
        """Po dobu bloku čtou inject a check stránek soubory z partial_files z files {cesta v repu:
        text} místo z repa (zkušební sestavení stránek s neuloženou změnou). Generátor dostane
        tytéž soubory v overrides generate()."""
        yield

    # ── sdílený objekt JSON-LD (Rules.entity) ──
    def entity_data(self, text=None):
        """Data souboru Rules.entity (text = obsah souboru, jinak z repa), nebo None."""
        ent = self.rules.entity
        if not ent:
            return None
        if text is None:
            text = (self.root / ent["file"]).read_text(encoding="utf-8")
        return json.loads(text)

    def entity_text(self, data):
        """Text souboru Rules.entity, jak ho editor zapíše."""
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def entity_block(self, src):
        """Objekt entity na stránce (blok JSON-LD nejvyšší úrovně s @type Rules.entity), nebo None."""
        ent = self.rules.entity
        for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', src, re.S):
            try:
                o = json.loads(m.group(1))
            except ValueError:
                continue
            if ent and isinstance(o, dict) and o.get("@type") == ent["type"]:
                return o
        return None

    def entity_reach(self, old, new):
        """Stránky, kterým se se změnou dat entity old → new změní výstup buildu."""
        return []

    # ── generátor ──
    def shared_files(self):
        """Soubory generátoru, jejichž změna mění všechny generované stránky."""
        return []

    def scripts(self):
        """Skripty generátoru (cesty v repu), každý píše své stránky."""
        return []

    def generator_files(self):
        """Všechny zdroje generátoru s texty (Najít a nahradit), sdílené první."""
        return self.shared_files() + self.scripts()

    def page_sources(self, slug):
        """Zdroje generované stránky: její skript (source v manifestu) a sdílené soubory."""
        return [self.registry()[slug]["source"]] + self.shared_files()

    def scripts_for(self, changed, also=()):
        """Skripty k puštění po změně souborů changed (a navíc also); None = všechny."""
        if set(changed) & set(self.shared_files()):
            return None
        return sorted((set(changed) & set(self.scripts())) | set(also))

    def generate(self, overrides=None, scripts=None):
        """Zkušební build generátoru do tempu se zdroji overrides {cesta v repu: text}.
        scripts = skripty k puštění, None = všechny. Repo se nemění.
        Vrací (ok, chyba, {slug: html})."""
        if overrides or (self.scripts() if scripts is None else scripts):
            raise SiteError("Web nemá generátor stránek.")
        return True, "", {}

    # ── publikace ──
    def clean_env(self, env):
        """Prostředí pro build a sitemap ve worktree publikace (bez proměnných zkušebního buildu)."""
        return env

    def build_hint(self):
        """Příkaz buildu pro hlášky („spusť …“)."""
        return " ".join(["python"] + self.build_cmd) if self.build_cmd else "build webu"


def load(root, config=None):
    """Site webu v repu root podle cms.toml a instalace jeho pravidel pro proces.
    Modul adaptéru se v procesu načte jednou (podle jména souboru): testy nad kopií repa tak
    běží s kódem adaptéru z repa, ve kterém běží motor."""
    config = read_config(root) if config is None else config
    rel = (config.get("editor") or {}).get("adapter")
    site = _module(Path(root) / rel).Site(root, config) if rel else Site(root, config)
    install(site.rules)
    return site


def _module(path):
    name = path.stem
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return mod
