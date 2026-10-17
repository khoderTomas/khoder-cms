# -*- coding: utf-8 -*-
"""Textové bloky stránky pro editor: strom HTML s offsety, bloky, pevné prvky, zámky.

Blok = nejhlubší prvek s vlastním textem, jehož potomci jsou jen inline prvky.
Prvek bez vlastního textu, který má jen inline potomky (skupina tlačítek,
<li><a>…</a></li>), se rozpadne na potomky. Uvnitř bloku má každý inline
potomek pořadové číslo k (atribut data-ke-t v editačním pohledu); pevné prvky
(ikony, obrázky, svg, prvky s atributem z adapter.RULES.atom_attrs, inline prvky bez
vlastního textu) se nedají přepsat ani smazat.

Offsety jsou indexy do dekódovaného řetězce souboru (čteno s newline="", takže
CRLF v index.html zůstává, P20).
"""
import hashlib
import re
from html.parser import HTMLParser

import adapter as AD

INLINE = {"a", "strong", "b", "em", "i", "span", "br", "small", "sup", "sub", "code",
          "mark", "abbr", "time", "s", "u", "wbr", "img", "svg"}
VOID = {"br", "img", "input", "meta", "link", "hr", "wbr", "source", "area", "base",
        "col", "embed", "param", "track"}
OPAQUE = {"svg"}                      # dovnitř se nechodí, celé je jeden pevný prvek
SKIP = {"head", "script", "style", "template", "noscript", "select", "textarea", "svg"}
ATOM_TAGS = {"img", "svg", "wbr", "input", "picture", "video", "iframe"}
_ICON = re.compile(r"material-symbols|\bmsym\b|\bchev\b")

# Důvody zámků (tooltip v editoru). Menu a patička jsou sdílené napříč stránkami: když je stránka
# nese ze sdíleného souboru (adapter.RULES.partial_parts), bloky v nich se upravují v tom souboru
# (Block.part, apply.save_part) a díry stránky jako ostatní text; bez sdíleného souboru jsou zamčené.
# Ostatní oblasti (plní je build z dat webu) a zámky podle id a třídy dodává adaptér webu
# (adapter.RULES). Hlavička a patička sekce (<header> v <section> apod.) k menu nepatří, viz page_level.
SECTIONING = {"article", "aside", "nav", "section"}
LOCK_TAG = {
    "header": "Menu je sdílené mezi stránkami. Mění se přes Najít a nahradit.",
    "footer": "Patička je sdílená mezi stránkami. Mění se přes Najít a nahradit.",
}
LOCK_FAQ_FROM = "Tyhle otázky přebírá build ze stránky /%s, mění se tam."


class Node:
    __slots__ = ("tag", "attrs", "raw_tag", "s", "se", "ie", "e", "children", "text",
                 "closed", "parent")

    def __init__(self, tag, attrs, raw_tag, s, se, parent):
        self.tag, self.attrs, self.raw_tag = tag, attrs, raw_tag
        self.s, self.se, self.ie, self.e = s, se, None, None
        self.children, self.text, self.closed, self.parent = [], False, False, parent

    def cls(self):
        return self.attrs.get("class") or ""


class _Tree(HTMLParser):
    def __init__(self, src):
        super().__init__(convert_charrefs=True)
        self.src = src
        self.lines = [0] + [m.end() for m in re.finditer("\n", src)]
        self.root = Node("#root", {}, "", 0, 0, None)
        self.root.closed = True
        self.stack = [self.root]

    def _off(self):
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def _open(self, tag, attrs, selfclose):
        s = self._off()
        raw = self.get_starttag_text()
        top = self.stack[-1]
        n = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, raw, s, s + len(raw), top)
        top.children.append(n)
        if tag in VOID or selfclose:
            n.ie, n.e, n.closed = n.se, n.se, True
        else:
            self.stack.append(n)

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, True)

    def handle_endtag(self, tag):
        if not any(n.tag == tag for n in self.stack[1:]):
            return
        o = self._off()
        while self.stack[-1].tag != tag:
            self.stack.pop()                       # neuzavřený prvek: closed zůstane False
        n = self.stack.pop()
        n.ie = o
        n.e = self.src.index(">", o) + 1
        n.closed = True

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].text = True


def parse(src):
    t = _Tree(src)
    t.feed(src)
    t.close()
    return t.root


def page_level(n):
    """<header> a <footer> mimo sekci, článek a navigaci patří celé stránce (menu, patička webu)."""
    p = n.parent
    while p is not None:
        if p.tag in SECTIONING:
            return False
        p = p.parent
    return True


def is_icon(n):
    return n.tag in ATOM_TAGS or bool(_ICON.search(n.cls())) or any(a in n.attrs for a in AD.RULES.atom_attrs)


def has_text(n):
    return n.text or any(has_text(c) for c in n.children if not is_icon(c) and c.tag not in OPAQUE)


def is_atom(n):
    """Pevný prvek uvnitř bloku: nejde přepsat ani smazat."""
    if n.tag == "br":
        return False
    return is_icon(n) or n.tag in OPAQUE or not has_text(n)


def inline_only(n):
    return all(c.tag in INLINE and (c.tag in OPAQUE or is_icon(c) or inline_only(c)) for c in n.children)


class Block:
    """part = sdílený soubor (cesta v repu), ze kterého blok build vkládá (menu, patička), jinak None.
    region = důvod zámku oblasti, kterou build plní z dat a jejíž rodinu adaptér odemkl
    (adapter.Rules.unlocked): blok není zamčený, upravuje se v datech podle proveniance (B8)."""
    __slots__ = ("n", "node", "lock", "elems", "part", "region")

    def __init__(self, n, node, lock, part=None, region=None):
        self.n, self.node, self.lock, self.part, self.region = n, node, lock, part, region
        self.elems = []                            # [(k, Node, atom)] v pořadí dokumentu

    def inner(self, src):
        return src[self.node.se:self.node.ie]

    def hash(self, src):
        return short_hash(self.inner(src))


def block_text(src, block, inner=None):
    """Normalizovaný text bloku bez pevných prvků (ikona v <summary> apod.).
    Rekonstrukce kopíruje pevné prvky jako původní bajty, takže jdou vyjmout i z nového inner."""
    inner = block.inner(src) if inner is None else inner
    for _, e, atom in block.elems:
        if atom:
            inner = inner.replace(src[e.s:e.e], " ", 1)
    return inner


def short_hash(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def managed_ranges(src):
    """[(začátek, konec, důvod)] oblastí, které plní build (adaptér webu), v pořadí přednosti."""
    return AD.RULES.regions(src)


def unlocked(why):
    """Je oblast s tímhle důvodem zámku v rodině, kterou adaptér odemkl pro úpravy přes data?"""
    R = AD.RULES
    return bool(R.unlocked) and R.region_family(why) in R.unlocked


def node_lock(n):
    """Důvod zámku prvku podle id nebo třídy (adaptér webu), nebo None."""
    R = AD.RULES
    if n.attrs.get("id") in R.lock_ids:
        return R.lock_ids[n.attrs["id"]]
    return next((R.lock_classes[c] for c in n.cls().split() if c in R.lock_classes), None)


def _elems(node, out):
    for c in node.children:
        out.append((len(out), c, is_atom(c)))
        if not is_atom(c):
            _elems(c, out)


def blocks(src, root=None, part=None):
    """Všechny textové bloky v <body> v pořadí dokumentu (editovatelné i zamčené).
    part = src je celý sdílený soubor (šablona menu nebo patičky, bez <body>): všechny bloky
    jsou z něj, hlavička a patička v něm zamčené nejsou."""
    root = root or parse(src)
    managed = managed_ranges(src)
    parts = [(0, len(src), part)] if part else AD.RULES.partial_parts(src)
    out = []

    def part_of(p):
        return next((rel for a, z, rel in parts if a <= p < z), None)

    def lock_of(n, inherited):
        if inherited:
            return inherited
        if n.tag in LOCK_TAG and page_level(n) and part_of(n.s) is None:
            return LOCK_TAG[n.tag]
        why = node_lock(n)
        if why:
            return why
        for a, b, why in managed:
            if a <= n.s < b:
                return None if unlocked(why) else why
        return None

    def region_of(p):
        why = next((why for a, b, why in managed if a <= p < b), None)
        return why if why is not None and unlocked(why) else None

    def walk(n, lock):
        for c in n.children:
            if c.tag in SKIP or is_icon(c):
                continue
            lk = lock_of(c, lock)
            if c.closed and c.tag not in VOID and inline_only(c) and has_text(c):
                if c.text:
                    b = Block(len(out), c, lk, part_of(c.s), None if lk else region_of(c.s))
                    _elems(c, b.elems)
                    out.append(b)
                    continue
            walk(c, lk)

    body = next((c for c in _iter(root) if c.tag == "body"), root)
    walk(body, None)
    return out


def _iter(n):
    for c in n.children:
        yield c
        yield from _iter(c)


def annotate(src, blist=None, extra=None, more=()):
    """Vloží do začátečních tagů data-ke / data-ke-lock / data-ke-t / data-ke-atom, sdílenému bloku
    data-ke-part (soubor, ze kterého je).
    extra = {n: další atributy bloku} (proveniance generované stránky: sdílení, soubor),
    more = [(pozice, text)] další vložení (texty v atributech, classify.attr_marks).
    Vrací nový řetězec; zdroj se nemění (anotace je jen pro editační pohled)."""
    blist = blist if blist is not None else blocks(src)
    extra = extra or {}
    ins = list(more)
    for b in blist:
        at = b.node.s + 1 + len(b.node.tag)
        part = ' data-ke-part="%s"' % _attr(b.part) if b.part else ""
        if b.lock:
            ins.append((at, ' data-ke-lock="%s"%s' % (_attr(b.lock), part)))
            continue
        ins.append((at, ' data-ke="%d" data-ke-h="%s"%s%s' % (b.n, b.hash(src), part, extra.get(b.n, ""))))
        for k, e, atom in b.elems:
            ins.append((e.s + 1 + len(e.tag), ' data-ke-t="%d"%s' % (k, ' data-ke-atom=""' if atom else "")))
    ins.sort(key=lambda x: x[0], reverse=True)
    out = src
    for at, text in ins:
        out = out[:at] + text + out[at:]
    return out


def _attr(s):
    return s.replace("&", "&amp;").replace('"', "&quot;")


def page_hash(src):
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:16]


def find_block(src, n, bhash, phash):
    """Blok n z aktuálního zdroje. Když se stránka mezitím změnila (jiný hash),
    dohledá blok podle hashe obsahu; shoda musí být přesně jedna."""
    blist = [b for b in blocks(src) if not b.lock]
    if page_hash(src) == phash:
        hit = [b for b in blist if b.n == n]
    else:
        hit = [b for b in blist if b.hash(src) == bhash]
    if len(hit) != 1 or hit[0].hash(src) != bhash:
        return None
    return hit[0]
