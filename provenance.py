# -*- coding: utf-8 -*-
"""Proveniance textů: který zdroj textu tvoří který úsek bloku stránky a kam všude se ten zdroj
ještě dostane (další stránky, JSON-LD, hlavička, atributy, zamčené oblasti).

Zdroje textu (B8):
  literály Pythonu   skript generované stránky a sdílené soubory generátoru (adapter.Site.page_sources,
                     pysource); literál nese HTML
  hodnoty z dat      řetězcové hodnoty datových souborů webu (adapter.Site.data_files, jsonsource);
                     hodnota bez tagů a entit je prostý text se značkovacími vzory adaptéru
                     (adapter.Rules.value_markup: atom jako pevný prvek, párové značky kolem textu odkazu),
                     hodnota s tagy nebo entitami se čte jako HTML
Kandidátní bloky: editovatelné bloky generovaných stránek (hledají se ve zdrojích stránky) a bloky oblastí,
které build plní z dat a jejichž rodinu adaptér odemkl (blocks.Block.region; hledají se v datových souborech).

Sonda celého webu (probe_site): na konec každého výskytu normalizovaného textu úseku kandidátního bloku
v kandidátních zdrojích se vloží značka jen ze znaků pro soukromé použití (U+E000, číslice z U+E040–U+E07F,
U+E001; číslice ani písmena by změnily typografii buildu, značka na začátku hodnoty pravidla pro začátek
textu), jeden zkušební build celého webu (vlny a render všech stránek, adapter.Site.build_site) ukáže, kde
značky dopadly. Výstup bez značek se musí rovnat stránce, bloky se pak párují podle pořadí n; stránka
s rozdílem jde do druhého kola jen se svými značkami, potom je neprověřená (PageProv.error, kandidátní bloky
zamčené). Výsledek stránky platí, dokud sedí její zdroj a soubory, ze kterých má texty (PageProv.key).

Zápis úpravy: úsek bloku i odpovídající rozsah zdroje se rozloží na jednotky (znak, bílé znaky, tag)
a SequenceMatcher najde změnu; nezměněné jednotky zůstanou původními bajty zdroje, vložený text přijde
ze stránky (do prostého textu dekódovaný z HTML, nové tagy tam nejdou). Pevné mezery a word joinery se do
zdroje zapisují jen tam, kde je zdroj měl sám; jinak je doplní build (adapter.RULES.adds_typo).
"""
import bisect
import collections
import hashlib
import html
import re
from difflib import SequenceMatcher
from pathlib import Path

import adapter as AD
import blocks as B
import htmltext as H
import jsonsource as J
import pysource as P

WS = H.WS
NBSP, WJ = chr(0xA0), chr(0x2060)
_UNIT = re.compile(r"<!--.*?-->|<[^>]*>|&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);|\r\n|.", re.S)
_TYPO = re.compile("%s|&nbsp;|%s|&#8288;" % (NBSP, WJ))
_HTMLISH = re.compile(r"<[A-Za-z/!][^>]*>|&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
M0, M1 = chr(0xE000), chr(0xE001)
_D0 = 0xE040                                   # číslice značky: U+E040 … U+E07F (základ 64)
_MARK = re.compile("%s([%s-%s]+)%s" % (M0, chr(_D0), chr(_D0 + 63), M1))
_LD = re.compile(r'<script type="application/ld\+json">.*?</script>', re.S)
SEP = chr(0)                                   # atom hodnoty z dat v normalizovaném textu: úsek přes něj nevede
JOIN = chr(1)                                  # mezi literály v rejstříku souboru
PLAIN_KINDS = ("jsonld", "head", "attr")

LOCK_UNMAPPED = "Tenhle text vzniká ve skriptu skládáním, editor ho ve zdroji nenašel. Upravit přes Clauda."
LOCK_INJECTED = "Text je sdílený s oblastí, kterou plní build z dat webu. Upravit přes Clauda."
UNVERIFIED = "Zkušební build se značkami dal jinou stránku, než je na disku (značka změnila výstup, nebo stránka neodpovídá zdrojům)."


class SpliceError(Exception):
    pass


# ── jednotky a normalizace ──
def units(raw):
    """[(klíč, start, konec)]: tag a komentář vcelku, entity dekódované, bílé znaky
    a NBSP jako jedna jednotka WS, word joiner bez klíče (přilepí se k další jednotce)."""
    out, pend = [], None
    for m in _UNIT.finditer(raw):
        t = m.group(0)
        a, z = m.span()
        if pend is not None:
            a, pend = pend, None
        if t.startswith("<"):
            key = ("t", t)
        else:
            ch = html.unescape(t) if t.startswith("&") else t
            if ch == WJ:
                pend = a
                continue
            if ch == NBSP or ch.isspace():
                if out and out[-1][0] == WS:
                    out[-1] = (WS, out[-1][1], z)
                    continue
                key = WS
            else:
                key = ch
        out.append((key, a, z))
    return out


def _markup(value):
    """[(začátek, konec, klíč)] značkovacích vzorů adaptéru v prostém textu, bez překryvů."""
    found = []
    for rx, kind in AD.RULES.value_markup:
        for m in rx.finditer(value):
            if kind == "atom":
                found.append((m.start(), m.end(), ("a", m.group(0))))
            else:                                  # wrap: skupina 1 = text uvnitř párových značek
                found.append((m.start(), m.start(1), ("t", value[m.start():m.start(1)])))
                found.append((m.end(1), m.end(), ("t", value[m.end(1):m.end()])))
    out, end = [], 0
    for a, z, key in sorted(found):
        if a >= end:
            out.append((a, z, key))
            end = z
    return out


def value_units(value):
    """Jednotky hodnoty z dat jako prostého textu (klíče jako units): znaky, bílé znaky a NBSP jako WS,
    word joiner bez klíče, párová značka vzoru jako ("t", text), atom vzoru jako ("a", text)."""
    out, pend, pos = [], None, 0

    def chars(a, z):
        nonlocal pend
        i = a
        while i < z:
            ch = value[i]
            s, i = i, i + (2 if value.startswith("\r\n", i) else 1)
            if pend is not None:
                s, pend = pend, None
            if ch == WJ:
                pend = s
                continue
            if ch == NBSP or ch.isspace():
                if out and out[-1][0] == WS:
                    out[-1] = (WS, out[-1][1], i)
                    continue
                out.append((WS, s, i))
            else:
                out.append((ch, s, i))

    for a, z, key in _markup(value):
        chars(pos, a)
        out.append((key, pend if pend is not None else a, z))
        pend, pos = None, z
    chars(pos, len(value))
    return out


def normalize(us):
    """Normalizovaný text jednotek (bez tagů, <br> jako mezera, atom jako SEP, bílé znaky sloučené,
    bez okrajů) a pro každý jeho znak index jednotky."""
    s, idx = [], []
    for i, (key, _, _) in enumerate(us):
        if key == WS or (isinstance(key, tuple) and key[1][:3].lower() == "<br"):
            if s and s[-1] != " ":
                s.append(" ")
                idx.append(i)
        elif isinstance(key, tuple):
            if key[0] == "a":
                s.append(SEP)
                idx.append(i)
            continue
        else:
            for ch in key:
                s.append(ch)
                idx.append(i)
    if s and s[-1] == " ":
        s.pop()
        idx.pop()
    return "".join(s), idx


def segment_raws(src, block):
    """Inner bloku rozdělený pevnými prvky na úseky (surový HTML)."""
    segs, cur = [], []
    for key, raw in H.orig_units(src, block):
        if isinstance(key, tuple) and key[0] == "a":
            segs.append("".join(cur))
            cur = []
        else:
            cur.append(raw)
    segs.append("".join(cur))
    return segs


def mappable(text):
    return sum(c.isalnum() for c in text) >= 2


def _keys(us):
    return [k for k, _, _ in us]


# ── zdroje textu: literály Pythonu a hodnoty datových souborů ──
class JLit:
    """Řetězcová hodnota datového souboru: cesta, rozsah tokenu (a, z, i s uvozovkami), hodnota."""
    __slots__ = ("path", "a", "z", "value")

    def __init__(self, path, a, z, value):
        self.path, self.a, self.z, self.value = path, a, z, value

    def __repr__(self):
        return "JLit(%s, %r)" % ("/".join(map(str, self.path)), self.value[:40])


def is_data(rel):
    """Datový soubor (JSON), ne zdroj generátoru."""
    return rel.endswith(".json")


def is_text(rel, value):
    """Hodnota z dat bez tagů a entit: prostý text (build ho escapuje), jinak HTML jako literál."""
    return is_data(rel) and not _HTMLISH.search(value)


def literals(rel, text):
    if is_data(rel):
        return [JLit(p, a, z, v) for p, a, z, v in J.strings(text)]
    return P.literals(text)


def lit_units(rel, value):
    return value_units(value) if is_text(rel, value) else units(value)


def write(rel, text, lit, value):
    """Zdroj s novou hodnotou literálu (ValueError, když ji neumí zapsat)."""
    if is_data(rel):
        return J.write_string(text, lit.path, value)
    return P.replace(text, lit, value)


def _mark_write(rel, text, lit, value):
    if is_data(rel):
        return text[:lit.a] + J.enc(value) + text[lit.z:]
    return P.replace(text, lit, value)


def candidate(rel, lit):
    """Smí se do literálu vložit značka (není to slug, klíč, třída, cesta ani poznámka v datech)?"""
    if P.IDENT.match(lit.value):
        return False
    return not (is_data(rel) and any(isinstance(k, str) and k[:1] in "_@" for k in lit.path))


class _Index:
    """Normalizovaný text kandidátních literálů jednoho souboru v jednom řetězci: úsek se hledá jedním find."""

    def __init__(self, rel, lits):
        self.cand, self.pos, self.li, parts, at = {}, [], [], [], 0
        for li, lit in enumerate(lits):
            if not candidate(rel, lit):
                continue
            us = lit_units(rel, lit.value)
            nv, idx = normalize(us)
            self.cand[li] = (nv, idx, us)
            self.pos.append(at)
            self.li.append(li)
            parts.append(nv)
            at += len(nv) + 1
        self.big = JOIN.join(parts)

    def find(self, text):
        """[(index literálu, začátek v normalizovaném textu)] výskytů textu."""
        out, k = [], self.big.find(text)
        while k != -1:
            i = bisect.bisect_right(self.pos, k) - 1
            out.append((self.li[i], k - self.pos[i]))
            k = self.big.find(text, k + 1)
        return out


# ── výsledek proveniance ──
class Seg:
    """Úsek bloku přiřazený k výskytu textu v literálu: file (cesta v repu), index literálu,
    normalizovaný rozsah (k, n) a kam ještě ten výskyt dopadl (page, kind, n)."""
    __slots__ = ("file", "li", "k", "n", "dest")

    def __init__(self, file, li, k, n, dest):
        self.file, self.li, self.k, self.n, self.dest = file, li, k, n, dest


class BlockProv:
    __slots__ = ("segs", "unmapped", "pages", "also", "files", "same_page")

    def __init__(self):
        self.segs, self.unmapped = [], False
        self.pages, self.also, self.files, self.same_page = set(), set(), set(), False

    def plain(self):
        """Text jde i do JSON-LD, hlavičky nebo atributu: zapisuje se bez typografie."""
        return bool(self.also & set(PLAIN_KINDS))

    def injected(self):
        return "locked" in self.also


class PageProv:
    """script = skript generované stránky (cesta v repu), u ruční None; blocks = {n: BlockProv}
    kandidátních bloků; srcs a lits = {cesta v repu: obsah, literály} zdrojů sondy; deps = soubory,
    na kterých výsledek stránky závisí; key = otisk stránky a deps; error = proč je stránka neprověřená."""
    __slots__ = ("slug", "script", "key", "blocks", "srcs", "lits", "error", "deps")

    def __init__(self, slug, script, key=None):
        self.slug, self.script, self.key = slug, script, key
        self.blocks, self.srcs, self.lits, self.error, self.deps = {}, {}, {}, None, set()

    @property
    def generated(self):
        return self.script is not None

    def attrs(self, n):
        """Atributy pro editační pohled (sdílení, soubor)."""
        bp = self.blocks.get(n)
        if not bp:
            return ""
        out = ' data-ke-file="%s"' % B._attr(",".join(sorted(bp.files)))
        pages = sorted(bp.pages)
        if pages:
            out += ' data-ke-shared="%d" data-ke-pages="%s"' % (len(pages), B._attr(" ".join("/" + p for p in pages)))
        if bp.plain():
            out += ' data-ke-plain=""'
        return out


class SiteProv:
    """Proveniance celého webu z jedné sondy: key = otisk zdrojů a stránek, pages = {slug: PageProv},
    error = zkušební build selhal (všechny kandidátní bloky zamčené)."""
    __slots__ = ("key", "pages", "error")

    def __init__(self, key):
        self.key, self.pages, self.error = key, {}, None


def read(root, rel):
    with open(Path(root) / rel, encoding="utf-8", newline="") as f:
        return f.read()


def sources(site, slug):
    """{cesta v repu: obsah} zdrojů generované stránky (skript stránky a sdílené soubory)."""
    return {rel: read(site.root, rel) for rel in site.page_sources(slug)}


def cache_key(src, srcs):
    h = hashlib.sha1(src.encode("utf-8"))
    for k in sorted(srcs):
        h.update(srcs[k].encode("utf-8"))
    return h.hexdigest()


def probe_files(site):
    """Zdroje sondy: soubory generátoru a datové soubory (cesty v repu)."""
    return list(dict.fromkeys(list(site.generator_files()) + list(site.data_files())))


def fingerprint(site):
    """Otisk všeho, na čem sonda celého webu závisí: zdroje a stránky manifestu."""
    h = hashlib.sha1()
    for rel in probe_files(site) + [site.page_rel(s) for s in sorted(site.registry())]:
        h.update(rel.encode("utf-8") + b"\0")
        try:
            h.update(read(site.root, rel).encode("utf-8"))
        except FileNotFoundError:
            h.update(b"\1")
    return h.hexdigest()


def page_key(site, slug, deps):
    """Otisk stránky a souborů deps v jejich aktuální podobě na disku."""
    return cache_key(read(site.root, site.page_rel(slug)), {rel: read(site.root, rel) for rel in deps})


def mark_id(n):
    s = ""
    while True:
        s = chr(_D0 + n % 64) + s
        n //= 64
        if not n:
            return s


def strip_marks(raw):
    """(výstup bez značek, [(pozice ve výstupu bez značek, id značky)])."""
    parts, at, n, last = [], [], 0, 0
    for m in _MARK.finditer(raw):
        parts.append(raw[last:m.start()])
        n += m.start() - last
        at.append((n, m.group(1)))
        last = m.end()
    parts.append(raw[last:])
    return "".join(parts), at


def _mark_pos(us, j):
    """Kam za výskyt končící jednotkou j patří značka: hned za ni, jen bílé znaky na konci hodnoty
    nebo před tagem se vezmou s sebou (jednopísmenné „u“ před pevným prvkem dostane pevnou mezeru
    i se značkou; před dalším textem ale značka mezi mezeru a text nesmí, „GA4 | Web“ by přestalo
    být oddělovačem titulku)."""
    if j + 1 < len(us) and us[j + 1][0] == WS and (j + 2 == len(us) or isinstance(us[j + 2][0], tuple)):
        j += 1
    return us[j][2]


def _marked(srcs, lits, mids):
    """Zdroje se značkami: {cesta: text} a množina značek, které se do literálu zapsat nepodařilo."""
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for (rel, li, pos), mid in mids.items():
        by[rel][li].append((pos, mid))
    out, dropped = {}, set()
    for rel, per in by.items():
        s = srcs[rel]
        for li in sorted(per, key=lambda i: -lits[rel][i].a):
            lit = lits[rel][li]
            v = lit.value
            for pos, mid in sorted(per[li], reverse=True):
                v = v[:pos] + M0 + mid + M1 + v[pos:]
            try:
                s = _mark_write(rel, s, lit, v)
            except ValueError:
                dropped |= {mid for _, mid in per[li]}
        out[rel] = s
    return out, dropped


def _landing(st, at, page, land):
    """Kam na stránce page (výstup bez značek st) dopadly značky at: land[mid] += (page, druh, n)."""
    ld = [(m.start(), m.end()) for m in _LD.finditer(st)]
    body = st.find("<body")
    obl, starts = None, None
    for p, mid in at:
        n = None
        if any(a <= p < z for a, z in ld):
            kind = "jsonld"
        elif p < body:
            kind = "head"
        elif st.rfind("<", 0, p) > st.rfind(">", 0, p):
            kind = "attr"
        else:
            if obl is None:
                obl = B.blocks(st)
                starts = [b.node.se for b in obl]
            i = bisect.bisect_right(starts, p) - 1
            b = obl[i] if i >= 0 and p <= obl[i].node.ie else None
            if b is None:
                kind = "other"
            elif b.lock:
                kind = "locked"
            else:
                kind, n = "block", b.n
        land[mid].add((page, kind, n))


def probe_site(site, key=None):
    """Proveniance kandidátních bloků všech stránek z jedné sondy celého webu (site = adapter.Site)."""
    reg = site.registry()
    data = list(site.data_files())
    files = probe_files(site)
    srcs = {rel: read(site.root, rel) for rel in files}
    lits = {rel: literals(rel, srcs[rel]) for rel in files}
    index = {}
    pages = {slug: read(site.root, site.page_rel(slug)) for slug in reg}
    sp = SiteProv(key)
    gen_pages = {s for s, v in reg.items() if v["kind"] == "generated"}
    takes = {s: {x for _, _, x in site.rules.faq_from(pages[s])} for s in pages}

    def _reach(start):
        # značky jen v literálech generátoru: stačí generované stránky a stránky, které od nich obsah přebírají
        out, grow = set(start), True
        while grow:
            more = {s for s, t in takes.items() if s not in out and t & out}
            out |= more
            grow = bool(more)
        return sorted(out)

    cands = []                                   # (slug, blok)
    occ = {}                                     # (slug, n, si) → [(soubor, literál, k, konec výskytu v hodnotě)]
    for slug in sorted(reg):
        gen = reg[slug]["kind"] == "generated"
        pv = PageProv(slug, reg[slug]["source"] if gen else None)
        pv.srcs, pv.lits = srcs, lits
        pv.deps = set(site.page_sources(slug)) if gen else set()
        sp.pages[slug] = pv
        src = pages[slug]
        for b in B.blocks(src):
            if b.lock or b.part:
                continue
            if b.region:
                fk = data
            elif gen:
                fk = site.page_sources(slug)
            else:
                continue
            bp = BlockProv()
            pv.blocks[b.n] = bp
            cands.append((slug, b))
            for si, raw in enumerate(segment_raws(src, b)):
                text, _ = normalize(units(raw))
                if not mappable(text):
                    bp.segs.append(None)
                    continue
                bp.segs.append(text)
                hits = []
                for rel in fk:
                    ix = index.get(rel)
                    if ix is None:
                        ix = index[rel] = _Index(rel, lits[rel])
                    for li, k in ix.find(text):
                        nv, idx, us = ix.cand[li]
                        e = k + len(text)
                        if (k and nv[k - 1].isalnum() and text[0].isalnum() and idx[k] - idx[k - 1] == 1) or (
                                e < len(nv) and nv[e].isalnum() and text[-1].isalnum() and idx[e] - idx[e - 1] == 1):
                            continue               # uprostřed slova (bez tagu mezi): „Shoptet“ v „Shoptetu“ zdrojem není
                        hits.append((rel, li, k, _mark_pos(us, idx[e - 1])))
                occ[(slug, b.n, si)] = hits

    def run(keys):
        mids = {}
        for k in keys:
            for rel, li, _, pos in occ[k]:
                mids.setdefault((rel, li, pos), mark_id(len(mids)))
        overrides, dropped = _marked(srcs, lits, mids)
        only = None if any(is_data(rel) for rel in overrides) else _reach(gen_pages)
        ok, err, outputs = site.build_site(overrides, only)
        if not ok:
            return None, err
        land, diff = collections.defaultdict(set), set()
        for page, raw in outputs.items():
            st, at = strip_marks(raw)
            if st != pages.get(page):
                diff.add(page)
            if at:
                _landing(st, at, page, land)
        for mid in dropped:
            land.pop(mid, None)
        return (mids, land, diff), None

    result = {}
    if occ:
        res, err = run(list(occ))
        if res is None:
            sp.error = err or "zkušební build selhal"
        else:
            for k in occ:
                result[k] = res
            again = sorted({s for s, _ in cands} & res[2])
            if again:
                res2, err2 = run([k for k in occ if k[0] in again])
                for slug in again:
                    if res2 is None or slug in res2[2]:
                        sp.pages[slug].error = UNVERIFIED if res2 is not None else (err2 or "zkušební build selhal")
                    else:
                        for k in occ:
                            if k[0] == slug:
                                result[k] = res2

    for slug, b in cands:
        pv, bp = sp.pages[slug], sp.pages[slug].blocks[b.n]
        if sp.error or pv.error:
            pv.error = pv.error or sp.error
            bp.unmapped = True
            bp.segs = [None] * len(bp.segs)
            continue
        mine = (slug, "block", b.n)
        segs = []
        for si, text in enumerate(bp.segs):
            if text is None:
                segs.append(None)
                continue
            mids, land, _ = result[(slug, b.n, si)]
            good = []
            for rel, li, k, pos in occ[(slug, b.n, si)]:
                mid = mids.get((rel, li, pos))
                if mid is not None and mine in land.get(mid, ()):
                    good.append((rel, li, k, mid))
            if len(good) != 1:
                bp.unmapped = True
                segs.append(None)
                continue
            rel, li, k, mid = good[0]
            other = land[mid] - {mine}
            segs.append(Seg(rel, li, k, len(text), other))
            bp.files.add(rel)
            pv.deps.add(rel)
            bp.pages |= {d[0] for d in other if d[0] != slug}
            bp.also |= {d[1] for d in other if d[1] in PLAIN_KINDS + ("locked",)}
            bp.same_page = bp.same_page or any(d[0] == slug for d in other)
        bp.segs = segs
    for slug, pv in sp.pages.items():
        pv.key = cache_key(pages[slug], {rel: srcs[rel] for rel in pv.deps})
    return sp


# ── zápis úseku do literálu ──
def strip_typo(s):
    return s.replace("&nbsp;", " ").replace(NBSP, " ").replace("&#8288;", "").replace(WJ, "")


def _generic(k):
    """Klíč jednotky pro srovnání hodnoty z dat se stránkou: značka vzoru odpovídá libovolnému tagu."""
    return ("t",) if isinstance(k, tuple) and k[0] == "t" else k


def splice(value, seg, old_raw, new_raw, strip, text=False):
    """Nová hodnota literálu: v rozsahu úseku (seg.k, seg.n) se změní jen to, co se
    změnilo mezi old_raw a new_raw (úsek bloku na stránce před a po úpravě).
    text = hodnota je prostý text z dat (is_text): vložený text se dekóduje z HTML, tagy ne."""
    lu = value_units(value) if text else units(value)
    key = _generic if text else (lambda k: k)
    nv, idx = normalize(lu)
    t0, t1 = idx[seg.k], idx[seg.k + seg.n - 1]
    ou, nu = units(old_raw), units(new_raw)
    a = next((i for i, (k, _, _) in enumerate(ou) if not isinstance(k, tuple) and k != WS), len(ou))
    b = next((i for i, (k, _, _) in enumerate(reversed(ou)) if not isinstance(k, tuple) and k != WS), 0)
    if [key(k) for k in _keys(lu[t0:t1 + 1])] != [key(k) for k in _keys(ou[a:len(ou) - b])]:
        raise SpliceError("Úsek se ve zdroji liší od stránky, editor ho neumí bezpečně přepsat.")
    lead = 0
    while lead < a and t0 - lead - 1 >= 0 and key(lu[t0 - lead - 1][0]) == key(ou[a - lead - 1][0]):
        lead += 1
    trail = 0
    while trail < b and t1 + trail + 1 < len(lu) and key(lu[t1 + trail + 1][0]) == key(ou[len(ou) - b + trail][0]):
        trail += 1
    s, e = t0 - lead, t1 + 1 + trail
    oa, oz = a - lead, len(ou) - b + trail
    na, nz = oa, len(nu) - (len(ou) - oz)
    if nz < na or _keys(nu[:na]) != _keys(ou[:oa]) or _keys(nu[nz:]) != _keys(ou[oz:]):
        raise SpliceError("Formátování na okraji úseku pochází ze šablony, tady se změnit nedá.")
    lc, oc, nc = lu[s:e], ou[oa:oz], nu[na:nz]
    out = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, _keys(oc), _keys(nc), autojunk=False).get_opcodes():
        if op == "equal":
            out.append(value[lc[i1][1]:lc[i2 - 1][2]])
        elif op in ("replace", "insert"):
            piece = new_raw[nc[j1][1]:nc[j2 - 1][2]]
            if text:
                if any(isinstance(k, tuple) and k[0] == "t" for k in _keys(nc[j1:j2])):
                    raise SpliceError("Tenhle text je v datech webu prostý: formátování (tučné, odkaz) se do něj neuloží.")
                piece = re.sub(r"[ \t\r\n]+", " ", html.unescape(piece))
            out.append(strip_typo(piece) if strip else piece)
    start = lc[0][1] if lc else lu[t0][1]
    end = lc[-1][2] if lc else lu[t1][2]
    return value[:start] + "".join(out) + value[end:]


def file_adds_typo(src):
    return AD.RULES.adds_typo(src)


def needs_strip(pv, seg, old_raw, bp):
    """Pevné mezery do zdroje nepatří, když je doplňuje build (skript, který typografii doplňuje sám,
    hodnota z dat bez vlastních pevných mezer, stránka je má a zdroj ne) nebo když text jde i do
    JSON-LD/hlavičky (prostý text)."""
    if bp.plain() or (not is_data(seg.file) and file_adds_typo(pv.srcs[seg.file])):
        return True
    lit = pv.lits[seg.file][seg.li]
    if is_text(seg.file, lit.value):
        # prostý text z dat sází build celý: rozhoduje celá hodnota, ne úsek („s fakturou“ napsané
        # do úseku bez jednopísmenného slova by jinak šlo do dat s pevnou mezerou)
        return not _TYPO.search(lit.value)
    lu = lit_units(seg.file, lit.value)
    _, idx = normalize(lu)
    rng = lit.value[lu[idx[seg.k]][1]:lu[idx[seg.k + seg.n - 1]][2]]
    return bool(_TYPO.search(old_raw)) and not _TYPO.search(rng)
