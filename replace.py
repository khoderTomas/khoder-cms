# -*- coding: utf-8 -*-
"""Najít a nahradit napříč webem (panel editoru textů).

Hledá ve zdrojích textů: ruční a owner stránky (včetně menu, patičky a formuláře),
sdílené části stránek (adapter.Site.partial_files, třeba hlavička a patička) a zdroje
generátoru (adapter.Site.generator_files). Generované stránky se nepíšou přímo,
vzniknou zkušebním buildem.

Sdílená část (partial) je úryvek HTML, který build vkládá do stránek z
adapter.Site.partial_pages. Ve stránce se úseky z ní (adapter.RULES.partial_spans)
přeskočí, výskyt se najde jednou v souboru části a nahrazení přestaví všechny její
stránky (sestavení pod adapter.Site.overlay, generované zkušebním buildem).

Kde v HTML se hledá (druh výskytu):
  text    textový uzel v <body> mimo skripty a styly; nahrazený uzel dostane
          typografii webu (adapter.RULES.typo) jako úprava bloku
  title   <title>
  attr    alt, title, aria-label, placeholder
  meta    content u description, keywords a og/twitter title a description
  jsonld  hodnoty řetězců JSON-LD (ne klíče, @type/@id, adresy a identifikátory)
Mimo text jde vždy prostý text bez pevných mezer. Tagy a ostatní atributy (class,
href…), skripty a komentáře se neprohledávají. Oblasti, které plní build z dat webu
(adapter.RULES), jsou zamčené.

Mezera v dotazu najde i &nbsp;, U+00A0 a zalomení řádku s odsazením, entity se
porovnávají dekódované, word joiner je neviditelný. Výskyt nepřekročí tag.

V .py se hledá v hodnotách literálů (pysource) a nahrazuje se v nich. Kam výskyt
dopadne, ukáže zkušební build se značkou (jako proveniance): nahradit jde jen
výskyt, který na všech stránkách skončí v textu, title, meta, JSON-LD nebo
textovém atributu. Značka v class, href nebo skriptu = kód šablony, značka, která
se na web nedostane = nepoužitý text; ani jedno se nemění.

Nahrazení je transakce jako uložení bloku: nové zdroje, zkušební build dotčených
vln, injektory a verify, shrnutí k potvrzení (token) a jeden záznam v journalu
(kind=replace), takže Vrátit vrátí všechny soubory najednou.
"""
import bisect
import collections
import hashlib
import html
import json
import re
from pathlib import Path

import adapter as AD
import apply as A
import blocks as B
import htmltext as H
import provenance as PV
import publish as PB
import pysource as P
from journal import sha

TEXT_ATTRS = {"alt", "title", "aria-label", "placeholder"}
META_TEXT = {"description", "keywords", "og:title", "og:description", "twitter:title", "twitter:description"}
PLAIN = {"title", "attr", "meta", "jsonld"}
OK_KINDS = PLAIN | {"text"}
WHERE = [("text", "text"), ("title", "title"), ("meta", "meta"), ("attr", "alt/aria-label"), ("jsonld", "JSON-LD")]
MIN_Q, MAX_Q = 2, 200
MARK_A, MARK_Z = chr(0xE000), chr(0xE001)
WJ, NBSP = chr(0x2060), chr(0xA0)

_MARK = re.compile(MARK_A + "([0-9a-f]+)" + MARK_Z)
_TOKEN = re.compile(r"<!--.*?-->"
                    r"|<(script|style)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>.*?</\1\s*>"
                    r"|<[a-zA-Z/!?](?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.S | re.I)
_NAME = re.compile(r"</?([a-zA-Z][\w:-]*)")
_ATTR = re.compile(r"\s([a-zA-Z_:][-\w:.]*)\s*=\s*\"([^\"]*)\"")
_JSTR = re.compile(r"\"((?:[^\"\\]|\\.)*)\"(\s*:)?")
_URLISH = re.compile(r"^(?:https?:|mailto:|tel:|/|#)")
_TAGS = re.compile(r"<[^>]*>")


# ── kde v HTML je text ──
def _regions(src, whole=False):
    """[(druh, a, z, klíč)] míst s textem v pořadí souboru (u jsonld rozsah uvnitř uvozovek).
    whole: úryvek bez <body> (sdílená část stránky), text je všechno mimo tagy."""
    out = []
    body = -1 if whole else src.find("<body")
    in_title = False
    last = 0

    def text(a, z):
        if a < z:
            if in_title:
                out.append(("title", a, z, None))
            elif whole or (body != -1 and a > body):
                out.append(("text", a, z, None))

    for m in _TOKEN.finditer(src):
        text(last, m.start())
        last = m.end()
        t = m.group(0)
        if m.group(1):
            head = t[:t.index(">") + 1]
            if m.group(1).lower() == "script" and "application/ld+json" in head:
                out += _json_regions(src, m.start() + len(head), m.start() + t.lower().rindex("</script"))
            continue
        name = _NAME.match(t)
        if t.startswith("<!") or not name:
            continue
        tag = name.group(1).lower()
        if tag == "title":
            in_title = not t.startswith("</")
            continue
        if t.startswith("</"):
            continue
        attrs = list(_ATTR.finditer(t))
        if tag == "meta":
            d = {x.group(1).lower(): x.group(2) for x in attrs}
            c = next((x for x in attrs if x.group(1).lower() == "content"), None)
            if c and (d.get("name") or d.get("property")) in META_TEXT:
                out.append(("meta", m.start() + c.start(2), m.start() + c.end(2), None))
            continue
        for x in attrs:
            if x.group(1).lower() in TEXT_ATTRS:
                out.append(("attr", m.start() + x.start(2), m.start() + x.end(2), x.group(1).lower()))
    text(last, len(src))
    return out


def _json_regions(src, a, z):
    """Hodnoty řetězců JSON-LD mezi a a z; klíče, @type/@id, adresy a identifikátory ne."""
    out, key, key_end = [], None, -1
    for m in _JSTR.finditer(src, a, z):
        if m.group(2):
            key, key_end = m.group(1), m.end()
            continue
        own = key if not src[key_end:m.start()].strip() else None
        v = m.group(1)
        if (own or "").startswith("@") or _URLISH.match(v) or P.IDENT.match(v):
            continue
        out.append(("jsonld", m.start(1), m.end(1), own))
    return out


def _locked(src):
    """Rozsahy, které plní build nebo jsou zamčené (adapter.RULES): oblasti, prvky zamčené podle id
    a třídy, pevné prvky a další rozsahy webu (citace v JSON-LD)."""
    out = [(a, z) for a, z, _ in B.managed_ranges(src)]
    out += AD.RULES.locked_spans(src)
    atoms = AD.RULES.atom_attrs
    stack = [B.parse(src)]
    while stack:
        for c in stack.pop().children:
            if B.node_lock(c) or any(a in c.attrs for a in atoms):
                out.append((c.s, c.e if c.e is not None else len(src)))
            else:
                stack.append(c)
    return sorted(out)


_SPANS = collections.OrderedDict()


def spans(src):
    """(místa s textem, jejich začátky, zamčené rozsahy); cache podle obsahu."""
    key = hashlib.sha1(src.encode("utf-8")).hexdigest()
    hit = _SPANS.get(key)
    if hit is None:
        regs = _regions(src)
        hit = _SPANS[key] = (regs, [r[1] for r in regs], _locked(src))
        while len(_SPANS) > 200:
            _SPANS.popitem(last=False)
    return hit


def kind_at(sp, p):
    """Druh místa na pozici p: text/title/attr/meta/jsonld, locked, nebo code (všechno ostatní)."""
    regs, starts, locked = sp
    if any(a <= p < z for a, z in locked):
        return "locked"
    i = bisect.bisect_right(starts, p) - 1
    return regs[i][0] if i >= 0 and regs[i][1] <= p < regs[i][2] else "code"


# ── porovnání ──
def norm_query(q):
    return re.sub(r"\s+", " ", str(q or "").replace(WJ, ""))


def norm_rep(r):
    """Nahrazující text: bílé znaky jako jedna mezera, rovné uvozovky na „…” (P02)."""
    r = re.sub(r"\s+", " ", str(r or "").replace(WJ, ""))
    out, prev = [], ""
    for c in r:
        if c == '"':
            c = "„" if (not prev or prev in " ([/") else "”"
        out.append(c)
        prev = c
    return "".join(out)


def _flat(raw):
    """Text raw pro hledání: entity dekódované, bílé znaky a NBSP jako jedna mezera,
    tag jako \\x00 (výskyt ho nepřekročí); pro každý znak index jednotky."""
    us = PV.units(raw)
    chars, idx = [], []
    for i, (key, _, _) in enumerate(us):
        if key == PV.WS:
            key = " "
        elif isinstance(key, tuple):
            key = "\x00"
        for ch in key:
            chars.append(ch)
            idx.append(i)
    return "".join(chars), idx, us


def _needle(q):
    """Nejdelší úsek písmen a číslic dotazu: ve zdroji ho nerozdělí entita, NBSP ani word joiner."""
    return max(re.findall(r"[^\W_]+", q), key=len, default="")


def matches(raw, q, needle=None):
    """[(a, z)] rozsahy výskytů normalizovaného dotazu q v raw (bez překryvu)."""
    needle = _needle(q) if needle is None else needle
    if needle not in raw:
        return []
    text, idx, us = _flat(raw)
    out, k = [], text.find(q) if q else -1
    while k != -1:
        out.append((us[idx[k]][1], us[idx[k + len(q) - 1]][2]))
        k = text.find(q, k + len(q))
    return out


def _show(s):
    s = html.unescape(_TAGS.sub(" ", s)).replace(NBSP, " ").replace(WJ, "")
    return re.sub(r"\s+", " ", s)


def _sample(value, a, z, n=48):
    """[před, výskyt, po] pro náhled v panelu."""
    pre, mid, post = _show(value[:a]).lstrip(), _show(value[a:z]), _show(value[z:]).rstrip()
    if len(pre) > n:
        pre = "…" + pre[-n:].split(" ", 1)[-1]           # celé slovo na začátku ukázky
    if len(post) > n:
        post = post[:n].rsplit(" ", 1)[0] + "…"
    return [pre, mid, post]


# ── soubory ──
def _html_files(editor):
    return [(s, v["kind"]) for s, v in sorted(editor.registry().items()) if v["kind"] != "generated"]


def _py_files(editor):
    return editor.site.generator_files()


def _partial_files(editor):
    return editor.site.partial_files()


class _Files:
    """Obsah souborů repa pro jedno hledání nebo nahrazení: každý se čte jen jednou
    (čerstvé soubory v tempu antivir při každém otevření skenuje)."""

    def __init__(self, root):
        self.root, self.data = Path(root), {}

    def bytes(self, rel):
        if rel not in self.data:
            p = self.root / rel
            self.data[rel] = p.read_bytes() if p.exists() else None
        return self.data[rel]

    def text(self, rel):
        return self.bytes(rel).decode("utf-8")      # konce řádků beze změny, jako open(newline="")

    def sha(self, rel):
        return sha(self.bytes(rel))


def _file_sha(root, rel):
    p = Path(root) / rel
    return sha(p.read_bytes() if p.exists() else None)


def _lines(err, n=6):
    return [x for x in (err or "").strip().splitlines() if x.strip()][-n:]


# ── hledání ──
_FIND = collections.OrderedDict()


def find(editor, q):
    """Výsledek hledání pro panel: soubory s výskyty, co se nemění, sig pro nahrazení."""
    return _search(editor, q, _Files(editor.root))[0]


def _search(editor, q, fs):
    """(veřejný výsledek, výskyty pro nahrazení); cache podle dotazu a otisků souborů."""
    q = norm_query(q)
    if len(q.strip()) < MIN_Q:
        raise A.Fail("Hledaný text musí mít aspoň %d znaky." % MIN_Q, code="query", status=422)
    if len(q) > MAX_Q:
        raise A.Fail("Hledaný text je moc dlouhý, nejvíc %d znaků." % MAX_Q, code="query", status=422)
    rels = [editor.page_rel(s) for s, _ in _html_files(editor)] + _partial_files(editor) + _py_files(editor)
    state = sorted((rel, fs.sha(rel)) for rel in rels)
    sig = hashlib.sha1(json.dumps([q, state, sorted(editor.unlocked), A.actor()["role"]], ensure_ascii=False)
                       .encode("utf-8")).hexdigest()[:16]
    key = (str(editor.root), sig)
    if key not in _FIND:
        _FIND[key] = _find(editor, q, sig, fs)
        while len(_FIND) > 16:
            _FIND.popitem(last=False)
    return _FIND[key]


def _where(kinds):
    return [label for k, label in WHERE if k in kinds]


def _html_hits(src, regs, locked, q, needle, skipped, covered=()):
    """([(druh, a, z, [(va, vz)])], ukázka prvního výskytu) v HTML; výskyt v zamčeném rozsahu se
    počítá do skipped, v úseku sdílené části (covered) se přeskočí (najde se v jejím souboru)."""
    hits, first = [], None
    for kd, a, z, _ in regs:
        if needle not in src[a:z]:
            continue
        value = json.loads('"%s"' % src[a:z]) if kd == "jsonld" else src[a:z]
        found = []
        for va, vz in matches(value, q, needle):
            at = a + (0 if kd == "jsonld" else va)
            if any(ca <= at < cz for ca, cz in covered):
                continue
            if any(la <= at < lz for la, lz in locked):
                skipped["locked"] += 1
                continue
            found.append((va, vz))
        if found:
            hits.append((kd, a, z, found))
            first = first or _sample(value, *found[0])
    return hits, first


def _find(editor, q, sig, fs):
    files, internal, skipped = [], {}, collections.Counter()
    needle = _needle(q)
    for slug, kind in _html_files(editor):
        rel = editor.page_rel(slug)
        src = fs.text(rel)
        regs, _, locked = spans(src)
        covered = AD.RULES.partial_spans(src) if needle in src else []
        hits, first = _html_hits(src, regs, locked, q, needle, skipped, covered)
        if not hits:
            continue
        lock = None
        if kind == "owner" and slug not in editor.unlocked:
            lock = "Owner stránka (ADR 0003): nahradit v ní jde až po odemčení."
        files.append({"path": rel, "kind": kind, "slug": slug, "n": sum(len(h[3]) for h in hits), "lock": lock,
                      "pages": [slug], "where": _where({h[0] for h in hits}), "sample": first})
        internal[rel] = {"type": "html", "slug": slug, "hits": hits}

    reg = editor.registry()
    for rel in _partial_files(editor):
        src = fs.text(rel)
        if needle not in src:
            continue
        hits, first = _html_hits(src, _regions(src, whole=True), _locked(src), q, needle, skipped)
        if not hits:
            continue
        pages = editor.site.partial_pages(rel)
        # sdílená část mění i owner stránky: bez odemčení, potvrdí se v dialogu (ADR 0003 dodatek)
        owners = [p for p in pages if reg.get(p, {}).get("kind") == "owner"]
        lock = None if A.actor()["role"] == "admin" else A.LOCK_PART_ROLE   # menu a patičku mění jen admin
        files.append({"path": rel, "kind": "partial", "slug": None, "n": sum(len(h[3]) for h in hits), "lock": lock,
                      "owners": owners, "pages": pages, "where": _where({h[0] for h in hits}), "sample": first})
        internal[rel] = {"type": "partial", "hits": hits, "pages": pages}

    pyh = {}
    for rel in _py_files(editor):
        src = fs.text(rel)
        lits = P.literals(src)
        found = [(li, va, vz) for li, lit in enumerate(lits) if not P.IDENT.match(lit.value)
                 for va, vz in matches(lit.value, q, needle)]
        if found:
            pyh[rel] = (src, lits, found)
    error = None
    if pyh:
        land, error = _probe(editor.site, pyh)
        for rel, (src, lits, found) in pyh.items():
            ok, pages, kinds_all, first = [], set(), set(), None
            for li, va, vz in found:
                where = land.get((rel, li, va)) if land is not None else None
                if land is None:
                    skipped["error"] += 1
                elif where is None or any(k not in OK_KINDS and k != "locked" for _, k in where):
                    skipped["code"] += 1            # kód šablony (class, href, skript) nebo literál neumí značku
                elif not where:
                    skipped["unused"] += 1
                elif any(k == "locked" for _, k in where):
                    skipped["locked"] += 1
                else:
                    kinds = frozenset(k for _, k in where)
                    ok.append((li, va, vz, kinds))
                    pages |= {p for p, _ in where}
                    kinds_all |= kinds
                    first = first or _sample(lits[li].value, va, vz)
            if ok:
                files.append({"path": rel, "kind": "script", "slug": None, "n": len(ok), "lock": None,
                              "pages": sorted(pages), "where": _where(kinds_all), "sample": first})
                internal[rel] = {"type": "py", "hits": ok, "typo": PV.file_adds_typo(src)}
    files.sort(key=lambda f: f["path"])
    public = {"query": q, "sig": sig, "files": files, "total": sum(f["n"] for f in files),
              "skipped": dict(skipped), "error": error}
    return public, internal


def _probe(site, pyh):
    """Zkušební build se značkou na začátku každého výskytu v literálech. Vrací
    ({(soubor, literál, začátek): {(stránka, druh místa)}}, None), nebo (None, chyba)."""
    marks, overrides = {}, {}
    for rel, (src, lits, found) in pyh.items():
        by_lit = collections.defaultdict(list)
        for li, va, _ in found:
            by_lit[li].append(va)
        s = src
        for li in sorted(by_lit, key=lambda i: -lits[i].a):
            v, ids = lits[li].value, []
            for va in sorted(by_lit[li], reverse=True):
                mid = format(len(marks) + len(ids), "x")
                ids.append((mid, va))
                v = v[:va] + MARK_A + mid + MARK_Z + v[va:]
            try:
                s = P.replace(s, lits[li], v)
            except ValueError:
                continue                           # literál značku nepojme: výskyty zůstanou jako kód
            for mid, va in ids:
                marks[mid] = (rel, li, va)
        overrides[rel] = s
    ok, err, outputs = site.generate(overrides, site.scripts_for(overrides))
    if not ok:
        return None, "Výskyty ve skriptech nejde ověřit, zkušební build selhal: %s" % " ".join(_lines(err, 2))
    land = {m: set() for m in marks.values()}
    for page, raw in outputs.items():
        if MARK_A not in raw:
            continue
        parts, at, n, last = [], [], 0, 0
        for m in _MARK.finditer(raw):
            parts.append(raw[last:m.start()])
            n += m.start() - last
            at.append((n, m.group(1)))
            last = m.end()
        parts.append(raw[last:])
        sp = spans("".join(parts))
        for p, mid in at:
            if mid in marks:
                land[marks[mid]].add((page, kind_at(sp, p)))
    return land, None


# ── nahrazení ──
_PLANS = collections.OrderedDict()


def replace(editor, q, rep, files, sig, confirm=None, owners=False):
    """Nahradí výskyty ve vybraných souborech. Bez platného confirm vrací shrnutí
    zkušebního buildu s tokenem, s ním zapisuje (jeden záznam journalu). Mění-li vybraná sdílená
    část owner stránky, zapíše se jen s owners (zaškrtnutí v dialogu potvrzení)."""
    fs = _Files(editor.root)
    pub, internal = _search(editor, q, fs)
    if pub["sig"] != sig:
        raise A.Fail("Soubory se od hledání změnily. Hledání jsem spustil znovu.", code="stale", status=409)
    rep = norm_rep(rep)
    if "<" in rep or ">" in rep:
        raise A.Fail("Nahrazuje se prostým textem, bez znaků < a >.", code="markup", status=422)
    files = list(dict.fromkeys(files))
    by = {f["path"]: f for f in pub["files"]}
    if not files:
        raise A.Fail("Vyber aspoň jeden soubor.", code="empty", status=422)
    if any(p not in by for p in files):
        raise A.Fail("Ve vybraném souboru se hledaný text nenašel. Hledání jsem spustil znovu.", code="stale", status=409)
    locked = [by[p] for p in files if by[p]["lock"]]
    if locked:
        raise A.Fail("%s: %s" % (locked[0]["path"], locked[0]["lock"]), code="locked", status=403)
    if norm_query(rep) == pub["query"]:
        return {"ok": True, "unchanged": True}      # typografie by jinak „opravila“ dotčené uzly bez změny textu
    token = hashlib.sha1(json.dumps([sig, rep, sorted(files)], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    key = (str(editor.root), token)
    plan = _PLANS.get(key) or _plan(editor, pub, internal, files, rep, fs)
    _PLANS[key] = plan
    while len(_PLANS) > 4:
        _PLANS.popitem(last=False)
    if not plan["writes"]:
        return {"ok": True, "unchanged": True}
    own = sorted({p for f in files for p in by[f].get("owners") or ()})
    if confirm != token:
        return dict(plan["summary"], confirm=token, owners=own)
    if own and not owners:
        raise A.Fail("Nahrazení mění i owner stránky (%s), potvrď je v dialogu." % ", ".join("/" + p for p in own),
                     code="owners", status=403)

    with A.write_lock(editor.root):
        if any(_file_sha(editor.root, p) != h for p, h in plan["state"].items()):
            _PLANS.pop(key, None)
            raise A.Fail("Soubory se mezitím změnily. Hledání jsem spustil znovu.", code="stale", status=409)
        e = editor.journal.write(plan["writes"], slug=None, kind="replace", pages=plan["pages"], block=None,
                                 label="Najít a nahradit", before=pub["query"][:300], after=rep[:300],
                                 snippet=H.norm(rep)[:120])
    _PLANS.pop(key, None)
    return {"ok": True, "entry": e["id"], "pages": plan["pages"], "files": sorted(plan["writes"]),
            "hits": plan["summary"]["hits"]}


def _plan(editor, pub, internal, files, rep, fs):
    """Nové zdroje, zkušební build, injektory a verify. Nic nezapisuje."""
    parts = {}                                          # sdílené části: nový text
    for rel in (p for p in files if internal[p]["type"] == "partial"):
        new = _apply_html(fs.text(rel), internal[rel]["hits"], rep)
        if new != fs.text(rel):
            parts[rel] = new
    with editor.site.overlay(parts):
        return _plan_pages(editor, pub, internal, files, rep, fs, parts)


def _outside_parts(s):
    """Stránka bez úseků, které build vkládá ze sdílených částí (adapter.RULES.partial_spans)."""
    out, last = [], 0
    for a, z in sorted(AD.RULES.partial_spans(s)):
        out.append(s[last:a])
        last = z
    out.append(s[last:])
    return "".join(out)


def _plan_pages(editor, pub, internal, files, rep, fs, parts):
    """_plan pod overlay sdílených částí: sestavení i kontrola stránek je čtou s novým textem."""
    site = editor.site
    reg = editor.registry()
    by = {f["path"]: f for f in pub["files"]}
    writes, state, fails, notes, pages, gen = {}, {}, [], [], [], []
    plain = False
    for rel, text in parts.items():
        writes[rel] = text
        state[rel] = fs.sha(rel)
    reached = sorted({p for rel in parts for p in internal[rel]["pages"]})
    for rel in (p for p in files if internal[p]["type"] == "html"):
        it = internal[rel]
        src = fs.text(rel)
        state[rel] = fs.sha(rel)
        cand0 = _apply_html(src, it["hits"], rep)
        cand = editor.inject(cand0, it["slug"])
        if _outside_parts(cand) != _outside_parts(cand0):     # novou sdílenou část dosadí build, to není přepis
            notes.append("/%s: build část změny přepsal zpět (oblasti z dat webu se mění v datech)." % it["slug"])
        if cand == src:
            continue
        fails += editor.check(it["slug"], cand)
        writes[rel] = cand
        pages.append(it["slug"])
        plain = plain or any(h[0] in PLAIN for h in it["hits"])
    # stránky sdílené části bez vlastního výskytu (generované vzniknou zkušebním buildem níž)
    for slug in reached:
        if slug in pages or reg.get(slug, {}).get("kind") == "generated":
            continue
        prel = editor.page_rel(slug)
        cur = fs.text(prel)
        cand = editor.inject(cur, slug)
        if cand == cur:
            continue
        state[prel] = fs.sha(prel)
        fails += editor.check(slug, cand)
        writes[prel] = cand
        pages.append(slug)

    py = [p for p in files if internal[p]["type"] == "py"]
    gen_parts = {rel: t for rel, t in parts.items()
                 if any(reg.get(p, {}).get("kind") == "generated" for p in internal[rel]["pages"])}
    if py or gen_parts:
        texts = {}
        for rel in py:
            state[rel] = fs.sha(rel)
            src = fs.text(rel)
            new = _apply_py(src, internal[rel], rep)
            if new != src:
                texts[rel] = new
            plain = plain or any(h[3] & PLAIN for h in internal[rel]["hits"])
        expected = set().union(*(by[rel]["pages"] for rel in py + list(gen_parts)))
        scripts = None if gen_parts else site.scripts_for(texts)    # sdílená část mění všechny vlny
        ok, err, outputs = site.generate(dict(texts, **gen_parts), scripts)
        if not ok:
            raise A.Fail("Build se změněným textem selhal, nic jsem nezapsal.", code="verify", details=_lines(err))
        foreign = []
        for page in sorted(outputs):
            prel = editor.page_rel(page)
            cand = editor.inject(outputs[page], page)
            if cand == fs.text(prel):
                continue
            if page not in expected:
                foreign.append(page)
                continue
            fails += editor.check(page, cand)
            writes[prel] = cand
            state[prel] = fs.sha(prel)
            gen.append(page)
        if foreign:
            raise A.Fail("Zkušební build mění i stránky, kterých se nahrazení netýká (%s). %s/ neodpovídá zdrojům: "
                         "spusť %s a zkontroluj git diff."
                         % (", ".join("/" + p for p in foreign[:6]), site.source, site.build_hint()), code="drift", status=409)
        writes.update(texts)
    # stránky, které převzaly FAQ ze změněné stránky (adapter.Site.followers)
    changed = {s: writes[editor.page_rel(s)] for s in set(pages) | set(gen)}
    for page, (_, cand) in editor.faq_followers(changed, fails).items():
        prel = editor.page_rel(page)
        writes[prel] = cand
        state[prel] = fs.sha(prel)
        gen.append(page)
    if fails:
        raise A.Fail("Nahrazení by neprošlo kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)
    for rel, text in writes.items():
        if site.page_slug(rel) or rel in parts:
            old = fs.text(rel)
            for ent in ("&amp;nbsp;", "&amp;#8288;", "&amp;amp;"):
                if text.count(ent) > old.count(ent):
                    raise A.Fail("Po nahrazení by byl na stránce %s vidět kód entity %s, nic jsem nezapsal."
                                 % (rel, ent[5:]), code="verify", status=422)

    all_pages = sorted(set(pages) | set(gen))
    details = (["Mění se i title, meta nebo JSON-LD: tam jde prostý text bez pevných mezer."] if plain else []) + notes
    hits = sum(by[p]["n"] for p in files)
    summary = {"message": "Nahradit %s?" % PB.plural(hits, "výskyt", "výskyty", "výskytů"),
               "hits": hits, "files": sorted(files), "pages": all_pages,
               "generated": len(gen), "query": pub["query"], "replacement": rep,
               "examples": _examples(site, fs, writes), "details": details}
    return {"writes": {k: v.encode("utf-8") for k, v in writes.items()}, "state": state,
            "pages": all_pages, "summary": summary}


def _examples(site, fs, writes, limit=3, scan=8):
    """Pár různých ukázek skutečného výsledku (rozdíl po slovech) z prvních stránek."""
    out, seen = [], set()
    for rel in sorted(k for k in writes if site.page_slug(k))[:scan]:
        for it in PB.page_diff(fs.text(rel), writes[rel]):
            for d in it["diffs"]:
                key = json.dumps(d["ops"], ensure_ascii=False)
                if key not in seen and len(out) < limit:
                    seen.add(key)
                    out.append({"page": site.page_slug(rel), "label": d["label"] or it["tag"], "ops": d["ops"]})
    return out


def _splice(s, spans_, r):
    for a, z in sorted(spans_, reverse=True):
        s = s[:a] + r + s[z:]
    return s


def _apply_html(src, hits, rep):
    """Nahrazení v ruční stránce: text s typografií, jinde prostý text (atribut, JSON-LD)."""
    out, last = [], 0
    for kd, a, z, found in hits:
        if kd == "jsonld":
            new = H.ld_enc(_splice(json.loads('"%s"' % src[a:z]), found, rep))[1:-1]
        elif kd == "text":
            new = AD.RULES.typo(_splice(src[a:z], found, rep.replace("&", "&amp;")))
        elif kd == "title":
            new = _splice(src[a:z], found, rep.replace("&", "&amp;"))
        else:
            new = _splice(src[a:z], found, rep.replace("&", "&amp;").replace('"', "&quot;"))
        out += [src[last:a], new]
        last = z
    out.append(src[last:])
    return "".join(out)


def _enc_py(rep, kinds, adds_typo):
    """Hodnota do literálu: jde-li text i mimo <body> text, prostý; jinak HTML
    s typografií, kterou nedoplní build (skript, který typografii nedoplňuje sám)."""
    if kinds - {"text"}:
        return rep
    s = rep.replace("&", "&amp;")
    return s if adds_typo else AD.RULES.typo(s)


def _apply_py(src, it, rep):
    lits = P.literals(src)
    by = collections.defaultdict(list)
    for li, va, vz, kinds in it["hits"]:
        by[li].append((va, vz, _enc_py(rep, kinds, it["typo"])))
    s = src
    for li in sorted(by, key=lambda i: -lits[i].a):
        v = lits[li].value
        for va, vz, enc in sorted(by[li], reverse=True):
            v = v[:va] + enc + v[vz:]
        try:
            s = P.replace(s, lits[li], v)
        except ValueError as e:
            raise A.Fail("Do literálu na řádku %d se text nepodařilo zapsat (%s), nic jsem nezapsal."
                         % (lits[li].line, e), code="verify", status=422)
    return s
