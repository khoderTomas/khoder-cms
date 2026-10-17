# -*- coding: utf-8 -*-
"""Proveniance textů generovaných stránek: který literál ve skriptu stránky nebo ve sdíleném
souboru generátoru (adapter.Site.page_sources) tvoří který úsek bloku a kam všude se ten
literál ještě dostane (další stránky, JSON-LD, hlavička, atributy, zamčené oblasti).

Postup (spike B): do každého výskytu normalizovaného textu úseku v kandidátních
literálech se vloží značka, jeden zkušební build celého generátoru do tempu (~2 s)
ukáže, kde značky dopadly. Srovnává se jen normalizovaný text, protože značky
mění chování nb()/typo(). Výsledek se cachuje podle otisku zdrojů.

Zápis úpravy: úsek bloku i odpovídající rozsah literálu se rozloží na jednotky
(znak, bílé znaky, tag) a SequenceMatcher najde změnu; nezměněné jednotky
zůstanou původními bajty literálu, vložený text přijde ze stránky. Pevné
mezery a word joinery se do literálu zapisují jen tam, kde je zdroj měl sám;
jinak je doplní build (adapter.RULES.adds_typo).
"""
import collections
import hashlib
import html
import re
from difflib import SequenceMatcher
from pathlib import Path

import adapter as AD
import blocks as B
import htmltext as H
import pysource as P

WS = H.WS
_UNIT = re.compile(r"<!--.*?-->|<[^>]*>|&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);|\r\n|.", re.S)
_TYPO = re.compile("\u00a0|&nbsp;|\u2060|&#8288;")
_MARK = re.compile("\ue000([0-9a-z]+)\ue001")
_LD = re.compile(r'<script type="application/ld\+json">.*?</script>', re.S)
PLAIN_KINDS = ("jsonld", "head", "attr")

LOCK_UNMAPPED = "Tenhle text vzniká ve skriptu skládáním, editor ho ve zdroji nenašel. Upravit přes Clauda."
LOCK_INJECTED = "Text je sdílený s oblastí, kterou plní build z dat webu. Upravit přes Clauda."


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
            if ch == "\u2060":
                pend = a
                continue
            if ch == "\u00a0" or ch.isspace():
                if out and out[-1][0] == WS:
                    out[-1] = (WS, out[-1][1], z)
                    continue
                key = WS
            else:
                key = ch
        out.append((key, a, z))
    return out


def normalize(us):
    """Normalizovaný text jednotek (bez tagů, <br> jako mezera, bílé znaky sloučené,
    bez okrajů) a pro každý jeho znak index jednotky."""
    s, idx = [], []
    for i, (key, _, _) in enumerate(us):
        if key == WS or (isinstance(key, tuple) and key[1][:3].lower() == "<br"):
            if s and s[-1] != " ":
                s.append(" ")
                idx.append(i)
        elif isinstance(key, tuple):
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


# ── výsledek proveniance ──
class Seg:
    """Úsek bloku přiřazený k výskytu textu v literálu: file (cesta v repu), index literálu,
    normalizovaný rozsah (k, n) a kam ještě ten výskyt dopadl (page, kind, idx)."""
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
    """script = skript stránky (cesta v repu), srcs a lits = {cesta v repu: obsah, literály}."""
    __slots__ = ("slug", "script", "key", "blocks", "srcs", "lits", "error")

    def __init__(self, slug, script, key):
        self.slug, self.script, self.key = slug, script, key
        self.blocks, self.srcs, self.lits, self.error = {}, {}, {}, None

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


def probe(site, slug, src):
    """Proveniance všech editovatelných bloků stránky (jeden zkušební build), site = adapter.Site."""
    srcs = sources(site, slug)
    pv = PageProv(slug, site.registry()[slug]["source"], cache_key(src, srcs))
    pv.srcs = srcs
    pv.lits = {k: P.literals(s) for k, s in pv.srcs.items()}
    cand = {}
    for fk, ls in pv.lits.items():
        cand[fk] = []
        for lit in ls:
            if P.IDENT.match(lit.value):
                cand[fk].append(None)
                continue
            us = units(lit.value)
            cand[fk].append((normalize(us), us))

    ed = [b for b in B.blocks(src) if not b.lock]
    marks = {}                                  # mid → (n, si, file, li, k)
    ins = collections.defaultdict(list)         # (file, li) → [(pozice v hodnotě, mid)]
    for b in ed:
        bp = BlockProv()
        pv.blocks[b.n] = bp
        if b.part:
            continue                            # menu a patička: text je v šabloně, ne ve skriptu (apply.save_part)
        for si, raw in enumerate(segment_raws(src, b)):
            text, _ = normalize(units(raw))
            bp.segs.append(text if mappable(text) else None)
            if not mappable(text):
                continue
            for fk, cs in cand.items():
                for li, c in enumerate(cs):
                    if c is None:
                        continue
                    (nv, idx), us = c
                    k = nv.find(text)
                    while k != -1:
                        mid = format(len(marks), "x")
                        marks[mid] = (b.n, si, fk, li, k)
                        ins[(fk, li)].append((us[idx[k + len(text) - 1]][2], mid))
                        k = nv.find(text, k + 1)

    overrides = {}
    for fk, s in pv.srcs.items():
        for (fk2, li), plist in sorted(ins.items(), key=lambda x: -pv.lits[x[0][0]][x[0][1]].a):
            if fk2 != fk:
                continue
            lit = pv.lits[fk][li]
            v = lit.value
            for p, mid in sorted(plist, reverse=True):
                v = v[:p] + "\ue000" + mid + "\ue001" + v[p:]
            try:
                s = P.replace(s, lit, v)
            except ValueError:
                for _, mid in plist:
                    marks.pop(mid, None)
        overrides[fk] = s

    ok, err, outputs = site.generate(overrides)
    if not ok:
        pv.error = err
        for bp in pv.blocks.values():
            bp.unmapped = True
        return pv
    dest = collections.defaultdict(set)
    for page, raw in outputs.items():
        stripped, at, last = [], [], 0
        for m in _MARK.finditer(raw):
            stripped.append(raw[last:m.start()])
            at.append((sum(map(len, stripped)), m.group(1)))
            last = m.end()
        stripped.append(raw[last:])
        st = "".join(stripped)
        obl = B.blocks(st)
        # build v paměti (oblasti z dat webu) mění počet zamčených bloků,
        # takže se páruje pořadí mezi editovatelnými, ne absolutní n
        order = {id(b): i for i, b in enumerate(b for b in obl if not b.lock)}
        if page == slug and len(order) != len(ed):
            pv.error = "jiný počet bloků po zkušebním buildu (%d ≠ %d)" % (len(order), len(ed))
            for bp in pv.blocks.values():
                bp.unmapped = True
            return pv
        ld = [(m.start(), m.end()) for m in _LD.finditer(st)]
        body = st.find("<body")
        for p, mid in at:
            if any(a <= p < z for a, z in ld):
                kind, idx = "jsonld", None
            elif p < body:
                kind, idx = "head", None
            elif st.rfind("<", 0, p) > st.rfind(">", 0, p):
                kind, idx = "attr", None
            else:
                hit = [b for b in obl if b.node.se <= p <= b.node.ie]
                if not hit:
                    kind, idx = "other", None
                elif hit[-1].lock:
                    kind, idx = "locked", None
                else:
                    kind, idx = "block", order[id(hit[-1])]
            dest[mid].add((page, kind, idx))

    by_seg = collections.defaultdict(list)
    for mid, (n, si, fk, li, k) in marks.items():
        by_seg[(n, si)].append(mid)
    for ei, b in enumerate(ed):
        n, bp = b.n, pv.blocks[b.n]
        if b.part:
            continue
        segs = []
        for si, text in enumerate(bp.segs):
            if text is None:
                segs.append(None)
                continue
            mine = (slug, "block", ei)
            good = [mid for mid in by_seg.get((n, si), ()) if mine in dest.get(mid, ())]
            if len(good) != 1:
                bp.unmapped = True
                segs.append(None)
                continue
            _, _, fk, li, k = marks[good[0]]
            other = dest[good[0]] - {mine}
            segs.append(Seg(fk, li, k, len(text), other))
            bp.files.add(fk)
            bp.pages |= {d[0] for d in other if d[0] != slug}
            bp.also |= {d[1] for d in other if d[1] in PLAIN_KINDS + ("locked",)}
            bp.same_page = bp.same_page or any(d[0] == slug for d in other)
        bp.segs = segs
    return pv


# ── zápis úseku do literálu ──
def strip_typo(s):
    return s.replace("&nbsp;", " ").replace("\u00a0", " ").replace("&#8288;", "").replace("\u2060", "")


def splice(value, seg, old_raw, new_raw, strip):
    """Nová hodnota literálu: v rozsahu úseku (seg.k, seg.n) se změní jen to, co se
    změnilo mezi old_raw a new_raw (úsek bloku na stránce před a po úpravě)."""
    lu = units(value)
    nv, idx = normalize(lu)
    t0, t1 = idx[seg.k], idx[seg.k + seg.n - 1]
    ou, nu = units(old_raw), units(new_raw)
    a = next((i for i, (k, _, _) in enumerate(ou) if not isinstance(k, tuple) and k != WS), len(ou))
    b = next((i for i, (k, _, _) in enumerate(reversed(ou)) if not isinstance(k, tuple) and k != WS), 0)
    if _keys(lu[t0:t1 + 1]) != _keys(ou[a:len(ou) - b]):
        raise SpliceError("Úsek se ve zdroji liší od stránky, editor ho neumí bezpečně přepsat.")
    lead = 0
    while lead < a and t0 - lead - 1 >= 0 and lu[t0 - lead - 1][0] == ou[a - lead - 1][0]:
        lead += 1
    trail = 0
    while trail < b and t1 + trail + 1 < len(lu) and lu[t1 + trail + 1][0] == ou[len(ou) - b + trail][0]:
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
            out.append(strip_typo(piece) if strip else piece)
    start = lc[0][1] if lc else lu[t0][1]
    end = lc[-1][2] if lc else lu[t1][2]
    return value[:start] + "".join(out) + value[end:]


def file_adds_typo(src):
    return AD.RULES.adds_typo(src)


def needs_strip(pv, seg, old_raw, bp):
    """Pevné mezery do literálu nepatří, když je doplňuje build (skript, který typografii doplňuje sám,
    stránka je má a zdroj ne) nebo když text jde i do JSON-LD/hlavičky (prostý text)."""
    if bp.plain() or file_adds_typo(pv.srcs[seg.file]):
        return True
    lit = pv.lits[seg.file][seg.li]
    lu = units(lit.value)
    _, idx = normalize(lu)
    rng = lit.value[lu[idx[seg.k]][1]:lu[idx[seg.k + seg.n - 1]][2]]
    return bool(_TYPO.search(old_raw)) and not _TYPO.search(rng)
