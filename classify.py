# -*- coding: utf-8 -*-
"""Jak jde který text stránky upravit. Jedno místo pro editor (zámky bloků v editačním pohledu,
view.edit_view) i pro report pokrytí (cms/coverage.py), aby nemohly mluvit jinak.

blocks(src, lock, prov)   bloky stránky se zámkem: celá stránka (owner bez odemčení), oblast
                          (blocks.py) a u generované stránky proveniance (text ve skriptu nenalezen,
                          sdílený s injektorem). Editovatelné bloky generované stránky dostanou
                          atributy sdílení a souboru pro editační pohled.
classify(page)            každý text stránky jako Item:
    kind    text (viditelný text v <body>), svg (text v SVG), attr (alt, title, aria-label,
            placeholder a data-* s textem), head (title a meta), jsonld (řetězce JSON-LD),
            data (texty v JSON pro skripty webu), js (řetězce v inline skriptech)
    source  kde text žije: html (ruční a owner stránky), python (generátor a šablony injektorů),
            json (data webu), chrome (hlavička a patička webu), js; detail upřesní podkategorii
    state   klik (blok v editoru), sdileny (blok nebo text v atributu, úprava se propíše i na další
            stránky), panel (panel SEO, panel Další texty), pocitany (počítá nebo odvozuje build),
            zamceny (důvod ve why)
attr_nodes(src, kind, …)  prvky s texty mimo bloky (alt, title, aria-label, placeholder a text v SVG)
                          se stavem a zámkem: panel Další texty a popover u prvku v editoru
                          (attr_marks pro editační pohled), uložení (apply.save_attrs) i report.
js_texts(src)             textové řetězce skriptu (soubory .js webu počítá cms/coverage.py zvlášť).

Text = úsek mezi tagy jako u HTMLParser.handle_data: entity dekódované, NBSP jako mezera, bez
word joinerů, bílé znaky sloučené. Názvy ikon (Material Symbols) text nejsou.

Oblasti, které plní build, jejich zdroje, počítané texty a citace dodává adaptér webu (adapter.RULES).
"""
import html
import json
import re
from pathlib import Path

import adapter as AD
import blocks as B
import htmltext as H
import provenance as PV
import pysource as P
import seo as S

KLIK, SDILENY, PANEL, POCITANY, ZAMCENY = "klik", "sdileny", "panel", "pocitany", "zamceny"
STATES = (KLIK, SDILENY, PANEL, POCITANY, ZAMCENY)

# Najít a nahradit (replace.py) prohledává tyhle atributy a meta, v ručních stránkách i JSON-LD
TEXT_ATTRS = ("alt", "title", "aria-label", "placeholder")
REPLACE_META = ("description", "keywords", "og:title", "og:description", "twitter:title", "twitter:description")
HEAD_SKIP = {"viewport", "color-scheme", "robots", "theme-color", "referrer", "format-detection", "generator"}
LD_SKIP = S.LD_SKIP

WHY_LOOSE = "Text stojí vedle blokových prvků, editor ho jako blok nenabídne."
WHY_SVG = "Text v SVG editor neukazuje."
WHY_ATTR = "Atribut %s editor neukazuje."
WHY_DATA_ATTR = "Text pro skript webu v atributu %s editor neukazuje."
WHY_META = "Pole %s panel SEO neukazuje."
WHY_DERIVED = "Generátor ho odvozuje z %s."
WHY_LD = "Text v JSON-LD panel SEO neukazuje."
WHY_ENTITY = "Údaje o vás: mění se v panelu SEO (oddíl O vás), propíše se na všechny stránky, které je nesou."
WHY_ENTITY_DATA = "Údaje o vás mimo texty (kontakty, odkazy), mění je Claude Code v datech webu."
WHY_LD_LINKED = "Mění se s textem bloku na stránce."
WHY_DATA = "Data pro skript webu editor neukazuje."
WHY_JS = "Text ve skriptu editor neukazuje."
REPLACE = " Mění se přes Najít a nahradit."
WHY_PART = "Sdílené menu nebo patička: změna se propíše na všechny stránky, které je nesou."
WHY_GEN_ATTR = ("Na generované stránce je text v generátoru stránek, tady je jen ke čtení. "
                "Změní ho Claude Code ve vlně.")

# Texty mimo bloky (panel Další texty): ATTR_TEXT je jméno pro obsah textu v SVG
ATTR_TEXT = "#text"
SVG_TEXT_TAGS = ("text", "tspan")
ATTR_LABELS = {"alt": "Popisek obrázku", "title": "Nápověda po najetí myší", "aria-label": "Popisek pro čtečku obrazovky",
               "placeholder": "Zástupný text pole", ATTR_TEXT: "Text v grafu"}

FAQ_FROM = B.LOCK_FAQ_FROM.split("%s")[0]

_SKIP = {"script", "style", "noscript", "template"}     # obsah není text stránky (title je v _head)
_NONTEXT = re.compile(r"<!--.*?-->|<![^>]*>|<\?[^>]*>|</[A-Za-z][^>]*>", re.S)
_CZ = re.compile("[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")
_WORD = re.compile(r"[^\W\d_]{2,}")
_URLISH = re.compile(r"^(?:https?:|mailto:|tel:|/|#|assets/|data:)")
_DATE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:T[\d:+.Z-]*)?$")
_SCRIPT_TYPE = re.compile(r"^(?:|text/javascript|module|application/javascript)$", re.I)


def norm(s):
    """Text úseku: entity dekódované, NBSP jako mezera, bez word joinerů, bílé znaky sloučené."""
    s = html.unescape(s).replace(" ", " ").replace("⁠", "")
    return re.sub(r"\s+", " ", s).strip()


def has_word(s):
    """Má text aspoň jedno slovo (dvě písmena)? Bez něj atribut není text (dekorativní alt="")."""
    return bool(_WORD.search(s))


def is_text(s):
    """Je řetězec text pro člověka (ne třída, klíč, adresa, ID)? Čeština, nebo věta s velkým
    písmenem a aspoň dvěma slovy s malými písmeny."""
    s = re.sub(r"<[^>]*>", " ", s)
    if not _WORD.search(s) or _URLISH.match(s.strip()):
        return False
    if _CZ.search(s):
        return True
    return (bool(re.match(r"\s*[A-Z]", s)) and " " in s.strip() and len(re.findall(r"[a-z]{2,}", s)) >= 2
            and not re.search(r"[{};=<>]", s))


class Item:
    __slots__ = ("kind", "where", "source", "detail", "state", "why", "text", "at")

    def __init__(self, kind, where, source, detail, state, why, text, at):
        self.kind, self.where, self.source, self.detail = kind, where, source, detail
        self.state, self.why, self.text, self.at = state, why, text, at

    def __repr__(self):
        return "Item(%s %s %s/%s %s %r)" % (self.kind, self.where or "", self.source, self.detail, self.state,
                                            self.text[:40])


class Page:
    """Stránka, jak ji vidí editor: lock = zámek celé stránky, prov = proveniance bloků a seo =
    proveniance polí SEO generované stránky, seo_lock = zámek panelu SEO, root = repo, shared =
    sdílené soubory generátoru (text z nich má podkategorii podle jména souboru), entity = oddíl
    O vás panelu SEO (seo.entity_panel, údaje o autorovi z dat webu)."""
    __slots__ = ("slug", "kind", "src", "root", "lock", "prov", "seo", "seo_lock", "shared", "entity")

    def __init__(self, slug, kind, src, root=None, lock=None, prov=None, seo=None, seo_lock=None, shared=(),
                 entity=None):
        self.slug, self.kind, self.src, self.root = slug, kind, src, root
        self.lock, self.prov, self.seo, self.seo_lock = lock, prov, seo, seo_lock
        self.shared = tuple(shared)
        self.entity = entity


def page(editor, slug, unlock=False):
    """Page přes apply.Editor pro aktuálního uživatele (stejně jako editační pohled a panel SEO);
    unlock = owner stránka odemčená, jak ji vidí admin (report)."""
    kind = editor.kind(slug)
    if unlock and kind == "owner":
        editor.unlock(slug)
    src = editor.read_page(slug)
    lock = editor.lock_reason(slug)
    gen = kind == "generated"
    prov = editor.provenance(slug, src) if gen and not lock else None
    sp = editor.seo_prov(slug, src) if gen else None
    return Page(slug, kind, src, editor.root, lock, prov, sp, editor.seo_lock(slug, sp), editor.site.shared_files(),
                editor.entity_panel(slug, src))


# ── bloky (editační pohled) ──
def blocks(src, lock=None, prov=None, root=None, part_lock=None):
    """(bloky se zámkem, {n: atributy editovatelného bloku generované stránky}). Sdílený blok menu
    a patičky (Block.part) zámek stránky ani proveniance nezamyká, mění se v souboru šablony;
    part_lock = důvod, proč ho uživatel měnit nesmí (role)."""
    bl = B.blocks(src, root)
    extra = {}
    for b in bl:
        if b.part:
            b.lock = b.lock or part_lock
            continue
        if lock:
            b.lock = b.lock or lock
        if b.lock or prov is None:
            continue
        bp = prov.blocks.get(b.n)
        if bp is None or bp.unmapped:
            b.lock = PV.LOCK_UNMAPPED
        elif bp.injected():
            b.lock = PV.LOCK_INJECTED
        else:
            extra[b.n] = prov.attrs(b.n)
    return bl, extra


# ── zdroj textu ──
_TPL = {}


def template_texts(root, files):
    """Texty z literálů souborů šablon (cesty v repu, bez tagů a formátovacích polí): popisky šablon."""
    out = set()
    for rel in files:
        path = Path(root) / rel
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            continue
        if key not in _TPL:
            got = set()
            for lit in P.literals(path.read_text(encoding="utf-8")):
                for piece in re.split(r"<[^>]*>|%[sd]|\{[^{}]*\}", lit.value):
                    t = norm(piece)
                    if len(t) >= 2:
                        got.add(t)
            _TPL[key] = got
        out |= _TPL[key]
    return out


class _Ranges(list):
    """Oblasti, které plní build ((začátek, konec, důvod)), a úseky ze sdílených souborů (parts)."""
    def __init__(self, managed, parts):
        super().__init__(managed)
        self.parts = parts


def _region(path, managed, at):
    """Důvod zámku oblasti, ve které text leží (pořadí jako blocks.lock_of: vnější vyhrává)."""
    parts = getattr(managed, "parts", ())
    for n in path:
        if n.tag in B.LOCK_TAG and B.page_level(n) and not any(a <= n.s < z for a, z, _ in parts):
            return B.LOCK_TAG[n.tag]
        why = B.node_lock(n)
        if why:
            return why
        for a, z, why in managed:
            if a <= n.s < z:
                return why
    for a, z, why in managed:
        if a <= at < z:
            return why
    return None


def _source(page, path, why, text):
    """(zdroj, podkategorie) textu."""
    for n in path:
        if n.tag in ("header", "footer") and B.page_level(n):
            return "chrome", n.tag
    if why and why.startswith(FAQ_FROM):
        return "html", "faq-from"
    for attr, _, lock in AD.RULES.computed:
        if lock and not why and any(attr in n.attrs for n in path):
            why = lock
    reg = AD.RULES.region_source(why) if why else None
    if reg:
        slot, files = reg
        return ("python" if page.root and text in template_texts(page.root, files) else "json"), slot
    if page.kind == "generated":
        for rel in page.shared:
            if page.root and text in template_texts(page.root, (rel,)):
                return "python", Path(rel).stem
        return "python", AD.RULES.script_detail
    return "html", page.kind


# ── průchod stránkou ──
def _walk(src, root):
    """("text", pozice, text, předci) úseků mezi tagy a ("node", uzel, předci včetně uzlu) prvků."""
    out = []

    def gaps(a, z, path):
        pos = a
        raw = src[a:z]
        for m in list(_NONTEXT.finditer(raw)) + [None]:
            piece = raw[pos - a:m.start()] if m else raw[pos - a:]
            t = norm(piece)
            if t:
                out.append(("text", pos, t, path))
            if m:
                pos = a + m.end()

    def walk(n, path, end):
        pos = n.se
        for c in n.children:
            gaps(pos, c.s, path)
            cpath = path + (c,)
            out.append(("node", c, cpath))
            if c.tag not in _SKIP:
                walk(c, cpath, c.ie if c.closed else end)
            pos = c.e if c.closed else end
        gaps(pos, end, path)

    walk(root, (), len(src))
    return out


def _ld_strings(v, path=(), parent=None, key=None, holder=None, types=()):
    """(cesta, hodnota, rodičovský objekt, klíč, seznam s rodičem, @type předků) řetězců JSON-LD
    (rodič a seznam jako apply._walk, kvůli allowlistu svázaných kopií)."""
    if isinstance(v, dict):
        t = v.get("@type")
        types = types + ((t,) if isinstance(t, str) else ())
        for k, x in v.items():
            yield from _ld_strings(x, path + (k,), v, k, holder, types)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            if isinstance(x, (dict, list)):
                yield from _ld_strings(x, path + (i,), None, None, v, types)
            else:
                yield from _ld_strings(x, path + (i,), parent, key, v, types)
    elif isinstance(v, str):
        yield path, v, parent, key, holder, types


def _linked(parent, key, holder):
    """Allowlist apply._allowed: otázka FAQ a poslední položka drobečků jdou s textem bloku."""
    if key != "name" or not isinstance(parent, dict):
        return False
    t = parent.get("@type")
    return t == "Question" or (t == "ListItem" and isinstance(holder, list) and bool(holder) and holder[-1] is parent)


def _json_strings(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from _json_strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _json_strings(x)
    elif isinstance(v, str):
        yield v


# ── texty mimo bloky: atributy a text v SVG (panel Další texty) ──
class AttrNode:
    """Prvek s texty mimo bloky. i = pořadí na stránce (data-ke-a v editačním pohledu), names =
    atributy s textem v pořadí zdroje a ATTR_TEXT pro text v SVG, state = PANEL, SDILENY nebo
    ZAMCENY (report), lock = proč ho uživatel teď nezmění (None = může), part = sdílený soubor
    (menu, patička), ze kterého prvek build vkládá."""
    __slots__ = ("i", "node", "names", "state", "lock", "part")

    def __init__(self, i, node, names, state, lock, part):
        self.i, self.node, self.names, self.state, self.lock, self.part = i, node, names, state, lock, part

    def __repr__(self):
        return "AttrNode(%d <%s> %s %s)" % (self.i, self.node.tag, " ".join(self.names), self.state)


def attr_nodes(src, kind, lock=None, part_lock=None, root=None, part=None):
    """[AttrNode] v pořadí zdroje. Atributy alt, title, aria-label a placeholder s textem (prázdný
    dekorativní alt ne, citace ne) a text v SVG (<text> a <tspan> bez vnořených prvků). Zamčené jsou
    oblasti, které plní build, prvky zamčené adaptérem a menu a patička bez sdíleného souboru;
    sdílené (part) mění jen role admin (part_lock = proč ne), vlastní texty stránky zamyká zámek
    stránky (lock: owner bez odemčení) a na generované stránce generátor. part = src je celý
    sdílený soubor (šablona), všechny prvky jsou z něj."""
    root = root or B.parse(src)
    managed = _Ranges(B.managed_ranges(src), [(0, len(src), part)] if part else AD.RULES.partial_parts(src))
    out = []
    for ev in _walk(src, root):
        if ev[0] != "node":
            continue
        n, path = ev[1], ev[2]
        if n.tag in ("script", "style") or any(x.tag == "head" for x in path):
            continue
        names = [k for k, v in n.attrs.items() if k in TEXT_ATTRS and _WORD.search(v)]
        if (n.tag in SVG_TEXT_TAGS and n.closed and not n.children and any(x.tag == "svg" for x in path[:-1])
                and _WORD.search(norm(src[n.se:n.ie]))):
            names.append(ATTR_TEXT)
        if not names:
            continue
        why = _region(path, managed, n.s)
        rel = next((r for a, z, r in managed.parts if a <= n.s < z), None)
        if why:
            state, lk = ZAMCENY, why
        elif rel:
            state, lk = SDILENY, part_lock
        elif lock:
            state, lk = ZAMCENY, lock
        elif kind == "generated":
            state, lk = ZAMCENY, WHY_GEN_ATTR
        else:
            state, lk = PANEL, None
        out.append(AttrNode(len(out), n, names, state, lk, rel))
    return out


def attr_marks(nodes):
    """Vložení do začátečních tagů pro editační pohled (blocks.annotate more): data-ke-a = pořadí,
    data-ke-an = jména textů, data-ke-alock = proč text uživatel nezmění, data-ke-apart = sdílený soubor."""
    out = []
    for x in nodes:
        t = ' data-ke-a="%d" data-ke-an="%s"' % (x.i, " ".join(x.names))
        if x.lock:
            t += ' data-ke-alock="%s"' % B._attr(x.lock)
        if x.part:
            t += ' data-ke-apart="%s"' % B._attr(x.part)
        out.append((x.node.s + 1 + len(x.node.tag), t))
    return out


def _attr_state(x):
    """(state, why) položky reportu z AttrNode."""
    if x.state == ZAMCENY:
        return ZAMCENY, x.lock
    return x.state, WHY_PART if x.state == SDILENY else None


# ── klasifikace ──
def classify(page):
    """Všechny texty stránky jako [Item] v pořadí zdroje (text a atributy, pak hlavička, JSON-LD)."""
    src = page.src
    root = B.parse(src)
    bl, _ = blocks(src, page.lock, page.prov, root)
    bnode = {id(b.node): b for b in bl}
    managed = _Ranges(B.managed_ranges(src), AD.RULES.partial_parts(src))
    an = {id(x.node): x for x in attr_nodes(src, page.kind, page.lock, root=root)}
    repl = "" if page.kind == "generated" else REPLACE
    out = []
    plain_blocks = {}                                  # prostý text editovatelného bloku → blok

    def item(kind, where, path, text, at, state, why, source=None):
        source, detail = source or _source(page, path, _region(path, managed, at), text)
        out.append(Item(kind, where, source, detail, state, why, text, at))

    for b in bl:
        if not b.lock:
            plain_blocks.setdefault(H.plain(B.block_text(src, b)), b)

    for ev in _walk(src, root):
        if ev[0] == "node":
            n, path = ev[1], ev[2]
            if n.tag == "script":
                _script(page, n, path, managed, item)
                continue
            if any(x.tag == "head" for x in path):
                continue
            why = _region(path, managed, n.s)
            for k, v in n.attrs.items():
                if k in AD.RULES.quote_attrs and v.strip():
                    item("attr", k, path, norm(v), n.s, ZAMCENY, AD.RULES.quote_why)
                elif k in TEXT_ATTRS and _WORD.search(v):
                    item("attr", k, path, norm(v), n.s, *_attr_state(an[id(n)]))
                elif k.startswith("data-") and is_text(v):
                    item("attr", k, path, norm(v), n.s, ZAMCENY, why or WHY_DATA_ATTR % k)
            continue
        _, at, text, path = ev
        if any(x.tag == "head" for x in path) or any(B._ICON.search(x.cls()) for x in path):
            continue
        svg = any(x.tag == "svg" for x in path)
        kind = "svg" if svg else "text"
        why = _region(path, managed, at)
        blk = next((bnode[id(x)] for x in reversed(path) if id(x) in bnode), None)
        computed = next((w for attr, w, _ in AD.RULES.computed if any(attr in x.attrs for x in path)), None)
        svg_node = an.get(id(path[-1])) if svg and path else None
        if computed:
            item(kind, None, path, text, at, POCITANY, computed)
        elif svg_node is not None and ATTR_TEXT in svg_node.names:
            item(kind, None, path, text, at, *_attr_state(svg_node))
        elif svg:
            item(kind, None, path, text, at, ZAMCENY, why or WHY_SVG)
        elif blk is None:
            item(kind, None, path, text, at, ZAMCENY, why or WHY_LOOSE)
        elif blk.lock:
            item(kind, None, path, text, at, ZAMCENY, blk.lock)
        elif blk.part:
            item(kind, None, path, text, at, SDILENY, WHY_PART)
        else:
            bp = page.prov.blocks.get(blk.n) if page.prov else None
            if bp is not None and bp.pages:
                pages = sorted(bp.pages)
                item(kind, None, path, text, at, SDILENY, "Sdílený text: změní se i na %s."
                     % ", ".join("/" + p for p in pages[:5]) + (" …" if len(pages) > 5 else ""))
            else:
                item(kind, None, path, text, at, KLIK, None)

    gen = page.kind == "generated"
    pan = (S.panel(src, page.kind, page.seo, frozenset(plain_blocks), page.seo_lock)
           if not gen or page.seo is not None else {"fields": [], "ld": [], "faq": []})
    _head(page, pan, out, repl)
    _jsonld(page, pan, out, plain_blocks, repl)
    return out


def _script(page, n, path, managed, item):
    """Inline skript: řetězce s textem (js), JSON pro skripty webu (data). JSON-LD jde zvlášť."""
    typ = n.attrs.get("type", "")
    if "src" in n.attrs or not n.closed or typ == "application/ld+json":
        return
    body = page.src[n.se:n.ie]
    if typ == "application/json":
        try:
            data = json.loads(body)
        except ValueError:
            return
        why = _region(path, managed, n.s)
        for v in _json_strings(data):
            if is_text(v):
                item("data", n.attrs.get("class") or None, path, norm(re.sub(r"<[^>]*>", " ", v)), n.s,
                     ZAMCENY, why or WHY_DATA)
    elif _SCRIPT_TYPE.match(typ):
        for at, t in js_texts(body):
            item("js", "inline", path, t, n.se + at, ZAMCENY, WHY_JS, source=("js", "inline"))


def _head(page, pan, out, repl):
    src, gen = page.src, page.kind == "generated"
    source = ("python", "wave") if gen else ("html", page.kind)
    fields = {f["id"]: f for f in pan["fields"]}
    spans = S.head_spans(src)
    for fid, (a, z) in sorted(spans.items(), key=lambda x: x[1]):
        text = norm(src[a:z])
        f = fields.get(fid)
        if f is not None and f["edit"] and not page.seo_lock:
            state, why = PANEL, None
        elif f is not None:
            state, why = ZAMCENY, page.seo_lock or f["why"]
        elif gen and fid in S.LABEL:
            state, why = POCITANY, WHY_DERIVED % ("title" if fid.endswith("title") else "description")
        else:
            state, why = ZAMCENY, WHY_META % fid
        out.append(Item("head", fid, source[0], source[1], state, why, text, a))
    end = src.find("</head>")
    for m in re.finditer(r"<meta\s[^>]*>", src[:end if end != -1 else 0]):
        attrs = dict(re.findall(r'([a-zA-Z:-]+)="([^"]*)"', m.group(0)))
        key = attrs.get("name") or attrs.get("property")
        val = attrs.get("content", "")
        if not key or key in spans or key in HEAD_SKIP or key in S.LABEL:
            continue
        if not _WORD.search(val) or _URLISH.match(val) or P.IDENT.match(val) or re.match(r"^[\w:.-]+$", val):
            continue
        why = WHY_META % key + (repl if key in REPLACE_META else "")
        out.append(Item("head", key, source[0], source[1], ZAMCENY, why, norm(val), m.start()))


def _jsonld(page, pan, out, plain_blocks, repl):
    src, gen = page.src, page.kind == "generated"
    source = ("python", "wave") if gen else ("html", page.kind)
    faq = {}
    for i, (bi, path, _, _) in enumerate(S.faq_ld(src)):
        item = pan["faq"][i] if i < len(pan["faq"]) else {}
        faq[(bi, path + ("name",))] = item.get("q")
        faq[(bi, path + ("acceptedAnswer", "text"))] = item.get("a")
    rows = {S.ld_parse(r["id"]): r for o in pan.get("ld", []) for r in o["rows"]}
    ent = AD.RULES.entity
    efields = {f["id"][2:]: f for f in (page.entity or {}).get("fields", [])}
    etopics = (page.entity or {}).get("topics")
    for bi, m in enumerate(S.LD_RE.finditer(src)):
        try:
            tree = json.loads(m.group(2))
        except ValueError:
            continue
        is_entity = bool(ent and page.entity and isinstance(tree, dict) and tree.get("@type") == ent["type"])
        for path, v, parent, key, holder, types in _ld_strings(tree):
            if key is None or key.startswith("@") or key in LD_SKIP:
                continue
            if not _WORD.search(v) or _URLISH.match(v) or _DATE.match(v) or P.IDENT.match(v):
                continue
            text, where = norm(v), "%s.%s" % (types[-1] if types else "?", key)
            f = faq.get((bi, path), False)
            r = rows.get((bi, path)) or rows.get((bi, path[:-1]))
            if is_entity:
                dotted = ".".join(str(p) for p in path if isinstance(p, str))
                ef = etopics if etopics and dotted == ent.get("topics") else efields.get(dotted)
                if ef is None:
                    state, why = ZAMCENY, WHY_ENTITY_DATA
                elif ef["state"] == "locked":
                    state, why = ZAMCENY, ef["why"]
                else:
                    state, why = (SDILENY, WHY_ENTITY) if ef["state"] == "shared" else (PANEL, None)
                out.append(Item("jsonld", where, "json", "entity", state, why, text, m.start(2)))
                continue
            if f is not False:
                if f and f.get("edit") and not page.seo_lock:
                    state, why = PANEL, None
                else:
                    state, why = ZAMCENY, page.seo_lock or (f or {}).get("why") or WHY_LD
            elif AD.RULES.quote_types & set(types):
                out.append(Item("jsonld", where, "json", AD.RULES.quote_detail, ZAMCENY, AD.RULES.quote_why, text,
                                m.start(2)))
                continue
            elif text in plain_blocks and (_linked(parent, key, holder) if not gen else _ld_linked(page, plain_blocks[text])):
                state, why = POCITANY, WHY_LD_LINKED
            elif r is not None:
                state = PANEL if r["edit"] else POCITANY if r["state"] == "computed" else ZAMCENY
                why = None if r["edit"] else r["why"]
            else:
                state, why = ZAMCENY, WHY_LD + repl
            out.append(Item("jsonld", where, source[0], source[1], state, why, text, m.start(2)))


def _ld_linked(page, block):
    """Generovaná stránka: literál bloku jde i do JSON-LD, úprava bloku ho změní s ním."""
    bp = page.prov.blocks.get(block.n) if page.prov else None
    return bp is not None and "jsonld" in bp.also


# ── texty v JavaScriptu ──
def _strings(src):
    """(pozice, obsah) řetězcových literálů JS; komentáře a regulární výrazy přeskočí, výrazy
    v ${…} šablonových řetězců nahradí mezerou."""
    out, i, n, prev = [], 0, len(src), ""
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"`":
            j, buf = i + 1, []
            while j < n and src[j] != c and (c == "`" or src[j] != "\n"):
                if src[j] == "\\":
                    buf.append(src[j:j + 2])
                    j += 2
                elif c == "`" and src.startswith("${", j):
                    depth, j = 1, j + 2
                    while j < n and depth:
                        depth += {"{": 1, "}": -1}.get(src[j], 0)
                        j += 1
                    buf.append(" ")
                else:
                    buf.append(src[j])
                    j += 1
            out.append((i, "".join(buf)))
            i, prev = j + 1, c
            continue
        if c == "/" and prev and prev in "(,=:[!&|?{};+-*%<>~^":
            j, cls = i + 1, False
            while j < n and src[j] != "\n" and (cls or src[j] != "/"):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    cls = True
                elif src[j] == "]":
                    cls = False
                j += 1
            i, prev = j + 1, "/"
            continue
        if not c.isspace():
            prev = c
        i += 1
    return out


_JS_ESC = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.S)


def _unescape(m):
    e = m.group(1)
    if e[0] in "ux" and len(e) > 1:
        return chr(int(e.strip("ux{}"), 16))
    return {"n": "\n", "t": "\t", "r": "\r"}.get(e, e)


def js_texts(src):
    """[(pozice, text)] řetězců skriptu, které jsou text pro člověka (tagy vynechané)."""
    out = []
    for at, v in _strings(src):
        v = _JS_ESC.sub(_unescape, v)
        if is_text(v):
            t = norm(re.sub(r"<[^>]*>", " ", v))
            if t:
                out.append((at, t))
    return out
